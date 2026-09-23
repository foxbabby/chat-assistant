import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'src'))
from ding_stream import MessageStream
from ding_images import describe
from cloud import NeedsReview
from config import Config

class StreamTests(unittest.TestCase):
    def test_scope_and_duplicate_filter(self):
        s=MessageStream([], 'cid')
        for event in ({'conversation_id':'other','message_id':'1'}, {}, [], {'conversation_id':'cid'}):
            s.accept(event)
        self.assertFalse(s.poll())
        s.accept({'conversation_id':'cid','message_id':'1'})
        self.assertTrue(s.poll());self.assertFalse(s.poll())
        s.accept({'conversation_id':'cid','message_id':'1'})
        self.assertFalse(s.poll())
        s.accept({'conversation_id':'cid','message_id':'2'})
        self.assertTrue(s.poll())
    def test_disconnect_never_resubscribes(self):
        s=MessageStream([], 'cid');s.error='断开'
        with self.assertRaises(ValueError):s.poll()
        self.assertIsNone(s.process)
    def test_close_uses_term(self):
        s=MessageStream([], 'cid');s.process=Mock();s.process.poll.return_value=None
        s.close();s.process.terminate.assert_called_once();s.process.kill.assert_not_called()

class ImagesTests(unittest.TestCase):
    def snapshot(self):
        return {'profile':'test', 'conversation_id':'cid','messages':[SimpleNamespace(message_id='mid')]}
    def downloader(self,args,profile,cwd=None):
        self.root=Path(cwd)
        p=self.root/'image.png';p.write_bytes(b'\x89PNG\r\n\x1a\n' + b'test')
        return {'messages':[{'messageId':'mid','conversationId':'cid'}], 'resourceDownloads':{'ok':True,'downloads':[{'messageId':'mid','localPath':str(p)}]}}
    def test_ocr_and_cleanup(self):
        with patch('perception.ocr',return_value=[SimpleNamespace(text='库存不足',conf=1,y=.5,x=.1)]):
            result=describe(self.snapshot(),{},self.downloader)
        self.assertIn('库存不足',result);self.assertFalse(self.root.exists())
    def test_no_text_never_guesses(self):
        with patch('perception.ocr',return_value=[]):
            with self.assertRaises(NeedsReview):describe(self.snapshot(),{},self.downloader)
    def test_cloud_receives_image_not_path(self):
        cfg={'dingtalk_image_mode':'cloud','dingtalk_vision_url':'https://example.com/v1','dingtalk_vision_model':'vision','dingtalk_vision_key':'fake'}
        with patch('ding_images.completion',return_value='一只笑脸猫') as completion:
            result=describe(self.snapshot(),cfg,self.downloader)
        self.assertIn('笑脸猫',result)
        self.assertTrue(completion.call_args.args[1][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
    def test_wrong_conversation_no_recognition(self):
        def download(*a,**kw):
            d=self.downloader(*a,**kw);d['messages'][0]['conversationId']='wrong';return d
        with patch('ding_images.completion') as c:
            with self.assertRaises(NeedsReview):describe(self.snapshot(),{},download)
            c.assert_not_called()
    def test_download_failure_not_used(self):
        with self.assertRaises(NeedsReview):
            describe(self.snapshot(),{},lambda *a,**kw:{'messages':[{'messageId':'mid','conversationId':'cid'}],'resourceDownloads':{'ok':False}})
    def test_vision_key_not_public_and_endpoint_guard(self):
        with tempfile.TemporaryDirectory() as d:
            c=Config(Path(d));c.save({'dingtalk_vision_url':'https://example.com/v1','dingtalk_vision_key':'fake'})
            self.assertNotIn('dingtalk_vision_key',c.public());self.assertTrue(c.public()['has_vision_key'])
            c.save({'dingtalk_vision_key':''});self.assertEqual(c.data['dingtalk_vision_key'],'fake')
            with self.assertRaises(ValueError):c.save({'dingtalk_vision_url':'https://another.example/v1'})
