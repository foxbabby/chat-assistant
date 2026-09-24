import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'src'))
from config import Config, validate
from dingtalk import DingTalk
from engine import Engine, digest
from wechat import ReadUnavailable, signature
from test_assistant import FakeWeChat, msg, snap


def row(mid, text='消息', sender='other'):
    return {'messageId': mid, 'conversationId': 'cid-test', 'senderId': sender, 'sender': '同名', 'text': text}


class DingTalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.tmp.name))
        self.cfg.save({'api_key':'fake-key', 'dingtalk_profile':'org:user', 'dingtalk_conversation':'cid-test', 'dingtalk_name':'测试会话'})
        self.adapter = DingTalk(self.cfg)
        self.account = {'profile':'org:user','userId':'self','corpName':'测试','userName':'同名'}
        self.adapter.account = self.account
        self.adapter.own_ids = {'self', 'self-open'}
        self.rows = [row('old')]
        self.writes = []
        self.verify = patch('dingtalk.verify_profile', return_value=self.account).start()
        self.cli = patch('dingtalk.run', side_effect=self.cli_run).start()
        patch('dingtalk.time.sleep').start()
        patch.object(self.adapter, 'begin_listening').start()
        patch.object(self.adapter, 'end_listening').start()
    def tearDown(self):
        patch.stopall()
        self.tmp.cleanup()
    def cli_run(self, args, profile=None):
        if '+chat-messages' in args:
            return {'messages':copy.deepcopy(self.rows), 'complete':False, 'hasMore':True, 'partial':False, 'failures':[]}
        if '+messages-reply' in args:
            self.writes.append(args)
            self.rows.insert(0,row('sent', args[args.index('--content')+1], 'self-open'))
            return {'openTaskId':'task'}
        if 'query-send-status' in args:
            return {'openMessageId':'sent','openConversationId':'cid-test'}
        raise AssertionError(args)
    def engine(self):
        return Engine(self.cfg,self.adapter,Mock(return_value='好的'),Path(self.tmp.name)/'ding')
    def test_reads_author_ids_not_display_name(self):
        self.rows=[row('latest',sender='other'),row('before',sender='self-open')]
        result=self.adapter.read()
        self.assertEqual([m.side for m in result['messages']],['me','them'])
    def test_two_stage_reply_quotes_original_with_distinct_idempotency(self):
        e=self.engine()
        def generate(*args):return '查到的规则。'
        generate.supports_research_ack=True
        e.generator=generate
        e.start();self.rows.insert(0,row('question','需求计划有哪些类型'))
        e.tick()
        self.assertEqual(len(self.writes),2)
        self.assertEqual([w[w.index('--message-id')+1] for w in self.writes],['question','question'])
        keys=[w[w.index('--idempotency-key')+1] for w in self.writes]
        self.assertNotEqual(keys[0],keys[1])
        e.tick();self.assertEqual(len(self.writes),2)
    def test_identical_messages_have_distinct_ids(self):
        first=self.adapter.read();self.rows=[row('new'),row('old')]
        self.assertNotEqual(digest(first),digest(self.adapter.read()))
    def test_missing_sender_is_not_automatically_replied(self):
        e=self.engine();e.start();self.rows.insert(0,row('new',sender=None));e.tick()
        e.generator.assert_not_called();self.assertTrue(e.enabled)
    def test_own_message_not_replied(self):
        e=self.engine();e.start();self.rows.insert(0,row('new',sender='self-open'));e.tick()
        e.generator.assert_not_called();self.assertFalse(self.writes)
    def test_history_skipped_by_default(self):
        e=self.engine();e.start();e.tick();e.generator.assert_not_called()
    def test_new_message_send_confirmed_and_deduped(self):
        e=self.engine();e.start();self.rows.insert(0,row('new'));e.tick()
        self.assertEqual(len(self.writes),1);self.assertEqual(e.count,1)
        e.tick();self.assertEqual(len(self.writes),1)
        self.assertIn('--idempotency-key',self.writes[0])
    def test_other_person_repeating_own_text_still_replied(self):
        e=self.engine();e.start();self.rows.insert(0,row('new','好的'));e.tick()
        self.rows.insert(0,row('next','好的'));e.tick()
        self.assertEqual(len(self.writes),2)
    def test_reply_latest_option(self):
        e=self.engine();e.start(reply_latest=True);e.tick();self.assertEqual(len(self.writes),1)
    def test_changed_target_cancels_send(self):
        expected=self.adapter.read();self.cfg.save({'dingtalk_name':'另一个会话'})
        with self.assertRaises(ValueError):self.adapter.send('好的',expected,lambda:True)
        self.assertFalse(self.writes)
    def test_changed_message_cancels_send(self):
        expected=self.adapter.read();self.rows.insert(0,row('new'))
        with self.assertRaises(ValueError):self.adapter.send('好的',expected,lambda:True)
        self.assertFalse(self.writes)
    def test_stop_cancels_send(self):
        with self.assertRaises(ValueError):self.adapter.send('好的',self.adapter.read(),lambda:False)
        self.assertFalse(self.writes)
    def test_incomplete_read_does_not_generate(self):
        e=self.engine();e.start();self.cli.side_effect=None;self.cli.return_value={'messages':[],'partial':True}
        e.tick();e.generator.assert_not_called();self.assertTrue(e.enabled)
    def test_wrong_conversation_rejected(self):
        self.rows[0]['conversationId']='wrong'
        with self.assertRaises(ReadUnavailable):self.adapter.read()
    def test_missing_message_id_rejected(self):
        self.rows[0].pop('messageId')
        with self.assertRaises(ReadUnavailable):self.adapter.read()
    def test_delivery_timeout_is_not_retried(self):
        e=self.engine();e.start();self.rows.insert(0,row('new'))
        original=self.cli_run
        def uncertain(args,profile=None):
            if '+messages-reply' in args:
                self.writes.append(args);raise ReadUnavailable('超时')
            return original(args,profile)
        self.cli.side_effect=uncertain;e.tick()
        self.assertFalse(e.enabled);self.assertEqual(len(self.writes),1)
        e.start(reply_latest=True);e.tick();self.assertEqual(len(self.writes),1)
    def test_platform_engines_are_independent(self):
        we=Engine(self.cfg,FakeWeChat(snap(msg('旧'))),Mock(),Path(self.tmp.name)/'wx')
        de=self.engine();we.start();de.start();de.stop()
        self.assertTrue(we.enabled);self.assertFalse(de.enabled)
        self.assertNotEqual(we.ledger_path,de.ledger_path)
    def test_config_types_and_interval_validation(self):
        for value in [0,5,301,'15',True]:
            with self.assertRaises(ValueError):validate({**self.cfg.data,'dingtalk_interval':value})
        with self.assertRaises(ValueError):validate({**self.cfg.data,'wechat_reply_latest':'false'})
