"""Regression tests use temporary databases, never the user's workspace."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app
from app.store import Store, initial_state
from app.logic import approve, overdue, suggest, transaction_id


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(self.directory.name)
        self.client = TestClient(create_app(self.store))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.store.close()
        self.directory.cleanup()

    def state(self):
        return self.client.get('/api/state').json()

    def post(self, path, body=None):
        response = self.client.post('/api' + path, json=body or {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_transaction_ids_remain_stable(self):
        fixtures = json.loads(Path(__file__).with_name('express_matching_baseline.json').read_text())
        invoices = initial_state()['invoices']
        for fixture in fixtures:
            with self.subTest(narration=fixture['transaction']['narration']):
                self.assertEqual(transaction_id(fixture['transaction']), fixture['id'])

    def test_seed_and_customer_edit_persistence(self):
        self.assertEqual(len(self.state()['invoices']), 27)
        self.assertEqual(len(self.state()['customers']), 9)
        response = self.client.patch('/api/customers/C002', json={'email': 'test@example.org'})
        self.assertEqual(response.status_code, 200)
        second = Store(self.directory.name)
        self.assertEqual(second.get()['customers'][1]['contact_email'], 'test@example.org')
        second.close()
        self.assertEqual(self.client.patch('/api/customers/C002', json={'email': 'invalid'}).status_code, 400)

    def test_complete_conversation_and_payment_flow(self):
        self.post('/reminders')
        count = len(self.state()['conversations'])
        self.post('/reminders')
        self.assertEqual(len(self.state()['conversations']), count)
        thread = next(c for c in self.state()['conversations'] if c['invoice'] == 'INV-1004')
        path = '/conversations/' + thread['id']
        self.post(path + '/reply', {'message': 'Can I have more time?'})
        self.assertEqual(next(c for c in self.state()['conversations'] if c['id'] == thread['id'])['status'], 'attention')
        self.post(path + '/send', {'text': 'Accounts will review your request.'})
        self.post(path + '/reply', {'message': 'I paid yesterday'})
        self.assertEqual(next(i for i in self.state()['invoices'] if i['invoice_number'] == 'INV-1004')['balance_due'], 59000)
        self.post('/statements', {'sample': True})
        self.assertEqual(len(self.state()['transactions']), 14)
        self.assertTrue(self.post('/statements', {'sample': True})['message'].startswith('0 '))
        transaction = next(t for t in self.state()['transactions'] if t['suggestion']['invoice'] == 'INV-1004')
        review = '/transactions/' + transaction['id'] + '/review'
        body = {'action': 'approve', 'invoice': 'INV-1004', 'mode': 'partial'}
        self.post(review, body)
        self.assertEqual(self.client.post('/api' + review, json=body).status_code, 400)
        self.post(path + '/reply', {'message': 'What is our balance?'})
        answer = next(c for c in self.state()['conversations'] if c['id'] == thread['id'])['messages'][-1]['text']
        self.assertIn('INV-1005', answer)
        for excluded in ('INV-1004', 'INV-1006', 'INV-1002'):
            self.assertNotIn(excluded, answer)

    def test_partial_tds_and_rejection(self):
        self.post('/statements', {'sample': True})
        for amount, number, mode, expected_balance in [(200000, 'INV-1013', 'partial', 272000), (324000, 'INV-1007', 'tds', 0)]:
            transaction = next(t for t in self.state()['transactions'] if t['credit'] == amount)
            self.post('/transactions/' + transaction['id'] + '/review', {'action': 'approve', 'invoice': number, 'mode': mode})
            invoice = next(i for i in self.state()['invoices'] if i['invoice_number'] == number)
            self.assertEqual(invoice['balance_due'], expected_balance)
            if mode == 'tds':
                self.assertEqual(invoice['amount_paid'], 324000)
                self.assertEqual(invoice['tds_adjustment'], 30000)
                self.assertEqual(invoice['status'], 'paid')
        transaction = next(t for t in self.state()['transactions'] if t['status'] == 'pending')
        self.post('/transactions/' + transaction['id'] + '/review', {'action': 'reject'})
        self.assertEqual(next(t for t in self.state()['transactions'] if t['id'] == transaction['id'])['status'], 'rejected')

    def test_invalid_import_rolls_back(self):
        csv = ('txn_date,narration,reference,credit,debit,currency\n'
               '2026-09-05,KESTREL,abc,59000,,INR\n2026-99-99,BAD,def,1,,INR')
        response = self.client.post('/api/statements', json={'csv': csv})
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.assertEqual(self.state()['transactions'], [])

    def test_validation_and_reset(self):
        for path, body in [('/statements', {}), ('/conversations/missing/reply', {'message': ' '}), ('/transactions/missing/review', {'action': 'approve', 'mode': 'bad'})]:
            response = self.client.post('/api' + path, json=body)
            self.assertEqual(response.status_code, 400)
            self.assertIn('error', response.json())
        response = self.client.post('/api/statements', content=b'x' * (2 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 400)
        self.post('/statements', {'sample': True})
        self.post('/reset')
        self.assertEqual(self.state()['transactions'], [])
        self.assertEqual(self.state()['payments'], [])

    def test_ineligible_payments_do_not_mutate(self):
        state = initial_state()
        state['transactions'] = [dict(id='x', status='pending', credit=100, currency='USD', txn_date='2026-10-20')]
        with self.assertRaises(ValueError):
            approve(state, 'x', 'INV-1004', 'partial')
        state['transactions'][0]['currency'] = 'INR'
        with self.assertRaises(ValueError):
            approve(state, 'x', 'INV-1004', 'tds')
        self.assertEqual(state['invoices'][3]['balance_due'], 59000)
        self.assertFalse(overdue(dict(status='draft', balance_due=100, due_date='2020-01-01'), '2026-11-05'))

    def test_concurrent_approval_only_applies_once(self):
        self.post('/statements', {'sample': True})
        transaction = next(t for t in self.state()['transactions'] if t['suggestion']['invoice'] == 'INV-1004')
        def apply(_):
            try:
                self.store.mutate(lambda state: approve(state, transaction['id'], 'INV-1004', 'partial'))
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(apply, range(2))), 1)
        self.assertEqual(len(self.state()['payments']), 1)


class MigrationTests(unittest.TestCase):
    def test_legacy_nedb_preserved_and_migrated_once(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            original = initial_state()
            original['_id'] = 'demo'
            changed = json.loads(json.dumps(original))
            changed['customers'][0]['contact_email'] = 'preserved@example.org'
            path = Path(directory) / 'workspace.db'
            content = json.dumps(original) + '\n' + json.dumps(changed) + '\n'
            path.write_text(content, encoding='utf-8')
            store = Store(directory)
            self.assertEqual(store.get()['customers'][0]['contact_email'], 'preserved@example.org')
            self.assertEqual(path.read_text(encoding='utf-8'), content)
            store.mutate(lambda state: state['customers'][0].update(contact_email='new@example.org'))
            store.close()
            store = Store(directory)
            self.assertEqual(store.get()['customers'][0]['contact_email'], 'new@example.org')
            store.close()


if __name__ == '__main__':
    unittest.main()
