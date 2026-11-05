import unittest
from unittest.mock import Mock, patch
from app.agent import Agent

class PollingTests(unittest.TestCase):
 def test_waits_sixty_seconds_and_continues_after_failure(self):
  agent=Agent(Mock())
  agent.stop_event=Mock()
  agent.stop_event.is_set.return_value=False
  agent.stop_event.wait.side_effect=[False,False,True]
  agent.check=Mock(side_effect=[ValueError('failure'),{}])
  with patch('app.agent.dotenv_values',return_value={'MAIL_POLL_ENABLED':'true','MAIL_POLL_INTERVAL_SECONDS':'60'}), patch('app.agent.threading.Thread') as thread:
   agent.start()
   thread.call_args.kwargs['target']()
  self.assertEqual(agent.check.call_count,2)
  self.assertEqual([c.args[0] for c in agent.stop_event.wait.call_args_list],[60,60,60])
 def test_disabled_worker_does_not_fetch_mail(self):
  agent=Agent(Mock());agent.stop_event=Mock();agent.stop_event.is_set.return_value=False
  agent.stop_event.wait.side_effect=[False,True];agent.check=Mock()
  with patch('app.agent.dotenv_values',return_value={'MAIL_POLL_ENABLED':'false'}), patch('app.agent.threading.Thread') as thread:
   agent.start();thread.call_args.kwargs['target']()
  agent.check.assert_not_called()
