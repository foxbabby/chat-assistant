"""DingTalk user API adapter. Fixed profile/target, no UI automation or shell expansion."""
import hashlib
import copy
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from wechat import ReadUnavailable, identity, signature

DWS = Path.home() / '.local/bin/dws'


def run(args, profile=None, *, cwd=None):
    command = [str(DWS), *args, '--format', 'json']
    if profile:
        command += ['--profile', profile]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=40,
                                stdin=subprocess.DEVNULL, cwd=cwd)
    except FileNotFoundError:
        raise ValueError('未找到钉钉连接工具 dws，请在设置中查看连接说明') from None
    except (subprocess.TimeoutExpired, OSError):
        raise ReadUnavailable('钉钉连接超时，稍后继续检查') from None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise ValueError('钉钉接口未返回有效结果，请重新检查登录') from None
    if result.returncode or data.get('success') is False or data.get('error') or data.get('errorCode'):
        # Never forward arbitrary stderr, tokens, command arguments, or server payloads to logs/UI.
        raise ValueError('钉钉接口调用失败，请检查登录状态与账号权限')
    return data


def profiles():
    data = run(['profile', 'list'])
    return [{k: p.get(k, '') for k in ('profile', 'userName', 'corpName', 'userId', 'isOrgCurrent')}
            for p in data.get('profiles', []) if p.get('profile')]


def verify_profile(profile):
    matches = [p for p in profiles() if p['profile'] == profile]
    if len(matches) != 1:
        raise ValueError('钉钉账号已变化，请在设置中重新选择并连接')
    return matches[0]


def conversations(profile):
    verify_profile(profile)
    data = run(['chat', '+conversation-list', '--page-all', '--page-limit', '5', '--max-items', '300'], profile)
    if data.get('partial') or data.get('failures'):
        raise ValueError('钉钉会话列表读取不完整，请稍后重试')
    return {'conversations': [{'id': c['openConversationId'], 'name': c.get('conversationName') or c['openConversationId']}
                             for c in data.get('conversations', []) if c.get('openConversationId')],
            'complete': data.get('complete') is True}


