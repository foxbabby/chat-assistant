import unittest
from unittest.mock import patch
from test_assistant import AssistantTests as Base, msg, snap
from cloud import Draft
from stickers import choose
from image_sender import image_in_editor

class StickerTests(unittest.TestCase):
    setUp=Base.setUp
    tearDown=Base.tearDown
    def test_unknown_picture_uses_neutral_phrase(self):
        self.assertIn(choose('自然友好','图片'),['收到','好呀'])
    def test_description_guides_selection(self):
        self.assertEqual(choose('自然友好','动画表情 [累哭了]'),'辛苦了')
    def test_image_reply_and_echo_do_not_loop(self):
        self.engine.start()
        incoming=msg('动画表情 [哈哈]',side='unknown');incoming.kind='sticker'
        self.adapter.snapshot=snap(msg('原有消息'),incoming)
        output=msg('图片',side='unknown');output.kind='image'
        sent=snap(msg('原有消息'),incoming,output)
        draft=Draft('[表情图片] 哈哈');draft.image_path='/unused.png'
        with patch('stickers.reply_card',return_value=draft),patch('image_sender.send_image',return_value=sent) as send:
            self.engine.tick();self.adapter.snapshot=sent;self.engine.tick()
            send.assert_called_once();self.generator.assert_not_called()
            self.assertTrue(self.engine.sent_by_assistant(sent,'图片'))
            newer=snap(*sent['messages'],msg('图片',side='unknown'))
            self.assertFalse(self.engine.sent_by_assistant(newer,'图片'))
    def test_failed_image_keeps_pending_reservation(self):
        incoming=msg('图片');incoming.kind='image';self.adapter.snapshot=snap(incoming)
        self.engine.start(reply_latest=True)
        draft=Draft('[表情图片] 收到');draft.image_path='/unused.png'
        with patch('stickers.reply_card',return_value=draft),patch('image_sender.send_image',side_effect=ValueError('未确认')):
            self.engine.tick()
        self.assertFalse(self.engine.enabled)
        self.assertTrue(any(k.startswith('image-pending:') for k in self.engine.outbox))
    def test_plain_editor_never_counts_as_image(self):
        with patch('fill._ax_value',return_value='普通文字'),patch('fill._ax_attr',return_value=[]):
            self.assertFalse(image_in_editor(object()))

del Base
