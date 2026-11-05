import unittest
from app.logic import suggest
from app.store import initial_state


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state()

    def match(self, narration='KESTREL FOODS', **changes):
        t = dict(narration=narration, reference='', credit=59000, currency='INR', txn_date='2026-11-05')
        t.update(changes)
        return suggest(t, self.state['invoices'], self.state['customers'])

    def test_equal_balances_are_ambiguous(self):
        r = self.match()
        self.assertIsNone(r['invoice'])
        self.assertIn('Ambiguous', r['reason'])
        self.assertEqual({c['invoice'] for c in r['candidates']}, {'INV-1004', 'INV-1005'})

    def test_reference_column_resolves_tie(self):
        self.assertEqual(self.match(reference='INV/1005')['invoice'], 'INV-1005')

    def test_date_resolves_tie(self):
        self.assertEqual(self.match(txn_date='2026-09-05')['invoice'], 'INV-1004')

    def test_reference_boundaries_unknown_and_conflict(self):
        for text in ('KESTREL FOODS INV10040', 'KESTREL FOODS INV1004 INV1005', 'BRIGHTLINE LOGISTICS INV1004'):
            self.assertIsNone(self.match(text)['invoice'])

    def test_currency_and_paid_reference_do_not_fall_back(self):
        self.assertIsNone(self.match('KESTREL FOODS INV1004', currency='USD')['invoice'])
        next(i for i in self.state['invoices'] if i['invoice_number']=='INV-1004').update(status='paid', balance_due=0)
        self.assertIsNone(self.match('KESTREL FOODS INV1004')['invoice'])

    def test_typo_and_reordered_name(self):
        self.assertEqual(self.match('LOGISTICS BRIGHTLINE', credit=236000)['invoice'], 'INV-1002')
        self.assertEqual(self.match('BRIGHTLINE LOGISTCS', credit=236000)['invoice'], 'INV-1002')

    def test_unknown_payer_exact_amount_is_insufficient(self):
        self.assertIsNone(self.match('MAPLE RETAIL SERVICES')['invoice'])
        self.assertIsNone(self.match('R K SHARMA CLINIC DUES', credit=88500)['invoice'])

    def test_partial_and_tds(self):
        r = self.match('QUILLON TECH', credit=324000)
        self.assertEqual(r['invoice'], 'INV-1007')
        self.assertIn('TDS', r['reason'])
        self.assertEqual(self.match('ORBIS PKG', credit=200000)['invoice'], 'INV-1013')

    def test_current_balance_and_no_automatic_application(self):
        i = next(i for i in self.state['invoices'] if i['invoice_number']=='INV-1004')
        i['balance_due'] = 39000
        self.assertEqual(self.match(credit=39000)['invoice'], 'INV-1004')
        self.assertEqual(i['balance_due'], 39000)
        self.assertEqual(i['status'], 'sent')

    def test_explicit_customer_alias(self):
        next(c for c in self.state['customers'] if c['customer_id']=='C002')['aliases']=['Kestrel Trading Desk']
        self.assertEqual(self.match('KESTREL TRADING DESK', reference='INV1004')['invoice'], 'INV-1004')
