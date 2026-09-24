"""One scoped CLI subscription; no historical-message polling or automatic resubscribe."""
import json
import subprocess
import threading
import copy
from collections import deque


class MessageStream:
    def __init__(self, command, conversation_id):
        self.command, self.cid = command, conversation_id
        self.ready = threading.Event()
        self.changed = threading.Event()
        self.closed = threading.Event()
        self.process = None
        self.error = ''
        self.seen = set()
        self.lock = threading.RLock()
        self.messages = deque(maxlen=200)

    def accept(self, event):
        if not isinstance(event, dict) or event.get('conversation_id') != self.cid:
            return
        mid = event.get('message_id')
        if not isinstance(mid, str) or not mid:
            return
        with self.lock:
            if mid in self.seen:
                return
            if len(self.seen) >= 2000:
                self.seen.clear()
            self.seen.add(mid)
            self.messages.append(copy.deepcopy(event))
            self.changed.set()

    def drain(self):
        with self.lock:
            messages = list(self.messages)
            self.messages.clear()
            return messages

    def start(self):
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._stderr, daemon=True).start()
        threading.Thread(target=self._stdout, daemon=True).start()
        if not self.ready.wait(35) or self.error or self.closed.is_set():
            self.close()
            raise ValueError(self.error or '钉钉实时连接未就绪，请检查登录及网络后重新开启')

    def _stderr(self):
        for line in self.process.stderr:
            if line.startswith('[event] ready '):
                self.ready.set()
        if not self.closed.is_set():
            self.error = '钉钉实时连接已断开，请检查登录及网络后重新开启（未自动重试）'
            self.ready.set()
            self.changed.set()

    def _stdout(self):
        self.ready.wait()
        for line in self.process.stdout:
            if self.closed.is_set():
                return
            try:
                self.accept(json.loads(line))
            except (ValueError, TypeError):
                continue

    def poll(self):
        if self.error:
            raise ValueError(self.error)
        if self.changed.is_set():
            self.changed.clear()
            return True
        return False

    def close(self):
        self.closed.set()
        self.ready.set()
        self.changed.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()  # SIGTERM lets dws remove only this owned subscription.
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # Never SIGKILL: retain the handle and refuse a replacement.