class DingTalk:
    platform = 'dingtalk'
    text_media_reply = True

    def __init__(self, config):
        self.config = config
        self.lock = threading.RLock()
        self.account = None
        self.own_ids = set()
        self.checked = 0
        self.stream = None
        self.image_cache = {}
        self.live_messages = None

    def permissions(self):
        return {'screen': None, 'accessibility': True, 'connected': bool(self.account),
                'method': 'dingtalk-stream', 'stream_connected': bool(self.stream and self.stream.ready.is_set() and not self.stream.error and not self.stream.closed.is_set()), 'installed': os.access(DWS, os.X_OK)}

    def connect(self, profile):
        with self.lock:
            account = verify_profile(profile)
            me = run(['contact', 'user', 'get-self'], profile).get('result', [])
            employees = [p.get('orgEmployeeModel', {}) for p in me]
            if len(employees) != 1 or employees[0].get('userId') != account['userId']:
                raise ValueError('无法核对钉钉登录身份，未开启监听')
            name = employees[0].get('orgUserName')
            if not name:
                raise ValueError('钉钉登录资料缺少姓名')
            people = run(['aisearch', 'person', '--query', name, '--dimension', 'name'], profile).get('result', [])
            exact = [p for p in people if p.get('userId') == account['userId'] and p.get('openDingTalkId')]
            ids = {p['openDingTalkId'] for p in exact}
            if len(ids) != 1:
                raise ValueError('无法确认本人钉钉身份，请重新连接账号')
            self.own_ids = ids | {account['userId']}
            self.account = account
            self.checked = time.monotonic()
            return {'name': name, 'organization': account['corpName'], 'connected': True}

    def read(self):
        if self.live_messages is not None:
            return self.read_live()
        return self.read_history()

    def initial_snapshot(self):
        # History is useful context, not a prerequisite for an event subscription.
        self.live_messages = None
        try:
            return self.read_history()
        except ReadUnavailable:
            cfg = self.config.data
            if not self.account or self.account['profile'] != cfg['dingtalk_profile']:
                raise
            return {**self.snapshot([]), 'history_unavailable': True}

    def snapshot(self, messages):
        cfg = self.config.data
        profile, cid = cfg['dingtalk_profile'], cfg['dingtalk_conversation']
        return {'platform': 'dingtalk', 'profile': profile, 'conversation_id': cid,
                'window': {'wid': 'dingtalk:' + profile + ':' + cid},
                'chat_title': cfg['dingtalk_name'] or '钉钉会话', 'messages': messages}

    def listening_snapshot(self, snapshot):
        # Do not absorb events received during startup into the old-message baseline.
        return snapshot

    def read_live(self):
        with self.lock:
            if self.stream.error or self.stream.closed.is_set():
                raise ValueError(self.stream.error or '钉钉实时连接已关闭')
            known = {m.message_id for m in self.live_messages}
            for event in self.stream.drain():
                if event.get('conversation_id') != self.config.data['dingtalk_conversation']:
                    continue
                mid = event.get('message_id')
                if not mid or mid in known:
                    continue
                sender = event.get('sender_open_dingtalk_id')
                if not isinstance(sender, str):
                    sender = ''
                text = event.get('content')
                resources = not isinstance(text, str) or not text.strip()
                if isinstance(text, str):
                    resources = resources or text.strip() in ('[图片]', '[表情]', '[文件]')
                    try:
                        payload = json.loads(text)
                        resources = resources or (isinstance(payload, (dict, list))
                            and any(key in text for key in ('"mediaId"', '"fileId"')))
                    except ValueError:
                        pass
                self.live_messages.append(SimpleNamespace(
                    text='[图片或附件，内容未读取]' if resources else text,
                    sender=event.get('sender') if isinstance(event.get('sender'), str) else '', sender_id=sender, message_id=mid,
                    side='me' if sender in self.own_ids else 'them' if sender else 'unknown',
                    conf=1 if sender else 0, kind='text', has_resources=resources))
                known.add(mid)
            self.live_messages = self.live_messages[-100:]
            return self.snapshot(copy.deepcopy(self.live_messages))

    def read_history(self):
        cfg = self.config.data.copy()
        profile, cid = cfg['dingtalk_profile'], cfg['dingtalk_conversation']
        if not profile or not cid:
            raise ValueError('请先在设置中连接钉钉账号并选择监听会话')
        with self.lock:
            if not self.account or self.account['profile'] != profile:
                self.connect(profile)
            elif time.monotonic() - self.checked > 60:
                verify_profile(profile)
                self.checked = time.monotonic()
            own = self.own_ids.copy()
        data = run(['chat', '+chat-messages', '--conversation-id', cid, '--limit', '20', '--no-reactions'], profile)
        if data.get('partial') or data.get('failures') or not isinstance(data.get('messages'), list):
            raise ReadUnavailable('钉钉最新消息读取不完整，本轮不回复')
        messages, seen = [], set()
        for m in reversed(data['messages']):  # Public API defaults to newest first.
            mid = m.get('messageId')
            if not mid or m.get('conversationId') != cid:
                raise ReadUnavailable('钉钉消息缺少会话或消息标识，本轮不回复')
            if mid in seen:
                continue
            seen.add(mid)
            sender_id = m.get('senderId')
            text = m.get('text') or ''
            # Resource-only messages are described conservatively, never hallucinate image content.
            if not text:
                text = '[图片或附件，内容未读取]' if m.get('resourceRefs') else '[非文字消息，内容未读取]'
            messages.append(SimpleNamespace(text=text, sender=m.get('sender') or '',
                            side='me' if sender_id in own else 'them' if sender_id else 'unknown',
                            conf=1 if sender_id else 0, kind='text', message_id=mid, sender_id=sender_id, has_resources=bool(m.get('resourceRefs'))))
        return {'platform': 'dingtalk', 'profile': profile, 'conversation_id': cid,
                'window': {'wid': 'dingtalk:' + profile + ':' + cid},
                'chat_title': cfg['dingtalk_name'] or '钉钉会话', 'messages': messages}

    def prepare_history(self, snapshot, config, history):
        last = history[-1]
        if not getattr(last, 'has_resources', False):
            return history
        from ding_images import describe
        key = (snapshot['profile'], snapshot['conversation_id'], last.message_id)
        if key not in self.image_cache:
            result = describe(snapshot, config, run)
            if len(self.image_cache) >= 32:
                self.image_cache.clear()
            self.image_cache[key] = result
        last.text += '\n' + self.image_cache[key]
        return history

    def begin_listening(self, snapshot):
        from ding_stream import MessageStream
        if self.stream and self.stream.process and self.stream.process.poll() is None:
            raise ValueError('上一次钉钉连接尚未退出，请稍后再开启')
        profile, cid = snapshot['profile'], snapshot['conversation_id']
        info = run(['chat', 'conversation-info', '--group', cid], profile).get('result', {}).get('conversationInfo', {})
        if info.get('openConversationId') != cid or type(info.get('singleChat')) is not bool:
            raise ValueError('无法确认钉钉会话类型，未建立监听')
        if info['singleChat']:
            candidates = {m.sender_id for m in snapshot['messages'] if m.side == 'them' and m.sender_id}
            if not candidates:
                people = run(['aisearch', 'person', '--query', info.get('title', ''), '--dimension', 'name'], profile).get('result', [])
                candidates = {p['openDingTalkId'] for p in people if p.get('openDingTalkId') and p.get('name', p.get('orgUserName', info.get('title'))) == info.get('title')}
            exact = []
            for peer in sorted(candidates)[:5]:
                resolved = run(['chat', 'conversation-info', '--open-dingtalk-id', peer], profile).get('result', {}).get('conversationInfo', {})
                if resolved.get('openConversationId') == cid:
                    exact.append(peer)
            if len(exact) != 1:
                raise ValueError('无法唯一确认单聊对象，请先在钉钉中与对方产生消息再开启')
            args = ['event', 'consume', 'user_im_message_receive_o2o', '--open-dingtalk-id', exact[0]]
        else:
            args = ['event', '+listen-im', '--kind', 'group', '--chat-id', cid]
        self.stream = MessageStream([str(DWS), *args, '--flatten', '--format', 'ndjson', '--profile', profile], cid)
        self.stream.start()
        self.live_messages = copy.deepcopy(snapshot['messages'])

    def end_listening(self):
        with self.lock:
            if self.stream:
                self.stream.close()
            self.live_messages = None

    def has_update(self):
        return self.stream.poll() if self.stream else False

    def send(self, text, expected, allowed):
        if not allowed():
            raise ValueError('已暂停，取消钉钉发送')
        profile, cid = expected['profile'], expected['conversation_id']
        verify_profile(profile)
        fresh = self.read()
        if not allowed() or identity(fresh) != identity(expected) or signature(fresh) != signature(expected):
            raise ValueError('钉钉会话或消息已变化，本次未发送')
        last = expected.get('reply_anchor', expected['messages'][-1])
        if expected.get('reply_anchor') and not any(m.message_id == last.message_id and m.sender_id == last.sender_id for m in expected['messages']):
            raise ValueError('原问题已不在当前消息中，本次未发送')
        if last.side != 'them' or not last.sender_id:
            raise ValueError('最新消息无需回复，本次未发送')
        key = hashlib.sha256((profile + '\0' + cid + '\0' + last.message_id + '\0' + expected.get('reply_phase', 'initial')).encode()).hexdigest()
        # A user enables replies to this exact target in the UI before this path is reachable.
        # Exactly one write; a timeout or unknown outcome must never trigger another send.
        data = run(['chat', '+messages-reply', '--group', cid, '--message-id', last.message_id,
                    '--ref-sender', last.sender_id, '--content', str(text), '--idempotency-key', key,
                    '--ai-tag=false', '--yes'], profile)
        payload = data.get('result', data)
        if not isinstance(payload, dict):
            payload = data
        mid = payload.get('messageId') or payload.get('openMessageId')
        task = payload.get('openTaskId')
        for _ in range(4):
            if task and not mid:
                status = run(['chat', 'message', 'query-send-status', '--open-task-id', task], profile)
                status = status.get('result', status)
                if isinstance(status, dict) and status.get('openConversationId') == cid:
                    mid = status.get('openMessageId')
            if self.live_messages is not None and mid:
                # The stream filters our own messages. Confirm this exact send by ID,
                # without depending on the conversation's history list being available.
                receipt = run(['chat', '+messages-mget', '--msg-ids', mid, '--no-reactions'], profile)
                matches = [m for m in receipt.get('messages', [])
                           if m.get('messageId') == mid and m.get('conversationId') == cid
                           and m.get('senderId') in self.own_ids and m.get('text') == text]
                if not receipt.get('partial') and not receipt.get('failures') and len(matches) == 1:
                    with self.lock:
                        if not any(m.message_id == mid for m in self.live_messages):
                            self.live_messages.append(SimpleNamespace(text=str(text), sender=matches[0].get('sender') or '',
                                side='me', conf=1, kind='text', message_id=mid,
                                sender_id=matches[0]['senderId'], has_resources=False))
            result = self.read()
            if identity(result) != identity(expected):
                break
            if mid and any(m.message_id == mid and m.side == 'me' and m.text == text for m in result['messages']):
                return result
            time.sleep(1)
        raise ValueError('钉钉已尝试发送一次，尚未确认投递；已暂停，请核对钉钉，系统不会重发')
