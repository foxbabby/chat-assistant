import unittest
from unittest.mock import patch
from test_assistant import AssistantTests as Base, msg, snap
from cloud import Draft


class ManualReplyTests(unittest.TestCase):
    setUp = Base.setUp
    tearDown = Base.tearDown

    def draft(self, text='新消息'):
        self.engine.start(auto_reply=False)
        self.adapter.snapshot = snap(msg('原有消息'), msg(text))
        self.engine.tick()
        return self.engine.state()['latest']['pending_id']

    def test_manual_generates_without_writes_and_sends_once(self):
        token = self.draft()
        self.assertEqual(self.adapter.sent, [])
        self.assertEqual(self.engine.processed, set())
        self.assertEqual(self.engine.outbox, {})
        self.engine.tick()
        self.generator.assert_called_once()
        self.engine.send_pending(token)
        self.assertEqual(self.adapter.sent, ['知道啦，谢谢分享。'])
        self.assertEqual(self.engine.count, 1)
        with self.assertRaises(ValueError):
            self.engine.send_pending(token)

    def test_research_ack_is_not_sent_in_manual_mode(self):
        self.generator.supports_research_ack = True
        self.draft('需求计划有哪些类型')
        self.generator.assert_called_once()
        self.assertEqual(self.adapter.sent, [])

    def test_discard_continues_listening(self):
        token = self.draft()
        self.engine.send_pending(token, discard=True)
        self.engine.tick()
        self.assertTrue(self.engine.enabled)
        self.assertIsNone(self.engine.pending)
        self.assertEqual(self.adapter.sent, [])
        self.generator.assert_called_once()

    def test_new_message_invalidates_old_button(self):
        token = self.draft()
        self.adapter.snapshot['messages'].append(msg('更新的问题'))
        self.engine.tick()
        self.assertNotEqual(token, self.engine.latest['pending_id'])
        with self.assertRaises(ValueError):
            self.engine.send_pending(token)
        self.assertEqual(self.adapter.sent, [])

    def test_change_before_poll_is_rejected(self):
        token = self.draft()
        self.adapter.snapshot = snap(msg('另一聊天'), title='其他会话')
        with self.assertRaises(ValueError):
            self.engine.send_pending(token)
        self.assertEqual(self.adapter.sent, [])

    def test_pause_cancels_pending(self):
        token = self.draft()
        self.engine.stop()
        with self.assertRaises(ValueError):
            self.engine.send_pending(token)
        self.assertNotIn('pending_id', self.engine.latest)

    def test_images_wait_for_confirmation(self):
        answer = Draft('图片回复')
        answer.image_path = '/fake/sticker.png'
        self.generator.return_value = answer
        with patch('image_sender.send_image', return_value=snap(msg('图片', 'me'))) as send:
            token = self.draft()
            send.assert_not_called()
            self.engine.send_pending(token)
            send.assert_called_once()

    def test_unknown_delivery_is_not_retried(self):
        token = self.draft()
        self.adapter.send = lambda *args: (_ for _ in ()).throw(ValueError('结果不明'))
        with self.assertRaises(ValueError):
            self.engine.send_pending(token)
        self.assertTrue(self.engine.processed)
        with self.assertRaises(ValueError):
            self.engine.send_pending(token)

    def test_generation_cancelled_by_pause(self):
        self.generator.side_effect = lambda *args: (self.engine.stop(), '回复')[1]
        self.engine.start(auto_reply=False)
        self.adapter.snapshot = snap(msg('原有消息'), msg('新消息'))
        self.engine.tick()
        self.assertIsNone(self.engine.pending)
        self.assertEqual(self.adapter.sent, [])


del Base
