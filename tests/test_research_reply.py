import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
from engine import Engine
from config import Config
from cloud import CloudError
from test_assistant import FakeWeChat,snap,msg

class ResearchReplyTests(unittest.TestCase):
 def run_case(self,mode):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=Config(Path(tmp));cfg.save({'api_key':'placeholder','spd_knowledge':'enabled'})
   adapter=FakeWeChat(snap(msg('旧消息')))
   def generate(*args):
    self.assertEqual(adapter.sent,['我查一下相关资料，稍等。'])
    if mode=='fail':raise CloudError('云端超时')
    if mode=='switch':adapter.snapshot=snap(msg('其他消息'),title='另一会话')
    if mode=='pause':engine.stop('用户暂停')
    return '已确认的规则。'
   generate.supports_research_ack=True
   original_send=adapter.send
   def guarded(text,expected,allowed):
    from wechat import identity,signature
    if identity(adapter.snapshot)!=identity(expected) or signature(adapter.snapshot)!=signature(expected):
     raise ValueError('会话或消息已变化')
    return original_send(text,expected,allowed)
   adapter.send=guarded
   engine=Engine(cfg,adapter,generate,Path(tmp));engine.start()
   incoming=snap(msg('旧消息'),msg('需求计划有哪些类型'));adapter.snapshot=incoming
   with patch('engine.time.sleep'):
    engine.tick();engine.tick()
   if mode in ('switch','pause'):
    self.assertEqual(len(adapter.sent),1);self.assertFalse(engine.enabled)
   else:
    self.assertEqual(len(adapter.sent),2)
    self.assertNotEqual(adapter.sent[0],adapter.sent[1])
    self.assertTrue(engine.enabled)
    if mode=='fail':self.assertIn('查询暂时没完成',adapter.sent[1])
   return adapter.sent
 def test_ack_then_answer_without_self_reply(self):self.run_case('ok')
 def test_failure_is_followed_by_explanation(self):self.run_case('fail')
 def test_changed_conversation_cancels_answer(self):self.run_case('switch')
 def test_pause_cancels_answer(self):self.run_case('pause')
 def test_uncertain_ack_delivery_never_starts_lookup(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=Config(Path(tmp));cfg.save({'api_key':'placeholder','spd_knowledge':'enabled'})
   adapter=FakeWeChat(snap(msg('旧消息')))
   def generate(*args):self.fail('must not query after unknown delivery')
   generate.supports_research_ack=True
   def fail(*args):raise ValueError('未确认投递')
   adapter.send=fail
   engine=Engine(cfg,adapter,generate,Path(tmp));engine.start()
   adapter.snapshot=snap(msg('旧消息'),msg('需求计划有哪些类型'))
   with patch('engine.time.sleep'):engine.tick()
   self.assertFalse(engine.enabled)
