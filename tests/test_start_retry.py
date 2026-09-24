import json
import unittest
from unittest.mock import Mock, patch
from test_assistant import AssistantTests as Base, FakeWeChat, snap, msg
from engine import Engine
from wechat import ReadUnavailable


class TemporaryDingTalk(FakeWeChat):
    platform = 'dingtalk'

    def __init__(self):
        super().__init__(snap(msg('旧消息'), title='测试联系人'))
        self.fail = True
        self.begin_listening = Mock()
        self.end_listening = Mock()

    def read(self):
        if self.fail:
            raise ReadUnavailable('钉钉最新消息读取不完整，本轮不回复')
        return super().read()


class StartupRetryTests(unittest.TestCase):
    setUp = Base.setUp
    tearDown = Base.tearDown

    def prepare(self):
        adapter = TemporaryDingTalk()
        worker = Engine(self.cfg, adapter, self.generator, self.dir)
        return worker, adapter

    def retry_now(self, worker):
        worker.start_retry['at'] = 0
        worker.retry_startup()

    def test_partial_read_retries_then_connects_without_replaying_old_message(self):
        worker, adapter = self.prepare()
        worker.start(auto_reply=False, reply_latest=True)
        self.assertTrue(worker.starting)
        self.assertFalse(worker.enabled)
        self.assertTrue(json.loads(worker.session_path.read_text())['enabled'])
        self.assertTrue(worker.retry_startup())
        adapter.begin_listening.assert_not_called()
        adapter.fail = False
        self.retry_now(worker)
        self.assertTrue(worker.enabled)
        self.assertFalse(worker.starting)
        self.assertFalse(worker.auto_reply)
        adapter.begin_listening.assert_called_once()
        worker.tick()
        self.generator.assert_not_called()
        self.assertEqual(adapter.sent, [])
        adapter.snapshot = snap(msg('旧消息'), msg('新问题'), title='测试联系人')
        worker.tick()
        self.assertIsNotNone(worker.pending)

    def test_pause_cancels_retry_and_late_callback(self):
        worker, adapter = self.prepare()
        worker.start()
        epoch = worker.start_retry['epoch']
        worker.stop()
        adapter.fail = False
        worker.start(_retry_epoch=epoch)
        self.assertFalse(worker.retry_startup())
        self.assertFalse(worker.enabled)
        self.assertFalse(worker.starting)
        adapter.begin_listening.assert_not_called()
        self.assertFalse(json.loads(worker.session_path.read_text())['enabled'])

    def test_restart_during_retry_preserves_intent_and_manual_mode(self):
        worker, adapter = self.prepare()
        worker.start(auto_reply=False, expected_title='测试联系人')
        worker.stop('应用退出', preserve_session=True)
        restored = Engine(self.cfg, adapter, self.generator, self.dir)
        restored.restore_listening()
        self.assertTrue(restored.starting)
        self.assertEqual(restored.start_retry['target'], '测试联系人')
        adapter.fail = False
        self.retry_now(restored)
        self.assertTrue(restored.enabled)
        self.assertFalse(restored.auto_reply)
        restored.tick()
        self.generator.assert_not_called()

    def test_retry_backoff_caps_and_settings_do_not_cancel_retry(self):
        worker, adapter = self.prepare()
        with patch('engine.time.monotonic', return_value=100):
            worker.start()
            self.assertEqual(worker.start_retry['at'], 105)
            worker.settings_updated()
            for delay in (10, 20, 40, 60, 60):
                self.retry_now(worker)
                self.assertEqual(worker.start_retry['at'], 100 + delay)
        self.assertTrue(worker.starting)

    def test_partial_read_after_subscription_closes_before_retry(self):
        worker, adapter = self.prepare()
        adapter.fail = False
        adapter.begin_listening.side_effect = lambda *_: setattr(adapter, 'fail', True)
        worker.start()
        self.assertTrue(worker.starting)
        adapter.end_listening.assert_called_once()
        adapter.begin_listening.side_effect = None
        adapter.fail = False
        self.retry_now(worker)
        self.assertTrue(worker.enabled)

    def test_identity_or_auth_failure_is_not_retried(self):
        worker, adapter = self.prepare()
        adapter.read = Mock(side_effect=ValueError('账号身份不匹配'))
        with self.assertRaises(ValueError):
            worker.start()
        self.assertIsNone(worker.start_retry)
        self.assertFalse(worker.starting)
