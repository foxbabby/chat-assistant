import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from cloud import reply, NeedsReview, work_query, evidence_answer
from config import DEFAULTS, Config
from knowledge import select_passages, retrieve
from test_assistant import FakeWeChat, snap, msg
from engine import Engine

class VoiceKnowledgeTests(unittest.TestCase):
    def test_new_social_topic_does_not_inherit_spd_gate(self):
        for question in ('今天中午去哪吃', '富卓还是楼下'):
            with self.subTest(question=question):
                messages = [msg('新建需求计划，耗材查询不出来，是什么原因'), msg(question)]
                with patch('knowledge.retrieve', return_value=([], [])) as retrieve_mock, patch('cloud.completion', return_value='你想在哪边？') as generate:
                    draft=reply(DEFAULTS, messages)
                    self.assertTrue(draft)
                    self.assertEqual(retrieve_mock.call_args.args[1], question)
                    if generate.called:
                        self.assertNotIn('请仅输出 JSON', generate.call_args.args[1][0]['content'])

    def test_work_followup_keeps_topic_but_new_spd_query_uses_latest(self):
        self.assertTrue(work_query([msg('SPD库存不对'), msg('那怎么处理？')])[1])
        q='新建需求计划，耗材查询不出来，是什么原因'
        self.assertEqual(work_query([msg('库存问题'),msg(q)]), (q,True))

    def test_fenced_json_and_failure_reasons(self):
        evidence=[{'id':'1','source':'经验库','text':'已核对的资料'}]
        payload=json.dumps({'answer':'可以先核对目录。','supported':True,'evidence_ids':['1']})
        self.assertEqual(evidence_answer('```json\n'+payload+'\n```',evidence,[])[0],'可以先核对目录。')
        for raw, expected in [('非JSON','格式异常'), ('[]','格式异常'),
                              ('{"supported":false}', '字典暂不可用'),
                              ('{"supported":true,"answer":"猜测","evidence_ids":["9"]}', '有效的答案依据')]:
            with self.assertRaisesRegex(NeedsReview, expected):
                evidence_answer(raw,evidence,['字典暂不可用'])

    def test_voice_and_examples_enter_prompt(self):
        cfg = {**DEFAULTS, 'voice_profile': '先说结论', 'reply_examples': '可以，那就这样。'}
        with patch('knowledge.retrieve', return_value=([], [])), patch('cloud.completion', return_value='可以，那就这样。') as generate:
            self.assertEqual(reply(cfg, [msg('这样可以吗')]), '可以，那就这样。')
            prompt = generate.call_args.args[1][0]['content']
            self.assertIn('先说结论', prompt); self.assertIn('可以，那就这样。', prompt)
            self.assertIn('不自称机器人', prompt)
    def test_spd_without_evidence_never_generates(self):
        with patch('knowledge.retrieve', return_value=([], [])), patch('cloud.completion') as generate:
            draft=reply(DEFAULTS, [msg('SPD库存不对')])
            self.assertIn('具体在哪一步',draft)
            self.assertEqual(draft.sources,[])
            generate.assert_not_called()
    def test_spd_requires_supported_valid_evidence(self):
        evidence = [{'id':'1','source':'经验库','text':'库存批次时间'}]
        for answer in ('随便一个答案', '{"answer":"已修复","supported":true,"evidence_ids":["9"]}'):
            with patch('knowledge.retrieve', return_value=(evidence, [])), patch('cloud.completion', return_value=answer):
                with self.assertRaises(NeedsReview): reply(DEFAULTS, [msg('库存不对')])
    def test_unsupported_cause_is_replaced_with_observation_question(self):
        with patch('knowledge.retrieve', return_value=([{'id':'1','source':'经验库','text':'不相关'}],['报错排查字典暂不可用'])), patch('cloud.completion', return_value='{"answer":"一定是目录停用","supported":false,"evidence_ids":[]}'):
            draft=reply(DEFAULTS,[msg('新建需求计划，耗材查询不出来，是什么原因')])
            self.assertIn('是所有耗材都查不到',draft)
            self.assertNotIn('停用',draft)
            self.assertEqual(draft.sources,[])
            self.assertIn('报错排查字典暂不可用',draft.warnings)
    def test_spd_returns_plain_text_and_source(self):
        with patch('knowledge.retrieve', return_value=([{'id':'1','source':'经验库','text':'库存批次时间'}], [])), patch('cloud.completion', return_value=json.dumps({'answer':'这两处统计口径不同。','supported':True,'evidence_ids':['1']})):
            draft = reply(DEFAULTS, [msg('库存为什么不一致')])
            self.assertEqual(draft, '这两处统计口径不同。');self.assertEqual(draft.sources,['经验库'])
    def test_personal_profile_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Config(Path(tmp));cfg.save({'voice_profile':'直接说重点','reply_examples':'可以','work_knowledge':'库存案例','spd_knowledge':'disabled'})
            self.assertEqual(Config(Path(tmp)).data['voice_profile'],'直接说重点')
    def test_no_remote_retrieval_for_personal_chat(self):
        with patch('knowledge.module_evidence') as source:
            retrieve(DEFAULTS,'今天有点累');source.assert_not_called()
    def test_unrelated_memory_is_not_retrieved(self):
        self.assertEqual(select_passages('私人家庭信息', '库存数据异常'), [])
    def test_needs_review_sends_clarification_and_keeps_listening(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Config(Path(tmp));cfg.save({'api_key':'placeholder'})
            adapter=FakeWeChat(snap(msg('旧消息')))
            engine=Engine(cfg,adapter,lambda *_: (_ for _ in ()).throw(NeedsReview('待人工确认')),Path(tmp))
            engine.start();adapter.snapshot=snap(msg('旧消息'),msg('库存异常'))
            with patch('engine.time.sleep'): engine.tick()
            self.assertTrue(engine.enabled);self.assertTrue(adapter.sent)
            self.assertIn('具体在哪一步',str(adapter.sent[0]))
            self.assertEqual(engine.latest['state'],'已发送并确认')

    def test_robot_identity_output_is_blocked(self):
        with patch('knowledge.retrieve', return_value=([], [])), patch('cloud.completion', return_value='对，就是机器人本机。'):
            with self.assertRaises(NeedsReview): reply(DEFAULTS, [msg('机器人？')])

    def test_media_and_uncertain_answers_reply_once(self):
        for kind in ('text','image','file'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                cfg=Config(Path(tmp));cfg.save({'api_key':'placeholder'})
                adapter=FakeWeChat(snap(msg('旧消息')))
                adapter.text_media_reply=True
                engine=Engine(cfg,adapter,lambda *_: (_ for _ in ()).throw(NeedsReview('格式异常')),Path(tmp))
                engine.start()
                incoming=msg('新的消息');incoming.kind=kind
                adapter.snapshot=snap(msg('旧消息'),incoming)
                with patch('engine.time.sleep'):
                    engine.tick();engine.tick()
                self.assertEqual(len(adapter.sent),1)
                self.assertTrue(engine.enabled)
                self.assertNotIn('格式异常',str(adapter.sent[0]))
