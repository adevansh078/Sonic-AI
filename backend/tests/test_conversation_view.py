import unittest
from app.conversation_view import prepare_conversation

class ReviewQueueTests(unittest.TestCase):
    def test_failed_send_with_completed_incoming_is_attention(self):
        t={'status':'open','messages':[{'id':'in','transport':'imap','processing_status':'completed'},
            {'transport':'smtp','delivery_status':'failed'}]}
        self.assertEqual(prepare_conversation(t)['status'],'attention')

    def test_successful_retry_resolves_old_failure(self):
        t={'status':'attention','messages':[{'id':'in','transport':'imap','processing_status':'completed'},
            {'transport':'smtp','reply_to_id':'in','delivery_status':'failed'},
            {'transport':'smtp','reply_to_id':'in','delivery_status':'accepted'}]}
        self.assertEqual(prepare_conversation(t)['status'],'open')

    def test_pending_is_not_human_attention(self):
        t={'status':'attention','reason':'Old failure','messages':[{'transport':'imap','processing_status':'pending'}]}
        self.assertEqual(prepare_conversation(t)['status'],'open')

    def test_accepted_reminder_is_not_attention(self):
        t={'status':'attention','messages':[{'transport':'smtp','delivery_status':'accepted'}]}
        self.assertEqual(prepare_conversation(t)['status'],'open')

    def test_accepted_response_resolves_stale_attention(self):
        t={'status':'attention','messages':[{'id':'in','transport':'imap','processing_status':'attention'},
            {'transport':'smtp','reply_to_id':'in','delivery_status':'accepted'}]}
        self.assertEqual(prepare_conversation(t)['status'],'open')

    def test_new_unanswered_message_retains_attention(self):
        t={'status':'attention','messages':[{'id':'old','transport':'imap','processing_status':'completed'},
            {'transport':'smtp','reply_to_id':'old','delivery_status':'accepted'},
            {'id':'new','transport':'imap','processing_status':'attention'}]}
        self.assertEqual(prepare_conversation(t)['status'],'attention')

    def test_timeout_does_not_show_fake_ai_draft(self):
        t={'status':'attention','reason':'Gemini ReadTimeout','draft':'Generic fallback',
           'messages':[{'id':'in','transport':'imap','processing_status':'attention'}]}
        self.assertEqual(prepare_conversation(t)['draft'],'')

    def test_failed_send_retained(self):
        t={'status':'attention','messages':[{'transport':'smtp','delivery_status':'failed'}]}
        self.assertEqual(prepare_conversation(t)['status'],'attention')
