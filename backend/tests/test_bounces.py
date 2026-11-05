import unittest
from app.inbox import ingest
from app.store import initial_state
from app.conversation_view import prepare_conversation

class BounceTests(unittest.TestCase):
 def fixture(self,action='failed',status='5.1.1',recipient='payer@gmail.com',auth=True):
  s=initial_state();s['conversations']=[dict(id='t',customer_id='C002',invoice='INV-1004',status='open',messages=[dict(direction='out',text='Reminder',transport='smtp',message_id='<original>',to='payer@gmail.com',delivery_status='accepted')])]
  authentication='Authentication-Results: mx.google.com; dmarc=pass header.from=googlemail.com\n' if auth else ''
  raw=f'''From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>
Message-ID: <bounce>
Subject: Delivery Status Notification
{authentication}MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="bound"

--bound
Content-Type: text/plain

Delivery notification
--bound
Content-Type: message/delivery-status

Reporting-MTA: dns; googlemail.com
Original-Message-ID: <original>

Final-Recipient: rfc822; {recipient}
Action: {action}
Status: {status}
Diagnostic-Code: smtp; test diagnostic

--bound--
'''.encode()
  return s,raw
 def test_permanent_report_links_and_never_queues_ai(self):
  s,raw=self.fixture();self.assertTrue(ingest(s,raw,1,'v','company@gmail.com'))
  t=s['conversations'][0];m=t['messages'][-1]
  self.assertEqual(m['message_category'],'delivery_failed');self.assertTrue(m['no_reply'])
  self.assertEqual(t['messages'][0]['delivery_status'],'accepted')
  self.assertEqual(prepare_conversation(t)['status'],'attention')
  self.assertFalse(ingest(s,raw,1,'v','company@gmail.com'))
  ingest(s,raw,2,'v','company@gmail.com');self.assertEqual(len(t['messages']),2)
 def test_delayed_report_is_informational(self):
  s,raw=self.fixture('delayed','4.2.2');ingest(s,raw,1,'v','company@gmail.com')
  self.assertEqual(s['conversations'][0]['messages'][-1]['processing_status'],'completed')
  self.assertEqual(prepare_conversation(s['conversations'][0])['status'],'open')
 def test_untrusted_or_mismatched_report_cannot_change_delivery(self):
  for args in ({'auth':False},{'recipient':'other@gmail.com'}):
   s,raw=self.fixture(**args);ingest(s,raw,1,'v','company@gmail.com')
   self.assertNotIn('delivery_notice',s['conversations'][0]['messages'][0])
   self.assertEqual(s['conversations'][-1]['messages'][-1]['message_category'],'delivery_unverified')
