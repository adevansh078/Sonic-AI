import tempfile
import unittest
from unittest.mock import patch
from app.store import Store
from app.reminders import preview, send

CONFIG = dict(MAIL_USERNAME='company@gmail.com', TEST_PAYER_EMAIL='payer@gmail.com', MAIL_APP_PASSWORD='x'*16)


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(directory=self.directory.name)
        def configure(state):
            next(c for c in state['customers'] if c['customer_id']=='C002')['contact_email']='payer@gmail.com'
        self.store.mutate(configure)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_preview_customer_scope_and_no_mutation(self):
        before = self.store.get()
        result = preview(before, 'C002', CONFIG)
        self.assertEqual(result['invoice_numbers'], ['INV-1004', 'INV-1005'])
        self.assertIn('118,000.00', result['body'])
        self.assertNotIn('INV-1002', result['body'])
        self.assertIsNone(result['blocked_reason'])
        self.assertEqual(self.store.get(), before)

    @patch('app.reminders.dotenv_values', return_value=CONFIG)
    @patch('app.reminders.smtplib.SMTP')
    def test_single_email_and_duplicate_prevention(self, smtp, config):
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        prepared = preview(self.store.get(), 'C002', CONFIG)
        send(self.store, 'C002', prepared['preview_id'])
        client = smtp.return_value.__enter__.return_value
        self.assertEqual(client.send_message.call_count, 1)
        mail = client.send_message.call_args.args[0]
        self.assertEqual(mail['To'], 'payer@gmail.com')
        self.assertIn('INV-1004', mail.get_content())
        self.assertIn('INV-1005', mail.get_content())
        with self.assertRaises(ValueError):
            send(self.store, 'C002', prepared['preview_id'])
        self.assertEqual(client.send_message.call_count, 1)

    @patch('app.reminders.dotenv_values', return_value=CONFIG)
    @patch('app.reminders.smtplib.SMTP')
    def test_changed_balance_requires_new_preview(self, smtp, config):
        prepared = preview(self.store.get(), 'C002', CONFIG)
        self.store.mutate(lambda s: next(i for i in s['invoices'] if i['invoice_number']=='INV-1004').update(balance_due=100))
        with self.assertRaisesRegex(ValueError, 'changed'):
            send(self.store, 'C002', prepared['preview_id'])
        smtp.assert_not_called()

    @patch('app.reminders.dotenv_values', return_value=CONFIG)
    @patch('app.reminders.smtplib.SMTP')
    def test_uncertain_delivery_blocks_resend(self, smtp, config):
        smtp.return_value.__enter__.return_value.send_message.side_effect = TimeoutError()
        prepared = preview(self.store.get(), 'C002', CONFIG)
        with self.assertRaisesRegex(ValueError, 'unknown'):
            send(self.store, 'C002', prepared['preview_id'])
        thread = self.store.get()['conversations'][-1]
        self.assertEqual(thread['status'], 'attention')
        self.assertEqual(thread['messages'][0]['delivery_status'], 'unknown')
        with self.assertRaises(ValueError):
            send(self.store, 'C002', prepared['preview_id'])
        self.assertEqual(smtp.call_count, 1)

    def test_unconfigured_customer_is_preview_only(self):
        result = preview(self.store.get(), 'C001', CONFIG)
        self.assertIn('TEST_PAYER_EMAIL', result['blocked_reason'])

    def test_selected_invoice_only_and_invalid_selections(self):
        result=preview(self.store.get(),'C002',CONFIG,['INV-1004'])
        self.assertEqual(result['invoice_numbers'],['INV-1004'])
        self.assertNotIn('INV-1005',result['body'])
        self.assertIn('Total for invoices in this reminder (INR): 59,000.00',result['body'])
        for selection in ([],['INV-1002'],['INV-1006'],['INV-1004','INV-1004']):
            with self.assertRaises(ValueError):preview(self.store.get(),'C002',CONFIG,selection)

    @patch('app.reminders.dotenv_values', return_value=CONFIG)
    @patch('app.reminders.smtplib.SMTP')
    def test_send_preserves_selection_and_rejects_changed_selection(self,smtp,config):
        smtp.return_value.__enter__.return_value.send_message.return_value={}
        prepared=preview(self.store.get(),'C002',CONFIG,['INV-1004'])
        with self.assertRaises(ValueError):send(self.store,'C002',prepared['preview_id'],['INV-1005'])
        smtp.assert_not_called()
        send(self.store,'C002',prepared['preview_id'],['INV-1004'])
        mail=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertIn('INV-1004',mail.get_content())
        self.assertNotIn('INV-1005',mail.get_content())
