import unittest
from unittest.mock import Mock, patch
from test_assistant import msg, snap
from wechat import WeChat

class ForegroundTests(unittest.TestCase):
    def setUp(self):
        self.adapter=WeChat();self.expected=snap(msg('消息'))
        self.app=Mock();self.app.isActive.return_value=False
        self.read=patch.object(self.adapter,'read',return_value=self.expected).start()
        patch('fill._wechat_app',return_value=self.app).start()
        self.launch=patch('wechat.subprocess.run').start()
        patch('wechat.time.sleep').start()
    def tearDown(self):patch.stopall()
    def test_background_activates_once(self):
        self.app.isActive.side_effect=[False,False,True]
        self.assertIs(self.adapter.focus_for_send(self.expected,lambda:True),self.app)
        self.launch.assert_called_once()
    def test_already_foreground_does_not_activate(self):
        self.app.isActive.return_value=True
        self.adapter.focus_for_send(self.expected,lambda:True);self.launch.assert_not_called()
    def test_switched_chat_never_activated(self):
        self.read.return_value=snap(msg('消息'),title='另一会话')
        with self.assertRaisesRegex(ValueError,'已变化'):self.adapter.focus_for_send(self.expected,lambda:True)
        self.launch.assert_not_called()
    def test_stopped_never_activated(self):
        with self.assertRaisesRegex(ValueError,'已暂停'):self.adapter.focus_for_send(self.expected,lambda:False)
        self.launch.assert_not_called()
    def test_activation_failure_never_claims_ready(self):
        with self.assertRaisesRegex(ValueError,'未能切到前台'):self.adapter.focus_for_send(self.expected,lambda:True)
        self.launch.assert_called_once()
