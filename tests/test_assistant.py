import time
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from config import Config, validate
from engine import Engine, excluded, digest
from cloud import completion, CloudError


def msg(text, side='them', sender='张三', conf=1):
    return NS(text=text, side=side, sender=sender, conf=conf)


def snap(*messages, title='测试群', wid=1):
    return {'window': {'wid': wid}, 'chat_title': title, 'messages': list(messages)}


class FakeWeChat:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.sent = []
    def permissions(self):
        return {'screen': True, 'accessibility': True}
    def read(self):
        return self.snapshot
    def send(self, text, expected, allowed):
        if not allowed():
            raise ValueError('已暂停')
        self.sent.append(text)
        self.snapshot = snap(*expected['messages'], msg(text, 'me'), title=expected['chat_title'])
        return self.snapshot


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg = Config(self.dir)
        self.cfg.save({'api_key': 'test-placeholder', 'excluded_senders': ['李四']})
        self.adapter = FakeWeChat(snap(msg('原有消息')))
        self.generator = Mock(return_value='知道啦，谢谢分享。')
        self.engine = Engine(self.cfg, self.adapter, self.generator, self.dir)
        self.sleep = patch('engine.time.sleep').start()
    def tearDown(self):
        patch.stopall()
        self.tmp.cleanup()
    def test_key_never_exposed_and_restricted_permissions(self):
        self.assertNotIn('api_key', self.cfg.public())
        self.assertNotIn('test-placeholder', json.dumps(self.cfg.public()))
        self.assertEqual(os.stat(self.cfg.path).st_mode & 0o777, 0o600)
    def test_keep_key_blank_and_reject_endpoint_change_without_key(self):
        self.cfg.save({'api_key': '', 'style': '高情商'})
        self.assertEqual(self.cfg.data['api_key'], 'test-placeholder')
        with self.assertRaises(ValueError):
            self.cfg.save({'base_url':'https://different.example'})
    def test_cloud_only(self):
        for url in ['http://localhost:11434', 'https://localhost', 'https://127.0.0.1', 'https://a:b@host', 'https://host?key=secret']:
            with self.assertRaises(ValueError):
                validate({**self.cfg.data, 'base_url':url})
    def test_default_self_always_excluded(self):
        self.assertTrue(excluded(snap(msg('自己发言','me')), {'excluded_senders':[]}))
    def test_excluded_group_sender(self):
        self.assertTrue(excluded(snap(msg('不要回复', sender='李四')), self.cfg.data))
        self.assertFalse(excluded(snap(msg('可以回复', sender='张三')), self.cfg.data))
    def test_excluded_direct_contact(self):
        self.assertTrue(excluded(snap(msg('不要回复',sender=None), title='李四'), self.cfg.data))
    def test_last_sender_wins(self):
        self.assertTrue(excluded(snap(msg('问题'), msg('答复',sender='李四')), self.cfg.data))
        self.assertFalse(excluded(snap(msg('不要回复',sender='李四'), msg('新问题')), self.cfg.data))
    def test_initial_history_never_replied(self):
        self.engine.start(); self.engine.tick()
        self.generator.assert_not_called()
    def test_skip_self_without_cloud_or_send(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('自己回复','me'))
        self.engine.tick()
        self.generator.assert_not_called(); self.assertEqual(self.adapter.sent, [])
    def test_skip_excluded_sender_without_cloud_or_send(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('不回复的人', sender='李四'))
        self.engine.tick()
        self.generator.assert_not_called(); self.assertEqual(self.adapter.sent, [])
        self.assertIn('跳过', self.engine.status)
    def test_next_allowed_sender_gets_one_reply(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'), msg('不回复',sender='李四'))
        self.engine.tick()
        self.adapter.snapshot=snap(msg('原有消息'), msg('不回复',sender='李四'),msg('新消息'))
        self.engine.tick(); self.engine.tick()
        self.generator.assert_called_once(); self.assertEqual(len(self.adapter.sent),1)
        self.assertEqual(self.engine.count,1)
    def test_switch_chat_cancels(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('新消息'), title='另一个群')
        self.engine.tick()
        self.assertFalse(self.engine.enabled); self.generator.assert_not_called()
    def test_pause_during_generation_cancels(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('新消息'))
        def generate(*args):
            self.engine.stop()
            return '不可发送'
        self.engine.generator=generate
        self.engine.tick()
        self.assertEqual(self.adapter.sent,[])
    def test_failure_is_not_retried(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('新消息'))
        self.adapter.send=Mock(side_effect=ValueError('发送状态不确定'))
        self.engine.tick(); self.engine.tick()
        self.assertFalse(self.engine.enabled)
        self.adapter.send.assert_called_once()
        self.assertIn(digest(self.adapter.snapshot),json.loads(self.engine.ledger_path.read_text()))
    def test_low_confidence_pauses_without_generation(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('模糊',conf=.5))
        self.engine.tick()
        self.generator.assert_not_called(); self.assertTrue(self.engine.enabled)
    def test_cloud_error_sanitizes_body_and_key(self):
        import urllib.error
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=urllib.error.HTTPError('https://test',401,'secret body',{},None)
            with self.assertRaisesRegex(CloudError,'API Key 无效'):
                completion(self.cfg.data,[])
    def test_compatible_request_and_response(self):
        response=Mock(); response.read.return_value=json.dumps({'choices':[{'message':{'content':'测试回复'}}]}).encode()
        cm=Mock(); cm.__enter__=Mock(return_value=response); cm.__exit__=Mock(return_value=False)
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=cm
            self.assertEqual(completion(self.cfg.data,[]),'测试回复')
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.deepseek.com/chat/completions')
            self.assertEqual(json.loads(request.data)['thinking'],{'type':'disabled'})


if __name__ == '__main__':
    unittest.main()

class RecognitionTests(unittest.TestCase):
    def test_short_contact_titles_supported(self):
        from perception import TextBlock, extract_chat_title
        self.assertEqual(extract_chat_title([TextBlock('李四',1,.4,.94,.1,.03)]),'李四')
    def test_unknown_group_author_with_exclusions_skipped(self):
        self.assertFalse(excluded(snap(msg('未知作者',sender=None),title='测试群（8）'),{'excluded_senders':['李四']}))
    def test_exclusion_list_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            config=Config(Path(temp)); config.save({'excluded_senders':[' 李四 ','李四','张三']})
            self.assertEqual(Config(Path(temp)).data['excluded_senders'],['李四','张三'])

class DisplayStateTests(unittest.TestCase):
    setUp = AssistantTests.setUp
    tearDown = AssistantTests.tearDown
    def test_start_failure_has_persistent_reason(self):
        self.adapter.read=Mock(side_effect=ValueError('无法识别会话名称'))
        with self.assertRaises(ValueError): self.engine.start()
        state=self.engine.state()
        self.assertFalse(state['starting']); self.assertFalse(state['enabled'])
        self.assertIn('开启失败',state['status']); self.assertIn('无法识别会话名称',state['status'])
    def test_start_shows_existing_message_without_reply(self):
        self.engine.start()
        self.assertEqual(self.engine.latest['incoming'],'原有消息')
        self.assertTrue(self.engine.enabled)
        self.generator.assert_not_called()
    def test_skipped_message_is_visible(self):
        self.engine.start(); self.adapter.snapshot=snap(msg('原有消息'),msg('自己新发的消息','me'))
        self.engine.tick()
        self.assertEqual(self.engine.latest['incoming'],'自己新发的消息')
        self.assertIn('跳过',self.engine.latest['state']); self.generator.assert_not_called()
    def test_read_only_refresh_does_not_enable_or_generate(self):
        self.engine.scan()
        self.assertEqual(self.engine.latest['incoming'],'原有消息')
        self.assertFalse(self.engine.enabled); self.generator.assert_not_called()

class AccessibilityAlignmentTests(unittest.TestCase):
    def test_alignment_uses_chat_pane_not_screen_center(self):
        from ax_reader import alignment
        pane=(-1000,50,600,600)
        self.assertEqual(alignment((-980,100,120,30),pane),'them')
        self.assertEqual(alignment((-540,140,120,30),pane),'me')
        self.assertEqual(alignment((-1000,140,600,30),pane),'unknown')
    def test_unknown_sender_can_be_replied_to(self):
        with tempfile.TemporaryDirectory() as td:
            cfg=Config(Path(td));cfg.save({'api_key':'test-placeholder'})
            adapter=FakeWeChat(snap(msg('旧消息'))); generator=Mock(return_value='收到')
            engine=Engine(cfg,adapter,generator,Path(td));engine.start()
            adapter.snapshot=snap(msg('旧消息'),msg('新消息',side='unknown'))
            with patch('engine.time.sleep'):
                engine.tick()
            self.assertTrue(engine.enabled);generator.assert_called_once()
            self.assertEqual(adapter.sent,['收到'])

class ListeningRecoveryTests(unittest.TestCase):
    setUp = AssistantTests.setUp
    tearDown = AssistantTests.tearDown
    def test_start_with_unreadable_chat_recovers(self):
        from wechat import ReadUnavailable
        original=self.adapter.read
        self.adapter.read=Mock(side_effect=ReadUnavailable('标题暂不可读'))
        self.engine.start()
        self.assertTrue(self.engine.enabled)
        self.engine.tick(); self.assertTrue(self.engine.enabled)
        self.adapter.read=original
        self.engine.tick()
        self.assertIsNotNone(self.engine.target);self.generator.assert_not_called()
        self.adapter.snapshot=snap(msg('原有消息'),msg('恢复后的新消息'))
        self.engine.tick()
        self.generator.assert_called_once();self.assertTrue(self.engine.enabled)
    def test_switch_pauses_without_replying(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('另一会话已有消息'),title='另一个聊天')
        self.engine.tick();self.assertFalse(self.engine.enabled);self.generator.assert_not_called()
    def test_current_chat_read_failure_does_not_pause(self):
        from wechat import ReadUnavailable
        self.engine.start();target=self.engine.target
        original=self.adapter.read
        self.adapter.read=Mock(side_effect=ReadUnavailable('暂时无法识别标题'))
        self.engine.tick();self.assertTrue(self.engine.enabled);self.assertEqual(self.engine.target,target)
        self.adapter.read=original
        self.adapter.snapshot=snap(msg('原有消息'),msg('新的可识别消息'))
        self.engine.tick();self.generator.assert_called_once();self.assertTrue(self.engine.enabled)


class OCRCacheTests(unittest.TestCase):
    def test_unchanged_pixels_reuse_and_changed_pixels_recognize(self):
        from perception import _cached_blocks, _ocr_cache
        _ocr_cache.clear()
        recognize=Mock(return_value=[{'text':'消息'}])
        first=_cached_blocks(b'first',recognize);first[0]['text']='modified'
        self.assertEqual(_cached_blocks(b'first',recognize)[0]['text'],'消息')
        recognize.assert_called_once()
        _cached_blocks(b'changed',recognize);self.assertEqual(recognize.call_count,2)
    def test_missing_fingerprint_and_expiry_force_recognition(self):
        from perception import _cached_blocks, _ocr_cache
        _ocr_cache.clear();recognize=Mock(return_value=[])
        _cached_blocks(None,recognize);_cached_blocks(None,recognize)
        self.assertEqual(recognize.call_count,2)
        with patch('perception.time.monotonic',side_effect=[0,31]):
            _cached_blocks(b'expiring',recognize);_cached_blocks(b'expiring',recognize)
        self.assertEqual(recognize.call_count,4)


class WeChatWindowTests(unittest.TestCase):
    def test_only_verified_wechat_process_can_supply_window(self):
        import perception
        app=Mock();app.processIdentifier.return_value=100
        def window(pid,wid,name,width):
            return {'kCGWindowOwnerPID':pid,'kCGWindowNumber':wid,'kCGWindowOwnerName':name,
                    'kCGWindowName':name,'kCGWindowBounds':{'Width':width,'Height':700}}
        windows=[window(200,2,'微信聊天助手',1400),window(100,1,'微信',900),window(300,3,'WeChat Fake',1600)]
        with patch('AppKit.NSRunningApplication',Mock(runningApplicationsWithBundleIdentifier_=Mock(return_value=[app]))), patch('perception.Quartz.CGWindowListCopyWindowInfo',return_value=windows):
            self.assertEqual(perception.find_wechat_window().wid,1)
            self.assertEqual(perception.find_wechat_window(previous_wid=2).wid,1)
        with patch('AppKit.NSRunningApplication',Mock(runningApplicationsWithBundleIdentifier_=Mock(return_value=[]))), patch('perception.Quartz.CGWindowListCopyWindowInfo',return_value=windows):
            self.assertIsNone(perception.find_wechat_window())


class SelfNamesTests(unittest.TestCase):
    setUp = AssistantTests.setUp
    tearDown = AssistantTests.tearDown
    def test_alias_persists_and_can_be_cleared(self):
        self.cfg.save({'self_names':[' 我的昵称 ', '群昵称', '我的昵称']})
        self.assertEqual(Config(self.dir).public()['self_names'],['我的昵称','群昵称'])
        self.cfg.save({'style':'高情商'})
        self.assertEqual(self.cfg.data['self_names'],['我的昵称','群昵称'])
        self.cfg.save({'self_names':[]});self.assertEqual(Config(self.dir).data['self_names'],[])
    def test_unknown_side_with_own_author_skips_without_pausing(self):
        self.cfg.save({'self_names':['我的群昵称']});self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('自己的新消息',side='unknown',sender='我的群昵称'))
        self.engine.tick()
        self.generator.assert_not_called();self.assertEqual(self.adapter.sent,[])
        self.assertTrue(self.engine.enabled);self.assertEqual(self.engine.latest['sender'],'我自己')
    def test_title_and_partial_name_never_match_self(self):
        cfg={'self_names':['小明']}
        self.assertFalse(excluded(snap(msg('消息',sender=None),title='小明'),cfg))
        self.assertFalse(excluded(snap(msg('消息',sender='小明同事')),cfg))
        self.assertTrue(excluded(snap(msg('消息',sender='小明')),cfg))
        self.assertTrue(excluded(snap(msg('消息',side='me',sender=None)),{}))
    def test_invalid_alias_list_rejected(self):
        for value in ['名字',[None],['a'*101],['名字']*201]:
            with self.assertRaises(ValueError):self.cfg.save({'self_names':value})


