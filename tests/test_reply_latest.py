import unittest
from test_assistant import AssistantTests as Base, msg, snap

class ReplyLatestTests(unittest.TestCase):
    setUp = Base.setUp
    tearDown = Base.tearDown
    def test_default_does_not_reply_history(self):
        self.engine.start();self.engine.tick();self.generator.assert_not_called()
    def test_checked_replies_history_once(self):
        self.engine.start(reply_latest=True);self.engine.tick();self.engine.tick()
        self.generator.assert_called_once();self.assertEqual(len(self.adapter.sent),1)
    def test_checked_never_replies_excluded(self):
        self.adapter.snapshot=snap(msg('旧消息',sender='李四'))
        self.engine.start(reply_latest=True);self.engine.tick();self.generator.assert_not_called()
    def test_checked_never_replies_own_echo(self):
        self.engine.reserve_outgoing(self.adapter.snapshot,'原有消息')
        self.engine.start(reply_latest=True);self.engine.tick();self.generator.assert_not_called()

del Base
