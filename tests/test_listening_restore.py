import json
import unittest
from unittest.mock import Mock
from test_assistant import AssistantTests as Base, FakeWeChat, snap, msg
from engine import Engine
from ding_pool import DingTalkPool


class ListeningRestoreTests(unittest.TestCase):
    setUp = Base.setUp
    tearDown = Base.tearDown

    def restarted(self, adapter=None):
        return Engine(self.cfg, adapter or self.adapter, self.generator, self.dir)

    def test_restore_modes_without_replaying_existing_message(self):
        for auto in (False, True):
            self.engine = self.restarted()
            self.engine.start(auto_reply=auto, reply_latest=True)
            self.engine.stop('应用退出', preserve_session=True)
            worker = self.restarted()
            worker.restore_listening()
            self.assertTrue(worker.enabled)
            self.assertEqual(worker.auto_reply, auto)
            worker.tick()
            self.generator.assert_not_called()
            self.assertEqual(self.adapter.sent, [])
            worker.stop()

    def test_explicit_pause_is_not_restored(self):
        self.engine.start(auto_reply=False)
        self.engine.stop()
        worker = self.restarted()
        worker.restore_listening()
        self.assertFalse(worker.enabled)
        self.assertFalse(worker.auto_reply)

    def test_setting_update_during_start_does_not_strand_startup(self):
        original = self.adapter.read
        def read():
            self.engine.settings_updated()
            return original()
        self.adapter.read = read
        self.engine.start(auto_reply=False)
        self.assertTrue(self.engine.enabled)
        self.assertFalse(self.engine.starting)
        self.assertFalse(self.engine.auto_reply)

    def test_restoration_does_not_adopt_another_wechat_chat(self):
        self.engine.start()
        worker = self.restarted(FakeWeChat(snap(msg('消息'), title='另一位联系人')))
        worker.restore_listening()
        self.assertFalse(worker.enabled)
        self.assertIn('开启失败', worker.status)
        self.generator.assert_not_called()

    def test_window_id_may_change_after_restart(self):
        self.engine.start(auto_reply=False)
        worker = self.restarted(FakeWeChat(snap(msg('原有消息'), wid=999)))
        worker.restore_listening()
        self.assertTrue(worker.enabled)
        self.assertEqual(worker.target[0], 999)

    def test_pause_before_restore_cancels_saved_session(self):
        self.engine.start()
        worker = self.restarted()
        worker.stop()
        worker.restore_listening()
        self.assertFalse(worker.enabled)

    def test_corrupt_saved_state_is_visible_and_safe(self):
        self.engine.session_path.write_text('{oops')
        self.engine.restore_listening()
        self.assertFalse(self.engine.enabled)
        self.assertIn('开启失败', self.engine.status)

    def test_each_room_restores_independently_and_removed_room_stays_off(self):
        self.cfg.save({'dingtalk_profile':'fake', 'dingtalk_rooms':[
            {'id':'a', 'name':'会话 A'}, {'id':'b', 'name':'会话 B'}]})
        pool = DingTalkPool(self.cfg)
        for cid, auto in [('a', True), ('b', False)]:
            worker=pool.get(cid)
            worker.adapter=FakeWeChat(snap(msg('旧消息'),title=cid))
            worker.start(auto_reply=auto)
        pool.get('b').stop()
        pool.stop('应用退出', preserve_session=True)
        restored=DingTalkPool(self.cfg)
        for cid in ('a','b'):
            worker=restored.get(cid)
            worker.adapter=FakeWeChat(snap(msg('旧消息'),title=cid))
            worker.restore_listening()
        self.assertTrue(restored.get('a').enabled)
        self.assertTrue(restored.get('a').auto_reply)
        self.assertFalse(restored.get('b').enabled)
        self.cfg.save({'dingtalk_rooms':[{'id':'b','name':'会话 B'}]})
        restored.sync()
        self.assertFalse(json.loads(pool.get('a').session_path.read_text())['enabled'])

    def test_activity_reports_generation_pending_sending_and_confirmed(self):
        self.engine.start(auto_reply=False)
        def generate(*args):
            self.assertEqual(self.engine.state()['activity']['phase'], 'generating')
            return '回复'
        self.engine.generator=generate
        self.adapter.snapshot=snap(msg('原有消息'),msg('新的问题'))
        self.engine.tick()
        self.assertEqual(self.engine.state()['activity']['phase'], 'pending')
        self.assertEqual(self.adapter.sent, [])
        original=self.adapter.send
        def send(*args):
            self.assertEqual(self.engine.state()['activity']['phase'], 'sending')
            return original(*args)
        self.adapter.send=send
        self.engine.send_pending(self.engine.pending['id'])
        self.assertEqual(self.engine.state()['activity']['phase'], 'sent')
        self.assertTrue(self.engine.state()['activity']['time'])
