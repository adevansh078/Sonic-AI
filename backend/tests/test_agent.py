import unittest
from email.message import EmailMessage
from app.store import initial_state
from app.inbox import ingest
from app.agent import context_for, render, INTRO
from app.llm import Decision
from app.review import recover_legacy, human_block

class AgentTests(unittest.TestCase):
    def test_technical_failure_does_not_block_new_email(self):
        s,m=self.sample();t=s['conversations'][0]
        t.update(status='attention',reason='Provider timed out')
        t['messages'].append(dict(id='old',transport='imap',processing_status='attention',
                                 review_kind='generation_failed',verified=True))
        ingest(s,m.as_bytes(),1,'10','company@gmail.com')
        self.assertEqual(t['messages'][-1]['processing_status'],'pending')
        self.assertFalse(human_block(t))

    def test_real_human_review_still_blocks(self):
        s,m=self.sample();t=s['conversations'][0]
        t.update(status='attention')
        t['messages'].append(dict(id='old',processing_status='attention',review_kind='human_review'))
        ingest(s,m.as_bytes(),1,'10','company@gmail.com')
        self.assertEqual(t['messages'][-1]['processing_status'],'attention')
        self.assertEqual(t['messages'][-1]['review_kind'],'human_review')

    def test_legacy_block_is_retryable_but_send_attempt_is_not(self):
        t=dict(reason='Conversation already awaits human review.',messages=[
            dict(id='old',transport='imap',processing_status='attention',verified=True)])
        recover_legacy(t)
        self.assertEqual(t['messages'][0]['review_kind'],'generation_failed')
        t=dict(reason='Conversation already awaits human review.',messages=[
            dict(id='old',transport='imap',processing_status='attention',verified=True),
            dict(reply_to_id='old',delivery_status='unknown')])
        recover_legacy(t)
        self.assertNotIn('review_kind',t['messages'][0])

    def sample(self, sender='customer2@example.com'):
        s=initial_state()
        s['conversations']=[dict(id='thread',customer_id='C002',invoice='INV-1004',status='open',messages=[dict(message_id='<reminder>',direction='out',text='Reminder')])]
        m=EmailMessage();m['From']=sender;m['To']='company@gmail.com';m['Message-ID']='<reply>';m['In-Reply-To']='<reminder>'
        m['Authentication-Results']='mx.google.com; dmarc=pass header.from=example.com'
        m.set_content('What do we owe?')
        return s,m

    def test_threading_and_uid_deduplication(self):
        s,m=self.sample()
        self.assertTrue(ingest(s,m.as_bytes(),1,'10','company@gmail.com'))
        self.assertFalse(ingest(s,m.as_bytes(),1,'10','company@gmail.com'))
        self.assertFalse(ingest(s,m.as_bytes(),2,'11','company@gmail.com'))
        self.assertEqual(len(s['conversations'][0]['messages']),2)
        self.assertEqual(s['conversations'][0]['messages'][-1]['processing_status'],'pending')

    def test_unknown_sender_cannot_borrow_thread(self):
        s,m=self.sample('stranger@example.com');ingest(s,m.as_bytes(),1,'10','company@gmail.com')
        self.assertEqual(s['conversations'][0]['status'],'attention')
        self.assertFalse(s['conversations'][0]['messages'][-1]['verified'])

    def test_customer_context_and_financial_rendering(self):
        s,m=self.sample();ingest(s,m.as_bytes(),1,'10','company@gmail.com')
        t=s['conversations'][0];ctx=context_for(s,t,t['messages'][-1])
        self.assertEqual({i['invoice_number'] for i in ctx['invoices']},{'INV-1004','INV-1005'})
        d=Decision(intent='balance_question',requires_human_review=False,confidence=.99,reason='Routine',
            draft=INTRO['balance_question'],invoice_numbers=['INV-1004'],include_balance=True,include_bank_details=False,payment_note='')
        self.assertIn('118,000.00',render(d,ctx))
        d.draft='Your balance is INR 1.'
        with self.assertRaises(ValueError):render(d,ctx)
        d.draft=INTRO['balance_question'];d.requires_human_review=True
        with self.assertRaises(ValueError):render(d,ctx)

    def test_missing_authentication_requires_review(self):
        s,m=self.sample();del m['Authentication-Results'];ingest(s,m.as_bytes(),1,'10','company@gmail.com')
        self.assertEqual(s['conversations'][0]['status'],'attention')
