import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from wechat import WeChat
from test_assistant import snap, msg


class SendGuardTests(unittest.TestCase):
    def setUp(self):
        self.wechat=WeChat()
        self.snapshot=snap(msg('新消息'))
        self.app=Mock(); self.app.isActive.return_value=True
        self.read=patch.object(self.wechat,'read',return_value=self.snapshot).start()
        patch('fill._wechat_app',return_value=self.app).start()
        self.focus=patch.object(self.wechat,'focus_for_send',return_value=self.app).start()
        patch('fill.has_accessibility',return_value=True).start()
        patch('fill._find_input_box',return_value=object()).start()
        self.value=patch('fill._ax_value',side_effect=['','回复','回复','']).start()
        self.write=patch('fill._ax_set_value',return_value=True).start()
        patch('fill._ax_attr',return_value=True).start()
        patch('wechat.AX.AXUIElementSetAttributeValue').start()
        patch('wechat.Quartz.CGEventCreateKeyboardEvent').start()
        self.post=patch('wechat.Quartz.CGEventPostToPid').start()
        patch('wechat.time.sleep').start()
    def tearDown(self):
        patch.stopall()
    def test_background_wechat_never_written(self):
        self.focus.side_effect=ValueError('微信未能切到前台')
        with self.assertRaisesRegex(ValueError,'未能切到前台'):
            self.wechat.send('回复',self.snapshot,lambda:True)
        self.write.assert_not_called(); self.post.assert_not_called()
    def test_existing_draft_never_written_or_sent(self):
        self.value.side_effect=None; self.value.return_value='用户草稿'
        with self.assertRaisesRegex(ValueError,'已有内容'):
            self.wechat.send('回复',self.snapshot,lambda:True)
        self.write.assert_not_called(); self.post.assert_not_called()
    def test_changed_chat_never_written(self):
        self.read.return_value=snap(msg('另一条'),title='另一会话')
        with self.assertRaisesRegex(ValueError,'已变化'):
            self.wechat.send('回复',self.snapshot,lambda:True)
        self.write.assert_not_called(); self.post.assert_not_called()
    def test_cancel_after_fill_never_sends(self):
        allowed=Mock(side_effect=[True,True,False])
        with self.assertRaisesRegex(ValueError,'未发送'):
            self.wechat.send('回复',self.snapshot,allowed)
        self.write.assert_called_once(); self.post.assert_not_called()
    def test_empty_editor_alone_not_success(self):
        self.value.side_effect=None; self.value.return_value=''
        # Read-back matches for the pre-send checks, but no outgoing bubble ever appears.
        self.value.side_effect=['','回复','回复']
        with self.assertRaisesRegex(ValueError,'未确认消息气泡'):
            self.wechat.send('回复',self.snapshot,lambda:True)
        self.assertEqual(self.post.call_count,2) # One down/up pair, zero retries.
    def test_confirmed_outgoing_bubble(self):
        sent=snap(msg('新消息'),msg('回复','me'))
        self.read.side_effect=[self.snapshot,self.snapshot,sent]
        result=self.wechat.send('回复',self.snapshot,lambda:True)
        self.assertEqual(result,sent); self.assertEqual(self.post.call_count,2)

    def test_unknown_side_send_confirmed_from_append_and_empty_editor(self):
        sent=snap(msg('新消息'),msg('回复','unknown',sender=None))
        self.read.side_effect=[self.snapshot,self.snapshot,sent]
        self.assertEqual(self.wechat.send('回复',self.snapshot,lambda:True),sent)
        self.assertEqual(self.post.call_count,2)
