import copy
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'src'))
from config import Config
from dingtalk import DingTalk
from ding_stream import MessageStream
from engine import Engine


class LiveMessagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(Path(self.tmp.name))
        self.cfg.save({'api_key':'fake', 'dingtalk_profile':'profile',
                       'dingtalk_conversation':'cid', 'dingtalk_name':'联系人'})
        self.adapter = DingTalk(self.cfg)
        self.adapter.account = {'profile':'profile'}
        self.adapter.own_ids = {'me'}
        self.generator = Mock(return_value='生成的回复')
        self.engine = Engine(self.cfg, self.adapter, self.generator, Path(self.tmp.name)/'worker')
        self.writes = []
        self.history_calls = 0
        self.group = False
        self.bad_receipt = False
        self.cli = patch('dingtalk.run', side_effect=self.run_cli).start()
        patch('dingtalk.verify_profile').start()
        patch('engine.time.sleep').start()
        patch.object(MessageStream, 'start', lambda stream: stream.ready.set()).start()
        self.addCleanup(patch.stopall)

    def run_cli(self, args, profile=None):
        if '+chat-messages' in args:
            self.history_calls += 1
            return {'partial':True, 'messages':[], 'failures':[{'reason':'history unavailable'}]}
        if 'conversation-info' in args:
            return {'result':{'conversationInfo':{'openConversationId':'cid', 'singleChat':not self.group, 'title':'联系人'}}}
        if 'person' in args:
            return {'result':[{'name':'联系人','openDingTalkId':'peer'}]}
        if '+messages-reply' in args:
            self.writes.append(args)
            return {'openTaskId':'task'}
        if 'query-send-status' in args:
            return {'openMessageId':'sent','openConversationId':'cid'}
        if '+messages-mget' in args:
            return {'messages':[{'messageId':'sent','conversationId':'wrong' if self.bad_receipt else 'cid',
                                 'senderId':'me','text':'生成的回复'}], 'partial':False, 'failures':[]}
        raise AssertionError(args)

    def event(self, mid='new', sender='peer', cid='cid', text='你好'):
        return {'conversation_id':cid, 'message_id':mid, 'sender_open_dingtalk_id':sender,
                'sender':'联系人','content':text}

    def test_dm_starts_without_history_and_generates_from_new_event(self):
        self.engine.start(auto_reply=False, reply_latest=True)
        self.assertTrue(self.engine.enabled)
        self.assertIn('历史消息暂不可用', self.engine.status)
        self.assertFalse(self.engine.initial_tick)
        self.assertIn('peer', self.adapter.stream.command)
        self.engine.tick()
        self.generator.assert_not_called()
        self.adapter.stream.accept(self.event())
        self.assertTrue(self.adapter.has_update())
        self.engine.tick()
        self.generator.assert_called_once()
        self.assertEqual(self.engine.pending['snapshot']['messages'][-1].text, '你好')
        self.assertEqual(self.history_calls, 1)
        self.assertEqual(self.writes, [])
        self.adapter.stream.accept(self.event())
        self.engine.tick()
        self.generator.assert_called_once()

    def test_group_automatic_reply_confirms_exact_message_without_history(self):
        self.group = True
        self.engine.start()
        self.assertIn('+listen-im', self.adapter.stream.command)
        self.adapter.stream.accept(self.event())
        self.engine.tick()
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.engine.count, 1)
        self.assertTrue(self.engine.enabled)
        self.assertEqual(self.history_calls, 1)
        self.engine.tick()
        self.assertEqual(len(self.writes), 1)

    def test_events_during_start_are_not_discarded_as_history(self):
        def start(stream):
            stream.ready.set()
            stream.accept(self.event())
        with patch.object(MessageStream, 'start', start):
            self.engine.start(auto_reply=False)
        self.engine.tick()
        self.generator.assert_called_once()

    def test_malformed_and_other_conversation_events_cannot_trigger_reply(self):
        self.engine.start(auto_reply=False)
        self.adapter.stream.accept(self.event(cid='other'))
        self.adapter.stream.accept(self.event(sender=None))
        self.engine.tick()
        self.generator.assert_not_called()
        self.adapter.stream.accept(self.event('self',sender='me'))
        self.engine.tick()
        self.generator.assert_not_called()

    def test_new_event_invalidates_pending_even_when_history_is_unavailable(self):
        self.engine.start(auto_reply=False)
        self.adapter.stream.accept(self.event())
        self.engine.tick()
        old = copy.deepcopy(self.engine.pending)
        self.adapter.stream.accept(self.event('second',text='再等等'))
        with self.assertRaises(ValueError):
            self.engine.send_pending(old['id'])
        self.assertEqual(self.writes, [])

    def test_wrong_receipt_does_not_confirm_or_resend(self):
        self.engine.start()
        self.bad_receipt = True
        self.adapter.stream.accept(self.event())
        self.engine.tick()
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.engine.count, 0)
        self.assertFalse(self.engine.enabled)
