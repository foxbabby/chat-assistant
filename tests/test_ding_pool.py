import tempfile
import sys
import copy
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'src'))
from config import Config, validate, DEFAULTS
from ding_pool import DingTalkPool
from test_assistant import FakeWeChat, snap, msg


class MultiRoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.tmp.name))
        self.config.save({'api_key':'test-only', 'dingtalk_profile':'fake:profile',
                          'dingtalk_rooms':[{'id':'room-a','name':'会话甲'}, {'id':'room-b','name':'会话乙'}]})
        self.pool = DingTalkPool(self.config)
        for cid in ('room-a','room-b'):
            worker = self.pool.get(cid)
            worker.adapter = FakeWeChat(snap(msg('旧消息')))
            worker.generator = lambda config, messages: config['dingtalk_conversation']+' 的回复'
    def tearDown(self):
        self.pool.stop()
        self.tmp.cleanup()
    def test_batch_start_manual_only_and_pause_all(self):
        results = self.pool.start_all()
        self.assertEqual([r['result'] for r in results], ['started', 'started'])
        for worker in self.pool.workers.values():
            self.assertTrue(worker.enabled)
            self.assertFalse(worker.auto_reply)
            self.assertFalse(worker.initial_tick)
            self.assertFalse(worker.adapter.sent)
        a = self.pool.get('room-a')
        a.adapter.snapshot = snap(msg('旧消息'), msg('新的消息'))
        with patch('engine.time.sleep'): a.tick()
        pending = a.pending['id']
        epoch = a.epoch
        self.assertTrue(all(r['result']=='skipped' for r in self.pool.start_all()))
        self.assertEqual(a.epoch, epoch)
        self.assertEqual(a.pending['id'], pending)
        self.assertTrue(all(r['result']=='stopped' for r in self.pool.stop_all()))
        for worker in self.pool.workers.values():
            self.assertFalse(worker.enabled or worker.starting)
            self.assertIsNone(worker.pending)
            self.assertFalse(worker.adapter.sent)
        self.assertTrue(all(r['result']=='skipped' for r in self.pool.stop_all()))

    def test_batch_start_partial_failure_can_retry_without_restarting_success(self):
        a, b = self.pool.get('room-a'), self.pool.get('room-b')
        with patch.object(a, 'start', side_effect=ValueError('连接失败')):
            results = self.pool.start_all()
        self.assertEqual([r['result'] for r in results], ['failed', 'started'])
        epoch = b.epoch
        self.assertEqual([r['result'] for r in self.pool.start_all()], ['started', 'skipped'])
        self.assertEqual(b.epoch, epoch)

    def test_batch_empty_and_starting_rooms(self):
        a = self.pool.get('room-a')
        a.starting = True
        self.assertEqual(self.pool.start_all()[0]['result'], 'skipped')
        self.pool.stop_all()
        self.assertFalse(a.starting)
        self.config.save({'dingtalk_rooms':[], 'dingtalk_conversation':'', 'dingtalk_name':''})
        self.pool.sync()
        with self.assertRaises(ValueError): self.pool.start_all()
        self.assertEqual(self.pool.stop_all(), [])

    def test_batch_only_operates_checked_rooms_and_validates_before_mutation(self):
        a, b = self.pool.get('room-a'), self.pool.get('room-b')
        for bad in ([], ['room-a', 'missing'], ['room-a', 'room-a'], 'room-a', [1]):
            with self.assertRaises(ValueError): self.pool.start_all(bad)
            self.assertFalse(a.enabled or b.enabled)
        self.pool.start_all(['room-b'])
        self.assertFalse(a.enabled)
        self.assertTrue(b.enabled)
        self.pool.stop_all(['room-a'])
        self.assertTrue(b.enabled)
        self.pool.stop_all(['room-b'])
        self.assertFalse(b.enabled)

    def test_batch_reply_options_persist_and_skip_running_rooms(self):
        a, b = self.pool.get('room-a'), self.pool.get('room-b')
        a.start(auto_reply=False)
        for auto, latest in [(True, True), (True, False), (False, True), (False, False)]:
            with self.subTest(auto=auto, latest=latest):
                b.stop()
                self.pool.start_all(['room-a','room-b'], auto_reply=auto, reply_latest=latest)
                self.assertFalse(a.auto_reply)
                self.assertEqual(b.auto_reply, auto)
                self.assertEqual(b.initial_tick, auto and latest)
                saved = self.config.data['dingtalk_rooms'][1]
                self.assertEqual((saved['auto_reply'], saved['reply_latest']), (auto, auto and latest))
                self.assertFalse(a.adapter.sent or b.adapter.sent)
        before = self.config.data.copy()
        with self.assertRaises(ValueError): self.pool.start_all(['room-b'], auto_reply='true')
        self.assertEqual(self.config.data, before)
    def test_independent_pending_and_stop_and_deduplication(self):
        a,b = self.pool.get('room-a'),self.pool.get('room-b')
        for worker in (a,b):
            worker.start(auto_reply=False)
            worker.adapter.snapshot=snap(msg('旧消息'),msg('相同的新消息'))
            with patch('engine.time.sleep'):worker.tick()
        self.assertEqual(a.latest['reply'],'room-a 的回复')
        self.assertEqual(b.latest['reply'],'room-b 的回复')
        self.assertNotEqual(a.ledger_path,b.ledger_path)
        self.assertNotEqual(a.pending['id'],b.pending['id'])
        with self.assertRaises(ValueError): b.send_pending(a.pending['id'])
        self.assertFalse(a.adapter.sent or b.adapter.sent)
        a.stop()
        self.assertTrue(b.enabled)
        self.assertIsNotNone(b.pending)
        b.send_pending(b.pending['id'])
        self.assertEqual(b.adapter.sent,['room-b 的回复'])
        self.assertFalse(a.adapter.sent)
    def test_removing_room_invalidates_pending_and_cannot_restart_old_worker(self):
        a,b=self.pool.get('room-a'),self.pool.get('room-b')
        a.start(auto_reply=False);b.start(auto_reply=False)
        self.config.save({'dingtalk_rooms':[{'id':'room-b','name':'会话乙'}]})
        self.pool.sync()
        self.assertTrue(b.enabled)
        self.assertTrue(a.closed.is_set())
        self.assertFalse(a.enabled)
        with self.assertRaises(ValueError): self.pool.get('room-a')
        with self.assertRaises(ValueError): a.start()
    def test_fixed_profile_and_room_survive_other_room_selection(self):
        a=self.pool.get('room-a')
        self.config.save({'dingtalk_conversation':'room-b','dingtalk_name':'会话乙'})
        self.pool.get('room-b')
        self.assertEqual(a.config.data['dingtalk_conversation'],'room-a')
        self.config.save({'dingtalk_profile':'new:account'})
        self.assertEqual(a.config.data['dingtalk_profile'],'fake:profile')
        self.pool.sync()
        self.assertTrue(a.closed.is_set())
        self.assertNotEqual(a.ledger_path,self.pool.get('room-a').ledger_path)
    def test_migration_and_configuration_limits(self):
        migrated=validate({**DEFAULTS,'dingtalk_conversation':'old','dingtalk_name':'旧会话'})
        self.assertEqual(migrated['dingtalk_rooms'],[{'id':'old','name':'旧会话','auto_reply':False,'reply_latest':False}])
        for rooms in ([{'id':'a','name':'A'}]*2, [{'id':str(i),'name':'A'} for i in range(13)], [{'id':'a','name':'A','auto_reply':'yes'}], 'a'):
            with self.assertRaises(ValueError): validate({**DEFAULTS,'dingtalk_rooms':rooms})

    def test_each_room_runs_on_its_own_worker_thread(self):
        import threading
        a,b=self.pool.get('room-a'),self.pool.get('room-b')
        entered_a,entered_b,release=threading.Event(),threading.Event(),threading.Event()
        def wait_here(event):
            event.set();release.wait(3)
        with patch.object(a,'run',side_effect=lambda:wait_here(entered_a)), patch.object(b,'run',side_effect=lambda:wait_here(entered_b)):
            self.pool.run()
            try:
                self.assertTrue(entered_a.wait(1))
                self.assertTrue(entered_b.wait(1))
            finally:release.set()
