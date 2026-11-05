import json
import tempfile
import unittest
from unittest.mock import patch
import httpx
from app.llm import Decision, _groq
from app.agent import Agent, INTRO
from app.store import Store

DATA=dict(intent='extension',requires_human_review=True,confidence=.99,reason='Extension requires human approval',draft='',invoice_numbers=[],include_balance=False,include_bank_details=False,payment_note='')

class EmptyDraftTests(unittest.TestCase):
 def test_empty_draft_only_allowed_for_human_review(self):
  self.assertEqual(Decision(**DATA).draft,'')
  for draft in ('','  '):
   with self.assertRaises(ValueError):Decision(**{**DATA,'requires_human_review':False,'draft':draft})
 @patch('app.llm.httpx.post')
 def test_groq_accepts_empty_review_draft(self,post):
  post.return_value=httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(DATA)}}]})
  decision=_groq({}, {'GROQ_API_KEY':'fake'})
  self.assertTrue(decision.requires_human_review)
  self.assertEqual(decision._provider,'groq')
 def test_agent_routes_review_without_sending_and_routine_still_sends(self):
  with tempfile.TemporaryDirectory() as directory:
   store=Store(directory)
   store.mutate(lambda s:s['conversations'].append(dict(id='test',customer_id='C002',status='open',messages=[dict(id='in',direction='in',transport='imap',text='Extend the deadline?',processing_status='pending',verified=True)])))
   agent=Agent(store)
   with patch('app.agent.classify',return_value=Decision(**DATA)),patch('app.agent.deliver') as deliver:
    agent.process();deliver.assert_not_called()
   t=store.get()['conversations'][0]
   self.assertEqual(t['messages'][0]['review_kind'],'human_review')
   self.assertTrue(t['messages'][0]['llm_decision']['requires_human_review'])
   self.assertEqual(t['draft'],'')
   store.mutate(lambda s:s['conversations'].clear())
   store.mutate(lambda s:s['conversations'].append(dict(id='routine',customer_id='C002',status='open',messages=[dict(id='in',direction='in',transport='imap',text='Balance?',processing_status='pending',verified=True)])))
   decision=Decision(**{**DATA,'intent':'balance_question','requires_human_review':False,'draft':INTRO['balance_question'],'include_balance':True})
   def complete(*args):
    store.mutate(lambda s:s['conversations'][0]['messages'][0].update(processing_status='completed'))
   with patch('app.agent.classify',return_value=decision),patch('app.agent.deliver',side_effect=complete) as deliver:
    agent.process();deliver.assert_called_once()
    self.assertIn('118,000.00',deliver.call_args.args[3])
   store.close()
