"""Independent user configuration; secrets never returned to the UI or logged."""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

DATA_DIR = Path.home() / 'Library/Application Support/微信聊天助手'
DEFAULTS = {'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash',
            'api_key': '', 'style': '自然友好', 'excluded_senders': [], 'self_names': [],
            'voice_profile': '用我的第一人称说话。简短、直接、自然，先说重点，熟人聊天不客套，不使用客服腔。',
            'reply_examples': '', 'work_knowledge': '', 'spd_knowledge': 'enabled',
            'dingtalk_profile': '', 'dingtalk_conversation': '', 'dingtalk_name': '',
            'dingtalk_image_mode': 'ocr', 'dingtalk_vision_url': '', 'dingtalk_vision_model': '', 'dingtalk_vision_key': '',
            'dingtalk_rooms': [], 'dingtalk_interval': 15, 'wechat_reply_latest': False, 'dingtalk_reply_latest': False}
STYLES = {
    '自然友好': '像熟悉的朋友一样自然、礼貌，简短接住对方的话，不用过度客套。',
    '高情商': '先理解对方的情绪，再温和表达自己的想法，清楚、有分寸。',
    '简洁专业': '用简短清晰的句子回答，先说重点，语气可靠、专业。',
    '轻松幽默': '自然轻松，适当幽默和自嘲，不过度玩梗，不讽刺对方。',
    '温柔耐心': '温柔、耐心、有同理心，认真回应，不说教。',
    '礼貌婉拒': '清楚而礼貌地表达边界，不含糊，不编造理由。',
}


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate(data):
    list_fields = {'excluded_senders': '无需回复的人员', 'self_names': '我的微信昵称／群昵称'}
    typed = {'dingtalk_rooms', 'dingtalk_interval', 'wechat_reply_latest', 'dingtalk_reply_latest'}
    result = {key: str(data.get(key, value)).strip() for key, value in DEFAULTS.items() if key not in list_fields and key not in typed}
    rooms = data.get('dingtalk_rooms', [])
    if not rooms and result['dingtalk_conversation']:
        rooms = [{'id':result['dingtalk_conversation'], 'name':result['dingtalk_name'] or '钉钉会话'}]
    if not isinstance(rooms, list) or len(rooms) > 12:
        raise ValueError('最多同时管理 12 个钉钉会话')
    clean, seen = [], set()
    for room in rooms:
        if not isinstance(room, dict):
            raise ValueError('会话设置格式不正确')
        cid, name = room.get('id'), room.get('name')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 300 or any(c in v for c in '\r\n\x00') for v in (cid, name)) or cid in seen:
            raise ValueError('会话名称或标识不正确，不能重复添加')
        if any(not isinstance(room.get(k, False), bool) for k in ('auto_reply', 'reply_latest')):
            raise ValueError('会话回复选项必须为勾选状态')
        seen.add(cid)
        clean.append({'id':cid, 'name':name, 'auto_reply':room.get('auto_reply',False), 'reply_latest':room.get('reply_latest',False)})
    result['dingtalk_rooms'] = clean
    for key in ('wechat_reply_latest', 'dingtalk_reply_latest'):
        result[key] = data.get(key, False)
        if not isinstance(result[key], bool):
            raise ValueError('立即回复历史消息选项必须为勾选状态')
    interval = data.get('dingtalk_interval', 15)
    if type(interval) is not int or not 10 <= interval <= 300:
        raise ValueError('钉钉检查间隔为 10–300 秒')
    result['dingtalk_interval'] = interval
    for key in ('dingtalk_profile', 'dingtalk_conversation', 'dingtalk_name'):
        if len(result[key]) > 300 or any(c in result[key] for c in '\r\n\x00'):
            raise ValueError('钉钉设置格式不正确')
    for key, label in list_fields.items():
        names = data.get(key, [])
        if not isinstance(names, list) or len(names) > 200 or any(not isinstance(n, str) or len(n) > 100 for n in names):
            raise ValueError(label + '请每行填写一个名称，最多 200 个')
        result[key] = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    for key in ('voice_profile', 'reply_examples', 'work_knowledge'):
        if len(result[key]) > 20000:
            raise ValueError('个人表达和工作知识每项最多 20000 字')
    if result['spd_knowledge'] not in ('enabled', 'disabled'):
        raise ValueError('工作知识检索设置不正确')
    if result['dingtalk_image_mode'] not in ('ocr', 'cloud'):
        raise ValueError('请选择图片识别方式')
    if result['dingtalk_vision_url']:
        v = urlparse(result['dingtalk_vision_url'])
        if v.scheme != 'https' or not v.hostname or v.username or v.password or v.query or v.fragment or v.hostname in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
            raise ValueError('视觉模型地址请填写云端 HTTPS 地址')
    if len(result['dingtalk_vision_model']) > 120 or len(result['dingtalk_vision_key']) > 4096 or any(c in result['dingtalk_vision_key'] for c in '\r\n'):
        raise ValueError('视觉模型配置格式不正确')
    url = urlparse(result['base_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('API 地址请填写有效的 HTTPS 云端地址，不包含用户名、参数或密钥')
    if url.hostname in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
        raise ValueError('请使用云端模型地址')
    if not result['model'] or len(result['model']) > 120:
        raise ValueError('请填写模型名称')
    if result['style'] not in STYLES:
        raise ValueError('请选择一种回复风格')
    if len(result['api_key']) > 4096 or any(c in result['api_key'] for c in '\r\n'):
        raise ValueError('API Key 格式不正确')
    result['base_url'] = result['base_url'].rstrip('/')
    return result


class Config:
    def __init__(self, directory=DATA_DIR):
        self.path = directory / 'settings.json'
        self.data = DEFAULTS.copy()
        if self.path.exists():
            try:
                self.data = validate(json.loads(self.path.read_text()))
            except (ValueError, OSError):
                pass

    def public(self):
        return {**{k: v for k, v in self.data.items() if k not in ('api_key', 'dingtalk_vision_key')},
                'has_vision_key': bool(self.data['dingtalk_vision_key']), 'has_key': bool(self.data['api_key'])}

    def save(self, incoming):
        update = {k: v for k, v in incoming.items() if k in DEFAULTS}
        if not update.get('api_key'):
            update.pop('api_key', None)
        if not update.get('dingtalk_vision_key'):
            update.pop('dingtalk_vision_key', None)
        if update.get('dingtalk_vision_url', self.data['dingtalk_vision_url']).rstrip('/') != self.data['dingtalk_vision_url'].rstrip('/') and 'dingtalk_vision_key' not in update:
            raise ValueError('更换视觉模型地址时，请同时填写该服务的 API Key')
        # A saved key must never silently follow an endpoint change.
        if update.get('base_url', self.data['base_url']).rstrip('/') != self.data['base_url'] and 'api_key' not in update:
            raise ValueError('更换 API 地址时，请同时填写该服务的 API Key')
        merged = validate({**self.data, **update})
        atomic_json(self.path, merged)
        self.data = merged
        return self.public()
