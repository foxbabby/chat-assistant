"""Independent, bounded DingTalk workers. Viewing a room never changes its identity."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from config import atomic_json
from dingtalk import DingTalk
from engine import Engine


class RoomConfig:
    def __init__(self, parent, profile, room):
        self.parent, self.profile, self.room = parent, profile, dict(room)
        self.path = parent.path

    @property
    def data(self):
        return {**self.parent.data, 'dingtalk_profile': self.profile,
                'dingtalk_conversation': self.room['id'], 'dingtalk_name': self.room['name']}

    def public(self):
        return {**self.parent.public(), 'dingtalk_conversation': self.room['id'],
                'dingtalk_name': self.room['name']}


class DingTalkPool:
    def __init__(self, config):
        self.config = config
        self.lock = threading.RLock()
        self.workers = {}
        self.retired_streams = {}
        self.running = False
        self.empty = Engine(config, DingTalk(config), directory=config.path.parent/'dingtalk')
        self.sync()

    def sync(self):
        with self.lock:
            profile = self.config.data['dingtalk_profile']
            wanted = {(profile, r['id']): r for r in self.config.data['dingtalk_rooms']}
            for key in list(self.workers):
                if key not in wanted:
                    old = self.workers.pop(key)
                    old.closed.set()
                    old.stop('会话已移除或账号已变更')
                    stream = getattr(old.adapter, 'stream', None)
                    if stream and stream.process and stream.process.poll() is None:
                        self.retired_streams[key] = stream
            for key, room in wanted.items():
                if key in self.workers:
                    worker = self.workers[key]
                    worker.config.room = dict(room)
                    if not worker.enabled and not worker.starting:
                        worker.auto_reply = room['auto_reply']
                    continue
                scoped = RoomConfig(self.config, profile, room)
                folder = self.config.path.parent/'dingtalk'/'rooms'/hashlib.sha256(('\0'.join(key)).encode()).hexdigest()
                # Carry old deduplication history forward; never replay previously sent messages.
                for name in ('processed.json', 'outbox.json'):
                    legacy = self.config.path.parent/'dingtalk'/name
                    if not (folder/name).exists() and legacy.exists():
                        atomic_json(folder/name, json.loads(legacy.read_text()))
                adapter = DingTalk(scoped)
                # Retain any unclosed subscription handle so begin_listening refuses
                # a duplicate after a rapid remove/re-add.
                adapter.stream = self.retired_streams.pop(key, None)
                worker = Engine(scoped, adapter, directory=folder)
                worker.auto_reply = room['auto_reply']
                self.workers[key] = worker
                if self.running:
                    threading.Thread(target=worker.run, daemon=True).start()

    def get(self, cid=''):
        with self.lock:
            if not cid:
                rooms = self.config.data['dingtalk_rooms']
                if not rooms:
                    return self.empty
                cid = rooms[0]['id']
            worker = self.workers.get((self.config.data['dingtalk_profile'], cid))
            if worker is None:
                raise ValueError('会话已移除，请重新选择')
            return worker

    def rooms(self):
        with self.lock:
            return [{**r, **{k:v for k,v in self.get(r['id']).state().items()
                            if k in ('enabled','starting','status','auto_reply','count','activity')},
                     'pending': bool(self.get(r['id']).pending)}
                    for r in self.config.data['dingtalk_rooms']]

    def state(self):
        return self.get().state()

    def selected_rooms(self, room_ids=None):
        with self.lock:
            rooms = self.config.data['dingtalk_rooms']
            if room_ids is not None:
                if (not isinstance(room_ids, list) or not room_ids or
                        any(not isinstance(cid, str) for cid in room_ids) or
                        len(set(room_ids)) != len(room_ids)):
                    raise ValueError('请勾选要操作的会话')
                if set(room_ids) - {r['id'] for r in rooms}:
                    raise ValueError('所选会话已移除，请重新选择')
                rooms = [r for r in rooms if r['id'] in room_ids]
            return [(dict(r), self.get(r['id'])) for r in rooms]

    def start_all(self, room_ids=None, *, auto_reply=False, reply_latest=False):
        """Apply explicit reply options to the selected paused rooms."""
        if not isinstance(auto_reply, bool) or not isinstance(reply_latest, bool):
            raise ValueError('回复选项必须是勾选状态')
        reply_latest = reply_latest and auto_reply
        rooms = self.selected_rooms(room_ids)
        if not rooms:
            raise ValueError('请先在管理中添加监听会话')
        with self.lock:
            paused_ids = {room['id'] for room, worker in rooms if not worker.enabled and not worker.starting}
            if paused_ids:
                self.config.save({'dingtalk_rooms':[
                    {**r, 'auto_reply':auto_reply, 'reply_latest':reply_latest} if r['id'] in paused_ids else r
                    for r in self.config.data['dingtalk_rooms']]})
                self.sync()

        def start_room(item):
            room, worker = item
            result = {'id': room['id'], 'name': room['name']}
            with worker.lock:
                if worker.enabled or worker.starting:
                    return {**result, 'result': 'skipped'}
            try:
                worker.start(reply_latest=reply_latest, auto_reply=auto_reply)
                with worker.lock:
                    if not worker.enabled:
                        return {**result, 'result': 'failed', 'error': worker.status}
                return {**result, 'result': 'started'}
            except Exception as error:
                message = str(error) if isinstance(error, ValueError) else '连接失败，请单独重试此会话'
                return {**result, 'result': 'failed', 'error': message}

        with ThreadPoolExecutor(max_workers=3) as executor:
            return list(executor.map(start_room, rooms))

    def stop_all(self, room_ids=None):
        results = []
        with self.lock:
            for room, worker in self.selected_rooms(room_ids):
                result = {'id': room['id'], 'name': room['name']}
                try:
                    with worker.lock:
                        active = worker.enabled or worker.starting
                    if active:
                        worker.stop('已批量暂停监听')
                    results.append({**result, 'result': 'stopped' if active else 'skipped'})
                except Exception:
                    results.append({**result, 'result': 'failed', 'error': '暂停失败，请单独重试此会话'})
        return results

    def stop(self, reason='已暂停监听', *, preserve_session=False):
        with self.lock:
            for worker in [self.empty, *self.workers.values()]:
                worker.stop(reason, preserve_session=preserve_session)

    def run(self):
        with self.lock:
            self.running = True
            for worker in self.workers.values():
                threading.Thread(target=worker.run, daemon=True).start()

    def __getattr__(self, name):
        return getattr(self.get(), name)
