import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'src'))
from config import Config
from server import make_server
from test_assistant import FakeWeChat, snap, msg


class LocalAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.config=Config(Path(self.tmp.name))
        self.server=make_server(0,self.config,FakeWeChat(snap(msg('旧消息'))))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.client=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        self.client.request('GET','/')
        response=self.client.getresponse(); response.read()
        self.cookie=response.getheader('Set-Cookie').split(';')[0]
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.client.close(); self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()
    def request(self,path,data=None,origin=None,cookie=True):
        headers={'Cookie':self.cookie} if cookie else {}
        if data is not None:
            headers.update({'Content-Type':'application/json','Origin':origin or self.origin})
        self.client.request('GET' if data is None else 'POST',path,None if data is None else json.dumps(data),headers)
        response=self.client.getresponse()
        return response.status,json.loads(response.read())
    def test_settings_roundtrip_and_hidden_key(self):
        code,result=self.request('/api/settings',{'api_key':'test-placeholder','excluded_senders':['李四']})
        self.assertEqual(code,200); self.assertNotIn('api_key',result)
        code,result=self.request('/api/state')
        self.assertEqual(result['settings']['excluded_senders'],['李四'])
        self.assertTrue(result['settings']['has_key']); self.assertFalse(result['enabled'])
    def test_wrong_origin_rejected(self):
        code,result=self.request('/api/start',{},origin='https://untrusted.example')
        self.assertEqual(code,403)

    def test_reply_settings_keep_all_rooms_and_restart_modes(self):
        from unittest.mock import patch
        from engine import Engine
        self.config.save({'api_key':'fake-key', 'dingtalk_profile':'fake',
                          'dingtalk_rooms':[{'id':'a','name':'甲'}, {'id':'b','name':'乙'}]})
        pool = self.server.engines['dingtalk']
        pool.sync()
        workers = [self.server.engine, pool.get('a'), pool.get('b')]
        for index, worker in enumerate(workers):
            worker.adapter = FakeWeChat(snap(msg('旧消息'), title=str(index)))
            worker.generator = lambda *_: '旧规则草稿'
            worker.start(auto_reply=False)
            worker.adapter.snapshot = snap(msg('旧消息'), msg('新问题'), title=str(index))
            with patch('engine.time.sleep'):
                worker.tick()
            self.assertIsNotNone(worker.pending)
        workers[1].auto_reply = True
        workers[1].save_listening(True)
        workers[2].stop()
        baselines = [w.baseline for w in workers]
        code, _ = self.request('/api/settings', {'voice_profile':'新身份', 'style':'简洁专业'})
        self.assertEqual(code, 200)
        self.assertEqual([w.enabled for w in workers], [True, True, False])
        self.assertEqual([w.auto_reply for w in workers], [False, True, False])
        self.assertEqual([w.baseline for w in workers], baselines)
        for worker in workers:
            self.assertIsNone(worker.pending)
            worker.stop('应用退出', preserve_session=True)
        for index, worker in enumerate(workers):
            restored = Engine(worker.config, worker.adapter, worker.generator, worker.session_path.parent)
            restored.restore_listening()
            self.assertEqual(restored.enabled, index != 2)
            self.assertEqual(restored.auto_reply, index == 1)
            restored.tick()
            self.assertEqual(worker.adapter.sent, [])

    def test_settings_cancel_inflight_reply_and_next_message_uses_new_config(self):
        from unittest.mock import patch
        self.config.save({'api_key':'fake-key'})
        worker = self.server.engine
        worker.start()
        def old_generation(*_):
            self.assertEqual(self.request('/api/settings', {'voice_profile':'新的表达要求'})[0], 200)
            return '旧规则生成的回复'
        worker.generator = old_generation
        worker.adapter.snapshot = snap(msg('旧消息'), msg('问题一'))
        with patch('engine.time.sleep'):
            worker.tick()
        self.assertTrue(worker.enabled)
        self.assertEqual(worker.adapter.sent, [])
        def new_generation(config, _):
            self.assertEqual(config['voice_profile'], '新的表达要求')
            return '新规则生成的回复'
        worker.generator = new_generation
        worker.adapter.snapshot = snap(msg('旧消息'), msg('问题一'), msg('问题二'))
        with patch('engine.time.sleep'):
            worker.tick()
        self.assertEqual(worker.adapter.sent, ['新规则生成的回复'])

    def test_invalid_settings_do_not_interrupt_listener(self):
        self.config.save({'api_key':'fake-key'})
        self.server.engine.start()
        epoch = self.server.engine.epoch
        self.assertEqual(self.request('/api/settings', {'base_url':'http://bad'})[0], 400)
        self.assertTrue(self.server.engine.enabled)
        self.assertEqual(self.server.engine.epoch, epoch)

    def test_ding_batch_start_stop_and_platform_isolation(self):
        self.config.save({'api_key':'fake-key', 'dingtalk_profile':'fake',
                          'dingtalk_rooms':[{'id':'a','name':'甲'}, {'id':'b','name':'乙'}]})
        pool = self.server.engines['dingtalk']
        pool.sync()
        for worker in pool.workers.values():
            worker.adapter = FakeWeChat(snap(msg('旧消息')))
        self.server.engines['wechat'].start(auto_reply=False)
        for action, expected in [('start', 'started'), ('stop', 'stopped')]:
            path = '/api/dingtalk-'+action+'-selected'
            self.assertEqual(self.request(path, {'platform':'wechat'})[0], 400)
            self.assertEqual(self.request(path, {'platform':'dingtalk'}, cookie=False)[0], 403)
            self.assertEqual(self.request(path, {'platform':'dingtalk'}, origin='https://untrusted.example')[0], 403)
            code, result = self.request(path, {'platform':'dingtalk', 'conversation_id':'a', 'room_ids':['a','b']})
            self.assertEqual(code, 200)
            self.assertEqual([r['result'] for r in result['batch_results']], [expected, expected])
            self.assertTrue(self.server.engines['wechat'].enabled)
        self.assertFalse(any(w.adapter.sent for w in pool.workers.values()))
    def test_missing_cookie_rejected(self):
        self.assertEqual(self.request('/api/state',cookie=False)[0],403)
    def test_missing_key_clear_error(self):
        code,result=self.request('/api/start',{})
        self.assertEqual(code,400); self.assertIn('API Key',result['error'])
    def test_invalid_exclusion_rejected(self):
        self.assertEqual(self.request('/api/settings',{'excluded_senders':'李四'})[0],400)

    def test_self_names_roundtrip(self):
        code,result=self.request('/api/settings',{'self_names':['本人','群内昵称']})
        self.assertEqual(code,200);self.assertEqual(result['self_names'],['本人','群内昵称'])
        self.assertEqual(self.request('/api/state')[1]['settings']['self_names'],['本人','群内昵称'])
        self.assertEqual(self.request('/api/settings',{'self_names':'本人'})[0],400)

    def test_platform_switch_read_does_not_stop_other_engine(self):
        self.config.save({'api_key':'fake-key'})
        self.server.engines['wechat'].start()
        code,state=self.request('/api/state?platform=dingtalk')
        self.assertEqual(code,200);self.assertEqual(state['platform'],'dingtalk')
        self.assertTrue(state['platforms']['wechat']['enabled'])
        self.request('/api/stop',{'platform':'dingtalk'})
        self.assertTrue(self.server.engines['wechat'].enabled)

    def test_ding_only_settings_leave_wechat_running(self):
        self.config.save({'api_key':'fake-key'})
        self.server.engines['wechat'].start()
        code,_=self.request('/api/settings',{'dingtalk_interval':30})
        self.assertEqual(code,200);self.assertTrue(self.server.engines['wechat'].enabled)

    def test_unknown_platform_rejected(self):
        self.assertEqual(self.request('/api/start',{'platform':'unknown'})[0],400)
        self.assertEqual(self.request('/api/state?platform=unknown')[0],400)

    def test_ding_requires_explicit_target(self):
        self.config.save({'api_key':'fake-key'})
        code,result=self.request('/api/start',{'platform':'dingtalk'})
        self.assertEqual(code,400);self.assertIn('选择监听会话',result['error'])

    def test_profile_change_clears_old_target(self):
        self.config.save({'dingtalk_profile':'old','dingtalk_conversation':'old-cid'})
        code,result=self.request('/api/settings',{'dingtalk_profile':'new'})
        self.assertEqual(code,200);self.assertEqual(result['dingtalk_conversation'],'')

    def test_dynamic_style_preserves_listening(self):
        self.config.save({'api_key':'fake-key'})
        self.server.engines['wechat'].start()
        before=self.server.engines['wechat'].epoch
        code,_=self.request('/api/settings',{'style':'温柔耐心'})
        self.assertEqual(code,200)
        self.assertTrue(self.server.engines['wechat'].enabled)
        self.assertEqual(before+1,self.server.engines['wechat'].epoch)
        self.assertEqual(self.config.data['style'],'温柔耐心')

    def test_manual_reply_both_platforms(self):
        from unittest.mock import patch
        self.config.save({'api_key':'fake-key'})
        for platform in ('wechat', 'dingtalk'):
            worker = self.server.engines[platform]
            if platform == 'dingtalk':
                worker = worker.get()
            worker.adapter = FakeWeChat(snap(msg('旧消息')))
            worker.generator = lambda *args: '人工确认的回复'
            code, state = self.request('/api/start', {'platform':platform, 'auto_reply':False})
            self.assertEqual(code, 200)
            self.assertFalse(state['auto_reply'])
            worker.adapter.snapshot = snap(msg('旧消息'), msg('新消息'))
            with patch('engine.time.sleep'):
                worker.tick()
            self.assertEqual(worker.adapter.sent, [])
            token = worker.latest['pending_id']
            other = 'dingtalk' if platform == 'wechat' else 'wechat'
            self.assertEqual(self.request('/api/send-reply', {'platform':other, 'pending_id':token})[0], 400)
            code, state = self.request('/api/send-reply', {'platform':platform, 'pending_id':token})
            self.assertEqual(code, 200)
            self.assertEqual(state['latest']['state'], '已发送并确认')
            self.assertEqual(worker.adapter.sent, ['人工确认的回复'])
            self.assertEqual(self.request('/api/send-reply', {'platform':platform, 'pending_id':token})[0], 400)

    def test_auto_reply_default_and_validation(self):
        self.config.save({'api_key':'fake-key'})
        self.assertEqual(self.request('/api/start', {'auto_reply':'false'})[0], 400)
        code, state = self.request('/api/start', {})
        self.assertEqual(code, 200)
        self.assertTrue(state['auto_reply'])

    def test_reply_latest_requires_auto_reply_for_single_and_batch(self):
        self.config.save({'api_key':'fake-key', 'dingtalk_profile':'fake',
                          'dingtalk_rooms':[{'id':'a','name':'甲'}, {'id':'b','name':'乙'}]})
        pool = self.server.engines['dingtalk']; pool.sync()
        for worker in pool.workers.values(): worker.adapter = FakeWeChat(snap(msg('旧消息')))
        for auto, latest in [(True, True), (True, False), (False, True)]:
            for platform in ('wechat','dingtalk'):
                args = {'platform':platform, 'conversation_id':'a' if platform=='dingtalk' else '',
                        'auto_reply':auto, 'reply_latest':latest}
                self.request('/api/stop', args)
                code, result = self.request('/api/start', args)
                self.assertEqual(code, 200)
                worker = pool.get('a') if platform=='dingtalk' else self.server.engines['wechat']
                self.assertEqual(worker.initial_tick, auto and latest)
            pool.stop_all()
            code, result = self.request('/api/dingtalk-start-selected',
                {'platform':'dingtalk','room_ids':['a','b'],'auto_reply':auto,'reply_latest':latest})
            self.assertEqual(code, 200)
            for worker in pool.workers.values():
                self.assertEqual(worker.auto_reply, auto)
                self.assertEqual(worker.initial_tick, auto and latest)
                self.assertFalse(worker.adapter.sent)
        self.assertEqual(self.request('/api/dingtalk-start-selected',
            {'platform':'dingtalk','room_ids':['a'],'auto_reply':'true'})[0], 400)

    def test_preview_shows_same_review_reply_without_sending(self):
        from unittest.mock import patch
        from cloud import NeedsReview, review_clarification
        question = 'spd有几种拆单方式'
        error = NeedsReview('资料不足以支持本次答案；待人工确认，继续监听')
        with patch('server.reply', side_effect=error):
            code, result = self.request('/api/preview', {'text': question})
        self.assertEqual(code, 200)
        expected = review_clarification(question, error)
        self.assertEqual(result['reply'], expected)
        self.assertEqual(result['warnings'], expected.warnings)
        self.assertEqual(result['sources'], [])
        self.assertFalse(self.server.engines['wechat'].enabled)
        self.assertEqual(self.server.engines['wechat'].adapter.sent, [])

    def test_multi_room_api_selection_and_removal_are_isolated(self):
        from unittest.mock import patch
        rooms = [{'id':'a','name':'Alpha'}, {'id':'b','name':'Beta'}]
        self.config.save({'api_key':'fake-key','dingtalk_profile':'test:profile'})
        with patch('dingtalk.conversations', return_value={'conversations':rooms,'complete':True}):
            code, state = self.request('/api/dingtalk-rooms', {'platform':'dingtalk','rooms':rooms})
        self.assertEqual(code,200)
        self.assertEqual(len(state['rooms']),2)
        pool = self.server.engines['dingtalk']
        for cid in ('a','b'):
            worker = pool.get(cid)
            worker.adapter = FakeWeChat(snap(msg('旧消息')))
            self.assertEqual(self.request('/api/start', {'platform':'dingtalk','conversation_id':cid,'auto_reply':False})[0],200)
        state = self.request('/api/state?platform=dingtalk&conversation_id=b')[1]
        self.assertEqual(state['conversation_id'],'b')
        self.assertTrue(pool.get('a').enabled)
        with patch('dingtalk.conversations') as listing:
            code, state = self.request('/api/dingtalk-rooms', {'platform':'dingtalk','conversation_id':'a','rooms':[rooms[1]]})
            listing.assert_not_called()
        self.assertEqual(code,200)
        self.assertEqual(state['conversation_id'],'b')
        self.assertTrue(pool.get('b').enabled)
        self.assertEqual(self.request('/api/stop', {'platform':'dingtalk','conversation_id':'a', 'room_ids':['a','b']})[0],400)
        self.assertTrue(pool.get('b').enabled)
        self.assertFalse(pool.get('b').adapter.sent)

    def test_room_management_rejects_unknown_targets_and_settings_bypass(self):
        from unittest.mock import patch
        with patch('dingtalk.conversations', return_value={'conversations':[],'complete':True}):
            self.assertEqual(self.request('/api/dingtalk-rooms',{'rooms':[{'id':'unknown','name':'X'}]})[0],400)
        self.assertEqual(self.request('/api/settings',{'dingtalk_rooms':[{'id':'unknown','name':'X'}]})[0],400)
        self.assertEqual(self.config.data['dingtalk_rooms'],[])
