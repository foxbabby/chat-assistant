import copy
import hashlib
import json
import re
import threading
import time
from datetime import datetime
from config import DATA_DIR, atomic_json
from cloud import reply, CloudError, NeedsReview
from wechat import identity, signature, ReadUnavailable


def digest(snapshot):
    payload = [identity(snapshot), signature(snapshot)[-8:]]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def is_self(message, config):
    # Only an actual message author can match an alias, never the conversation title.
    if getattr(message, 'message_id', None):
        return message.side == 'me'
    sender = (message.sender or '').strip().casefold()
    return message.side == 'me' or bool(sender and sender in {
        n.strip().casefold() for n in config.get('self_names', [])})


def excluded(snapshot, config):
    messages = snapshot.get('messages', [])
    if not messages:
        return False
    last = messages[-1]
    if is_self(last, config):
        return True
    names = {n.strip().casefold() for n in config.get('excluded_senders', [])}
    # Sender is preferred for groups; the chat title covers one-to-one chats.
    sender = (last.sender or '').strip().casefold()
    title = snapshot.get('chat_title', '').strip().casefold()
    if sender:
        return sender in names
    if title in names:
        return True
    return False


def same_message(a, b):
    if len(a) > 3 or len(b) > 3:
        return len(a) > 3 and len(b) > 3 and a[3] == b[3] and a[2] == b[2]
    if a[2] != b[2]:
        return False
    return a == b or a[0] == 'unknown' or b[0] == 'unknown'


def overlaps(old, new):
    return any(all(same_message(a, b) for a, b in zip(old[-size:], new[:size]))
               for size in range(1, min(len(old), len(new)) + 1))


