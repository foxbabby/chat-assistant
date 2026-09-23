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
        self.assertEqual(before,self.server.engines['wechat'].epoch)
        self.assertEqual(self.config.data['style'],'温柔耐心')