class OutboxEchoTests(unittest.TestCase):
    setUp = AssistantTests.setUp
    tearDown = AssistantTests.tearDown
    def test_own_echo_unknown_side_never_generates_again(self):
        self.engine.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('新的问题',side='unknown',sender=None))
        self.engine.tick();self.generator.assert_called_once()
        answer=self.adapter.sent[0]
        self.adapter.snapshot=snap(msg('原有消息'),msg('新的问题',side='unknown',sender=None),msg(answer,side='unknown',sender=None))
        self.engine.tick();self.engine.tick()
        self.generator.assert_called_once();self.assertEqual(len(self.adapter.sent),1)
        self.assertTrue(self.engine.enabled)
    def test_reservation_survives_failed_send_and_restart(self):
        self.engine.start();self.adapter.snapshot=snap(msg('原有消息'),msg('问题',side='unknown'))
        self.adapter.send=Mock(side_effect=ValueError('发送确认超时'))
        self.engine.tick();self.assertFalse(self.engine.enabled)
        answer=self.generator.return_value
        resumed=Engine(self.cfg,self.adapter,self.generator,self.dir)
        self.assertTrue(resumed.sent_by_assistant(self.adapter.snapshot,answer))
        self.assertFalse(resumed.sent_by_assistant(snap(title='其他聊天'),answer))
        resumed.start()
        self.adapter.snapshot=snap(msg('原有消息'),msg('问题',side='unknown'),msg(answer,side='unknown'))
        resumed.tick();self.generator.assert_called_once();self.assertTrue(resumed.enabled)
    def test_ledger_has_no_plaintext_and_expires(self):
        self.engine.reserve_outgoing(snap(),'秘密回复')
        self.assertNotIn('秘密回复',self.engine.outbox_path.read_text())
        with patch('engine.time.time',return_value=time.time()+86401):
            self.assertFalse(self.engine.sent_by_assistant(snap(),'秘密回复'))
    def test_unknown_sender_media_is_not_sent(self):
        self.engine.start();media=msg('动画表情',side='unknown');media.kind='media'
        self.adapter.snapshot=snap(msg('原有消息'),media)
        self.engine.tick();self.generator.assert_not_called();self.assertTrue(self.engine.enabled)