class Engine:
    def __init__(self, config, adapter, generator=reply, directory=DATA_DIR):
        self.config, self.adapter, self.generator = config, adapter, generator
        self.lock = threading.RLock()
        self.enabled = False
        self.starting = False
        self.epoch = 0
        self.target = None
        self.baseline = None
        self.status = '选择一种风格，连接云端后即可开始'
        self.events = []
        self.latest = None
        self.count = 0
        self.ledger_path = directory / 'processed.json'
        self.processed = set(json.loads(self.ledger_path.read_text())) if self.ledger_path.exists() else set()
        self.outbox_path = directory / 'outbox.json'
        self.outbox = json.loads(self.outbox_path.read_text()) if self.outbox_path.exists() else {}

    def outgoing_key(self, snapshot, text):
        # Do not store message bodies or credentials in the durable echo ledger.
        payload = [snapshot.get('chat_title', '').strip(), ''.join(text.split())]
        if snapshot.get('platform') == 'dingtalk':
            payload.insert(0, identity(snapshot)[0])
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def sent_by_assistant(self, snapshot, text):
        # DingTalk has trustworthy author IDs: identical text from another person is new.
        if snapshot.get('platform') == 'dingtalk':
            return False
        if 'image:'+digest(snapshot) in self.processed:
            return True
        stamp = self.outbox.get(self.outgoing_key(snapshot, text), 0)
        return time.time() - stamp < 86400

    def reserve_outgoing(self, snapshot, text):
        now = time.time()
        self.outbox = {key: stamp for key, stamp in self.outbox.items() if now-stamp < 86400}
        self.outbox[self.outgoing_key(snapshot, text)] = now
        atomic_json(self.outbox_path, self.outbox)


    def state(self):
        with self.lock:
            return {'enabled': self.enabled, 'starting': self.starting, 'status': self.status,
                    'target': self.target[1] if self.target else '', 'count': self.count,
                    'events': list(self.events), 'latest': self.latest,
                    'settings': self.config.public(), 'permissions': self.adapter.permissions()}

    def event(self, text):
        self.events.insert(0, {'time': datetime.now().strftime('%H:%M:%S'), 'text': text})
        self.events = self.events[:30]

    def stop(self, reason='已暂停自动回复'):
        with self.lock:
            self.epoch += 1
            self.enabled = self.starting = False
            self.status = reason
            self.event(reason)
            if hasattr(self.adapter, 'end_listening'):
                self.adapter.end_listening()

    def show_snapshot(self, snapshot, label):
        messages = snapshot.get('messages', [])
        self.latest = ({'incoming': messages[-1].text, 'reply': '', 'state': label,
                        'sender': ('本助手已发送' if self.sent_by_assistant(snapshot, messages[-1].text) else '我自己' if is_self(messages[-1], self.config.data) else '未确认发送者' if messages[-1].side == 'unknown' else (messages[-1].sender or snapshot.get('chat_title', '')))}
                       if messages else None)

    def scan(self):
        snapshot = self.adapter.read()
        with self.lock:
            if not self.enabled and not self.starting:
                self.target = identity(snapshot)
                self.show_snapshot(snapshot, '仅显示当前消息，未开启自动回复')
                self.status = '已读取当前消息；自动回复未开启'
        return snapshot

    def start(self, reply_latest=False):
        with self.lock:
            if self.enabled or self.starting:
                return
            if not self.config.data['api_key']:
                raise ValueError('请先在设置中填写 API Key')
            permissions = self.adapter.permissions()
            if not permissions.get('accessibility'):
                raise ValueError('请先完成辅助功能授权')
            self.epoch += 1
            epoch = self.epoch
            self.starting = True
            self.status = '正在识别当前会话…'
        try:
            snapshot = self.adapter.read()
            if hasattr(self.adapter, 'begin_listening'):
                self.adapter.begin_listening(snapshot)
                if self.epoch != epoch:
                    self.adapter.end_listening()
                    return
                snapshot = self.adapter.read()
            with self.lock:
                if self.epoch != epoch:
                    return
                self.target = identity(snapshot)
                self.baseline = signature(snapshot)[:-1] if reply_latest else signature(snapshot)
                self.enabled, self.starting = True, False
                self.initial_tick = reply_latest
                self.status = '实时连接已就绪，等待新消息' if hasattr(self.adapter, 'has_update') else '等待当前会话的新消息'
                self.event('自动回复已开启 · ' + self.target[1])
                self.show_snapshot(snapshot, '将核对并回复当前最后一条消息' if reply_latest else '当前已有消息，仅显示；等待新消息')
                if reply_latest:
                    self.status = '准备回复当前最后一条消息…'
        except ReadUnavailable as error:
            if hasattr(self.adapter, 'end_listening'):
                self.adapter.end_listening()
                self.stop('开启失败：' + str(error))
                raise ValueError(str(error)) from None
            with self.lock:
                if self.epoch == epoch:
                    self.enabled, self.starting = True, False
                    self.target = self.baseline = None
                    self.latest = None
                    self.status = '自动回复已开启，等待会话：' + str(error)
                    self.event('已开启监听，等待会话可读取')
        except Exception as error:
            if hasattr(self.adapter, 'end_listening'):
                self.adapter.end_listening()
            reason = str(error) if isinstance(error, ValueError) else '读取会话失败（' + type(error).__name__ + '），请刷新当前消息重试'
            with self.lock:
                if self.epoch == epoch:
                    self.enabled = self.starting = False
                    self.status = '开启失败：' + reason
                    self.event(self.status)
            raise ValueError(reason) from None

    def tick(self):
        with self.lock:
            if not self.enabled:
                return
            epoch = self.epoch
        try:
            try:
                snap = self.adapter.read()
            except ReadUnavailable as error:
                with self.lock:
                    if epoch == self.epoch and self.enabled:
                        self.status = '继续监听，等待会话恢复：' + str(error)
                return False
            with self.lock:
                if epoch != self.epoch or not self.enabled:
                    return
                if self.target is not None and identity(snap) != self.target:
                    self.stop('会话已切换，已暂停；请在当前会话重新开启')
                    return
                if self.target is None:
                    self.target = identity(snap)
                    self.baseline = signature(snap)
                    self.show_snapshot(snap, '当前已有消息，仅显示；继续等待新消息')
                    self.status = '正在监听当前会话，等待新消息'
                    self.event('会话已就绪，继续监听')
                    return
                sig = signature(snap)
                if sig == self.baseline:
                    return
                messages = snap['messages']
                if (self.baseline and len(sig) == len(self.baseline)
                        and any(m[0] == 'unknown' for m in self.baseline)
                        and all(same_message(a,b) for a,b in zip(self.baseline,sig))):
                    self.baseline = sig
                    self.show_snapshot(snap, '发送者信息已更新，继续等待新消息')
                    return
                if messages and self.sent_by_assistant(snap, messages[-1].text):
                    self.baseline = sig
                    self.show_snapshot(snap, '本助手发送记录已匹配，跳过重复回复')
                    self.status = '已跳过本助手发送的内容，继续等待新消息'
                    return
                if (messages and getattr(messages[-1], 'kind', 'text') in ('image','sticker')
                        and 'image-pending:'+self.outgoing_key(snap,'') in self.outbox):
                    self.baseline = sig
                    self.show_snapshot(snap, '上次图片发送结果未确认，暂不再回复图片；文字消息继续监听')
                    self.status = '图片发送待人工确认，文字消息继续监听'
                    return
                # Baseline first: only handle a stable incoming tail, never old history on start.
                old = self.baseline
                if not messages or excluded(snap, self.config.data):
                    self.baseline = sig
                    self.status = '最后一条匹配无需回复规则，已跳过'
                    self.show_snapshot(snap, '无需回复，已跳过')
                    self.event('已跳过 · 无需回复的人员')
                    return
                # Existing tail must still overlap. Scrolling to unrelated history is not a new message.
                if old and not overlaps(old, sig):
                    self.baseline = sig
                    self.show_snapshot(snap, '聊天记录已变化，仅更新显示；继续监听')
                    self.status = '聊天记录已更新，继续等待新消息'
                    return
            time.sleep(0.65)
            try:
                stable = self.adapter.read()
            except ReadUnavailable:
                with self.lock:
                    if epoch == self.epoch and self.enabled:
                        self.status = '消息暂时无法复核，继续等待新消息'
                return False
            if signature(stable) != sig or identity(stable) != identity(snap):
                return False
            with self.lock:
                if epoch != self.epoch or not self.enabled:
                    return
                key = digest(snap)
                self.baseline = sig
                if key in self.processed:
                    return
                if 'research:'+key in self.processed:
                    self.show_snapshot(snap, '这条问题曾发送查询提示，但未确认最终回复；请人工核对，避免重复发送')
                    self.status = '上次查询未完成，请核对；继续监听新消息'
                    return
                if messages[-1].conf < 0.85:
                    self.show_snapshot(snap, '消息识别不够清晰，已跳过；继续监听')
                    self.status = '已跳过识别不清晰的消息，继续等待新消息'
                    return
                config = self.config.data.copy()
                self.status = '正在生成' + config['style'] + '回复…'
                self.latest = {'incoming': messages[-1].text, 'reply': '', 'state': '生成中'}
            history = copy.deepcopy(messages)
            question_snapshot = snap
            for message in history:
                if self.sent_by_assistant(snap, message.text):
                    message.side = 'me'
            from cloud import Draft, work_clarification
            from cloud import work_query
            acknowledged = False
            # Only the production knowledge generator opts in; previews do not send.
            if (getattr(self.generator, 'supports_research_ack', False) is True
                    and work_query(history)[1] and config.get('spd_knowledge') == 'enabled'):
                acknowledgement = '我查一下相关资料，稍等。'
                original = copy.deepcopy(messages[-1])
                with self.lock:
                    if epoch != self.epoch or not self.enabled:
                        return
                    self.reserve_outgoing(snap, acknowledgement)
                    self.processed.add('research:'+key)
                    atomic_json(self.ledger_path, sorted(self.processed))
                    self.status = '正在发送查询提示…'
                snap = self.adapter.send(acknowledgement, snap, lambda: self.enabled and self.epoch == epoch)
                acknowledged = True
                # Second DingTalk reply quotes the original question, not our own ack.
                snap['reply_anchor'] = original
                snap['reply_phase'] = 'answer'
                with self.lock:
                    if epoch != self.epoch or not self.enabled:
                        return
                    self.baseline = signature(snap)
                    self.latest['state'] = '已告知稍等，正在查资料'
                    self.status = '已告知稍等，正在查询 SPD 资料…'
                    self.event('已发送查询提示，正在查资料')
            try:
                if hasattr(self.adapter, 'prepare_history'):
                    history = self.adapter.prepare_history(question_snapshot, config, history)
                kind = getattr(messages[-1], 'kind', 'text')
                if kind not in ('text', 'sticker', 'image'):
                    answer = Draft('收到了，方便用文字补充一下你想说的内容吗？')
                elif kind in ('sticker','image') and not getattr(self.adapter, 'text_media_reply', False):
                    from stickers import reply_card
                    answer = reply_card(config, messages[-1].text)
                else:
                    answer = self.generator(config, history)
            except CloudError as error:
                if not isinstance(error, NeedsReview) and not acknowledged:
                    raise
                # A content uncertainty is not a reason to ignore an incoming message.
                # Use a factual clarification, never the rejected model's answer.
                if getattr(messages[-1], 'has_resources', False) or getattr(messages[-1], 'kind', 'text') in ('image', 'sticker'):
                    answer = Draft('图片收到了，方便用文字补充一下重点吗？', warnings=['图片未能可靠识别，本次仅追问内容'])
                else:
                    answer = work_clarification(messages[-1].text) or Draft('这点我还不确定，方便再具体说一下吗？', warnings=['本次发送澄清追问，未采用未经确认的答案'])
                if acknowledged and not isinstance(error, NeedsReview):
                    answer = Draft('这次查询暂时没完成，还不能给你准确结论。方便补充一下具体页面或现场版本吗？', warnings=['资料查询或模型服务失败，本次发送进度说明'])
            with self.lock:
                if epoch != self.epoch or not self.enabled:
                    return
                self.latest = {'incoming': messages[-1].text, 'reply': answer, 'state': '发送前校验',
                               'image_path': getattr(answer, 'image_path', None), 'sources': getattr(answer, 'sources', []), 'knowledge_warnings': getattr(answer, 'warnings', [])}
                self.status = '正在核对会话并发送…'
                # Durable reservation before ANY write; ambiguous sends never retry after restart.
                if not getattr(answer, 'image_path', None):
                    self.reserve_outgoing(snap, answer)
                else:
                    self.outbox['image-pending:'+self.outgoing_key(snap,'')] = time.time()
                    atomic_json(self.outbox_path, self.outbox)
                self.processed.add(key)
                atomic_json(self.ledger_path, sorted(self.processed))
            if getattr(answer, 'image_path', None):
                from image_sender import send_image
                sent = send_image(self.adapter, answer.image_path, snap, lambda: self.enabled and self.epoch == epoch)
                with self.lock:
                    self.processed.add('image:'+digest(sent))
                    atomic_json(self.ledger_path, sorted(self.processed))
                    self.outbox.pop('image-pending:'+self.outgoing_key(snap,''), None)
                    atomic_json(self.outbox_path,self.outbox)
            else:
                sent = self.adapter.send(answer, snap, lambda: self.enabled and self.epoch == epoch)
            with self.lock:
                if epoch != self.epoch:
                    return
                self.baseline = signature(sent)
                self.count += 1
                self.latest['state'] = '已发送并确认'
                self.status = '等待当前会话的新消息'
                self.event('已发送 · ' + config['style'])
        except NeedsReview as error:
            with self.lock:
                if epoch == self.epoch and self.enabled:
                    self.status = str(error)
                    self.show_snapshot(snap, str(error))
                    self.event('工作问题待人工确认，未发送；继续监听')
        except (ValueError, CloudError) as error:
            self.stop(str(error))
        except Exception:
            self.stop('处理失败，已暂停；请检查权限、网络和平台状态')

    def run(self):
        while True:
            if hasattr(self.adapter, 'has_update'):
                if self.enabled:
                    try:
                        if getattr(self, 'initial_tick', False) or self.adapter.has_update():
                            self.initial_tick = False
                            for attempt in range(3):
                                if self.tick() is not False or not self.enabled:
                                    break
                                time.sleep(1)
                    except ValueError as error:
                        self.stop(str(error))
                time.sleep(0.2)  # Local event flag only; no network requests while idle.
            else:
                self.tick()
                time.sleep(1.5)
