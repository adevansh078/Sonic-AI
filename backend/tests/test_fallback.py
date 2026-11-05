import unittest
from unittest.mock import patch
import httpx
from app.llm import classify, Decision

DATA=dict(intent='extension',requires_human_review=True,confidence=.95,reason='Extension needs review',
          draft='Accounts will review.',invoice_numbers=[],include_balance=False,include_bank_details=False,payment_note='')
CONFIG={'GEMINI_API_KEY':'fake','GROQ_API_KEY':'fake','GROQ_FALLBACK_ENABLED':'true'}

class FallbackTests(unittest.TestCase):
    @patch('app.llm.dotenv_values',return_value=CONFIG)
    @patch('app.llm.httpx.post')
    def test_503_uses_groq_and_preserves_review(self,post,config):
        import json
        post.side_effect=[httpx.Response(503),httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(DATA)}}]})]
        result=classify({})
        self.assertEqual(result._provider,'groq');self.assertTrue(result.requires_human_review)
        self.assertEqual(post.call_count,2)

    @patch('app.llm.dotenv_values',return_value=CONFIG)
    @patch('app.llm.httpx.post')
    def test_auth_error_does_not_fallback(self,post,config):
        post.return_value=httpx.Response(403)
        with self.assertRaises(ValueError):classify({})
        self.assertEqual(post.call_count,1)

    @patch('app.llm.dotenv_values',return_value=CONFIG)
    @patch('app.llm.httpx.post')
    def test_valid_review_not_overruled(self,post,config):
        import json
        post.return_value=httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(DATA)}]}}]})
        self.assertTrue(classify({}).requires_human_review)
        self.assertEqual(post.call_count,1)

    @patch('app.llm.dotenv_values',return_value=CONFIG)
    @patch('app.llm.httpx.post')
    def test_timeout_then_invalid_fallback_fails_closed(self,post,config):
        post.side_effect=[httpx.ReadTimeout('timeout'),httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})]
        with self.assertRaises(ValueError):classify({})
        self.assertEqual(post.call_count,2)
