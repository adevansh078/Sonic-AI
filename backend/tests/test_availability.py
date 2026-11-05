import tempfile
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from app.availability import validate_availability, pause_active, ZONE
from app.llm import Decision
from app.store import Store
from app.agent import Agent
from app.reminders import preview, send

class AvailabilityTests(unittest.TestCase):
 def setUp(self):
  self.day=(datetime.now(ZONE).date()+timedelta(days=7)).isoformat()
  self.text='I am out of office. I return on '+self.day
  self.data=dict(intent='out_of_office',requires_human_review=False,confidence=.99,reason='Away',draft='',invoice_numbers=[],include_balance=False,include_bank_details=False,payment_note='',reminder_resume_on=self.day,availability_evidence=self.text)
 def test_explicit_return_and_extension_separation(self):
  d=Decision(**self.data)
  self.assertEqual(validate_availability(d,dict(text=self.text,verified=True)),self.day)
  for text in [self.text+' Please extend the payment deadline.',self.text+' I need more time to pay.']:
   with self.assertRaises(ValueError):validate_availability(d,dict(text=text,verified=True))
  with self.assertRaises(ValueError):validate_availability(d,dict(text=self.text,verified=False))
 def test_ambiguous_and_invented_date_rejected(self):
  for text in ['I am out of office sometime soon','I am away, back next Monday.']:
   d=Decision(**{**self.data,'availability_evidence':text})
   with self.assertRaises(ValueError):validate_availability(d,dict(text=text,verified=True))
  d=Decision(**{**self.data,'reminder_resume_on':'2099-01-01'})
  with self.assertRaises(ValueError):validate_availability(d,dict(text=self.text,verified=True))
 def test_pause_saved_no_reply_and_sending_blocked(self):
  with tempfile.TemporaryDirectory() as directory:
   store=Store(directory)
   def seed(s):
    s['customers'][1]['contact_email']='payer@gmail.com'
    s['conversations']=[dict(id='t',customer_id='C002',status='open',messages=[dict(id='in',text=self.text,direction='in',transport='imap',verified=True,processing_status='pending')])]
   store.mutate(seed);agent=Agent(store)
   config=dict(MAIL_USERNAME='company@gmail.com',TEST_PAYER_EMAIL='payer@gmail.com',MAIL_APP_PASSWORD='x'*16)
   before=preview(store.get(),'C002',config)
   with patch('app.agent.classify',return_value=Decision(**self.data)),patch('app.agent.deliver') as deliver:
    agent.process();deliver.assert_not_called()
   state=store.get();self.assertTrue(pause_active(state['customers'][1]))
   self.assertEqual(state['conversations'][0]['messages'][0]['processing_status'],'completed')
   self.assertIn('Out of office',preview(state,'C002',config)['blocked_reason'])
   with patch('app.reminders.dotenv_values',return_value=config),patch('app.reminders.smtplib.SMTP') as smtp:
    with self.assertRaises(ValueError):send(store,'C002',before['preview_id'])
    smtp.assert_not_called()
   self.assertEqual(state['invoices'][3]['balance_due'],59000)
   store.close()
 def test_expired_pause_does_not_block(self):
  self.assertFalse(pause_active({'reminder_pause':{'resume_on':datetime.now(ZONE).date().isoformat()}}))

 def test_missing_year_until_uses_received_date(self):
  from unittest.mock import patch
  from datetime import date,datetime
  text='hey I am out of office till 10 November ?'
  decision=Decision(**{**self.data,'availability_evidence':'hey I am out of office till 10 November','reminder_resume_on':'2026-11-11'})
  class FixedDateTime(datetime):
   @classmethod
   def now(cls,tz=None):return cls(2026,11,6,tzinfo=tz)
  with patch('app.availability.datetime',FixedDateTime):
   self.assertEqual(validate_availability(decision,dict(text=text,verified=True,at='2026-11-05T21:53:44Z')),'2026-11-11')

 def test_december_to_january_rollover(self):
  class December(datetime):
   @classmethod
   def now(cls,tz=None):return cls(2026,12,20,tzinfo=tz)
  with patch('app.availability.datetime',December):
   for wording,expected in [('till 5Jan','2027-01-06'),('till 5 jan','2027-01-06'),('until 5 January','2027-01-06'),('back on 5Jan','2027-01-05')]:
    text='I am out of office '+wording
    d=Decision(**{**self.data,'availability_evidence':text,'reminder_resume_on':expected})
    self.assertEqual(validate_availability(d,dict(text=text,verified=True,at='2026-12-20T08:00:00Z')),expected)
   text='I am out of office till 5Jan 2026'
   d=Decision(**{**self.data,'availability_evidence':text,'reminder_resume_on':'2027-01-06'})
   with self.assertRaises(ValueError):validate_availability(d,dict(text=text,verified=True,at='2026-12-20T08:00:00Z'))
