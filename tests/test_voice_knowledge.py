import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from cloud import reply, NeedsReview
from config import DEFAULTS, Config
from knowledge import select_passages, retrieve
from test_assistant import FakeWeChat, snap, msg
from engine import Engine

class VoiceKnowledgeTests(unittest.TestCase):
    def test_voice_and_examples_enter_prompt(self):
        cfg = {**DEFAULTS, 'voice_profile': '先说结论', 'reply_examples': '可以，那就这样。'}
        with patch('knowledge.retrieve', return_value=([], [])), patch('cloud.completion', return_value='可以，那就这样。') as generate:
            self.assertEqual(reply(cfg, [msg('这样可以吗')]), '可以，那就这样。')
            prompt = generate.call_args.args[1][0]['content']
            self.assertIn('先说结论', prompt); self.assertIn('可以，那就这样。', prompt)
            self.assertIn('不自称机器人', prompt)
    def test_spd_without_evidence_never_generates(self):
        with patch('knowledge.retrieve', return_value=([], [])), patch('cloud.completion') as generate:
            with self.assertRaises(NeedsReview): reply(DEFAULTS, [msg('SPD库存不对')])
            generate.assert_not_called()
    def test_spd_requires_supported_valid_evidence(self):
        evidence = [{'id':'1','source':'经验库','text':'库存批次时间'}]
        for answer in ('随便一个答案', '{"answer":"已修复","supported":true,"evidence_ids":["9"]}', '{"answer":"","supported":false,"evidence_ids":[]}'):
            with patch('knowledge.retrieve', return_value=(evidence, [])), patch('cloud.completion', return_value=answer):
                with self.assertRaises(NeedsReview): reply(DEFAULTS, [msg('库存不对')])
    def test_spd_returns_plain_text_and_source(self):
        with patch('knowledge.retrieve', return_value=([{'id':'1','source':'经验库','text':'库存批次时间'}], [])), patch('cloud.completion', return_value=json.dumps({'answer':'这两处统计口径不同。','supported':True,'evidence_ids':['1']})):
            draft = reply(DEFAULTS, [msg('库存为什么不一致')])
            self.assertEqual(draft, '这两处统计口径不同。');self.assertEqual(draft.sources,['经验库'])
    def test_personal_profile_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Config(Path(tmp));cfg.save({'voice_profile':'直接说重点','reply_examples':'可以','work_knowledge':'库存案例','spd_knowledge':'disabled'})
            self.assertEqual(Config(Path(tmp)).data['voice_profile'],'直接说重点')
    def test_no_remote_retrieval_for_personal_chat(self):
        with patch('subprocess.run') as remote:
            retrieve(DEFAULTS,'今天有点累');remote.assert_not_called()
    def test_unrelated_memory_is_not_retrieved(self):
        self.assertEqual(select_passages('私人家庭信息', '库存数据异常'), [])
    def test_needs_review_keeps_listening_without_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Config(Path(tmp));cfg.save({'api_key':'placeholder'})
            adapter=FakeWeChat(snap(msg('旧消息')))
            engine=Engine(cfg,adapter,lambda *_: (_ for _ in ()).throw(NeedsReview('待人工确认')),Path(tmp))
            engine.start();adapter.snapshot=snap(msg('旧消息'),msg('库存异常'))
            with patch('engine.time.sleep'): engine.tick()
            self.assertTrue(engine.enabled);self.assertFalse(adapter.sent)
            self.assertIn('待人工确认',engine.latest['state'])

    def test_robot_identity_output_is_blocked(self):
        with patch('knowledge.retrieve', return_value=([], [])), patch('cloud.completion', return_value='对，就是机器人本机。'):
            with self.assertRaises(NeedsReview): reply(DEFAULTS, [msg('机器人？')])
