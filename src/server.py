import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
from config import Config, STYLES, validate
from cloud import completion, reply, CloudError
from engine import Engine
from wechat import WeChat

WEB = Path(__file__).resolve().parent.parent / 'web'


def make_server(port=18766, config=None, adapter=None):
    config = config or Config()
    adapter = adapter or WeChat()
    engine = Engine(config, adapter, directory=config.path.parent)
    from dingtalk import DingTalk
    ding_adapter = DingTalk(config)
    engines = {'wechat': engine, 'dingtalk': Engine(config, ding_adapter, directory=config.path.parent / 'dingtalk')}
    settings_lock = threading.RLock()
    login = SimpleNamespace(process=None)
    def state_for(platform):
        states = {name: worker.state() for name, worker in engines.items()}
        return {**states[platform], 'platform': platform, 'platforms': states}
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200, content_type='application/json; charset=utf-8', cookie=False):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            if cookie:
                self.send_header('Set-Cookie', f'assistant_session={token}; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def authenticated(self):
            cookies = self.headers.get('Cookie', '')
            return any(hmac.compare_digest(part.strip(), 'assistant_session=' + token) for part in cookies.split(';'))

        def do_GET(self):
            if not self.trusted_host():
                return self.respond({'error': '禁止访问'}, 403)
            route = urlparse(self.path).path
            platform = parse_qs(urlparse(self.path).query).get('platform', ['wechat'])[0]
            if platform not in engines:
                return self.respond({'error': '平台不正确'}, 400)
            engine = engines[platform]
            if route == '/api/diagnostics':
                if not self.authenticated():
                    return self.respond({'error': '请重新打开助手'}, 403)
                import fill
                result = fill.accessibility_diagnostics()
                from ax_reader import read_accessibility
                snap = read_accessibility()
                result['read_method'] = 'accessibility'
                result['has_title'] = bool(snap and snap.get('chat_title'))
                result['message_count'] = len(snap.get('messages', [])) if snap else 0
                result['alignment'] = snap.get('alignment_diagnostics') if snap else None
                return self.respond(result)
            if route == '/api/reply-image':
                if not self.authenticated():
                    return self.respond({'error': '请重新打开助手'}, 403)
                from config import DATA_DIR
                with engine.lock:
                    path = (engine.latest or {}).get('image_path')
                if not path or Path(path).resolve().parent != (DATA_DIR / 'stickers').resolve():
                    return self.respond({'error': '暂无表情图片'}, 404)
                return self.respond(Path(path).read_bytes(), content_type='image/png')
            if route == '/api/state':
                if not self.authenticated():
                    return self.respond({'error': '请重新打开助手'}, 403)
                return self.respond(state_for(platform))
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app-icon.png': ('app-icon.png', 'image/png'),
                     '/app.css': ('app.css', 'text/css'), '/app.js': ('app.js', 'text/javascript')}
            if route not in files:
                return self.respond({'error': '页面不存在'}, 404)
            filename, kind = files[route]
            return self.respond((WEB / filename).read_bytes(), content_type=kind, cookie=route == '/')

        def do_POST(self):
            origin = self.headers.get('Origin')
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if (not self.trusted_host() or not self.authenticated() or origin != expected or
                    self.headers.get('Content-Type') != 'application/json'):
                return self.respond({'error': '请求已拒绝，请从助手界面操作'}, 403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size < 300000:
                    raise ValueError('请求内容不正确')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求格式不正确')
                platform = data.pop('platform', 'wechat')
                if platform not in engines:
                    raise ValueError('平台不正确')
                engine = engines[platform]
                if self.path == '/api/dingtalk-login':
                    import subprocess
                    from dingtalk import DWS
                    with settings_lock:
                        if login.process is None or login.process.poll() is not None:
                            if not DWS.is_file():
                                raise ValueError('未安装 dws，请先安装钉钉连接工具')
                            login.process = subprocess.Popen([str(DWS), 'auth', 'login'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return self.respond({'message': '已打开钉钉授权流程。完成后点击读取已登录账号。'})
                if self.path == '/api/dingtalk-profiles':
                    from dingtalk import profiles
                    return self.respond({'profiles': profiles()})
                if self.path == '/api/dingtalk-conversations':
                    from dingtalk import conversations
                    profile = str(data.get('profile', ''))
                    if not profile or profile != config.data['dingtalk_profile']:
                        raise ValueError('请先在设置中连接并保存钉钉账号')
                    return self.respond(conversations(profile))
                if self.path == '/api/dingtalk-connect':
                    from dingtalk import conversations
                    profile = str(data.get('profile', ''))
                    if not profile:
                        raise ValueError('请选择钉钉账号')
                    # Discovery must not mutate the running adapter's identity.
                    probe = DingTalk(config)
                    return self.respond({**probe.connect(profile), **conversations(profile)})
                if self.path == '/api/scan':
                    engine.scan()
                    return self.respond(state_for(platform))
                if self.path == '/api/settings':
                    with settings_lock:
                        if data.get('dingtalk_profile', config.data['dingtalk_profile']) != config.data['dingtalk_profile'] and 'dingtalk_conversation' not in data:
                            data['dingtalk_conversation'] = ''
                            data['dingtalk_name'] = ''
                        changed = {k for k, v in data.items() if k in config.data and v != config.data[k] and not (k in ('api_key', 'dingtalk_vision_key') and not v)}
                        if changed & {'dingtalk_profile', 'dingtalk_conversation', 'dingtalk_name'} and data.get('dingtalk_conversation'):
                            from dingtalk import conversations
                            listing = conversations(data.get('dingtalk_profile', config.data['dingtalk_profile']))
                            matches = [c for c in listing['conversations'] if c['id'] == data['dingtalk_conversation']]
                            if len(matches) != 1:
                                raise ValueError('请重新连接钉钉并从会话列表中选择监听对象')
                            data['dingtalk_name'] = matches[0]['name']
                        # Invalidate in-flight replies before replacing configuration.
                        with engines['wechat'].lock, engines['dingtalk'].lock:
                            validate({**config.data, **{k:v for k,v in data.items() if k in config.data and not (k in ('api_key', 'dingtalk_vision_key') and not v)}})
                            for name, worker in engines.items():
                                if any(not k.startswith(('wechat_', 'dingtalk_')) or k.startswith(name + '_') for k in changed - {'style', 'wechat_reply_latest', 'dingtalk_reply_latest'}):
                                    worker.stop('设置已更新，请重新开启自动回复')
                            result = config.save(data)
                    return self.respond(result)
                if self.path == '/api/start':
                    if not isinstance(data.get('reply_latest', False), bool):
                        raise ValueError('立即回复选项必须是勾选状态')
                    engine.start(reply_latest=data.get('reply_latest', config.data[platform + '_reply_latest']))
                elif self.path == '/api/stop':
                    engine.stop()
                elif self.path == '/api/test-image':
                    if platform != 'wechat':
                        raise ValueError('图片测试仅支持微信')
                    if data.get('confirm') is not True:
                        raise ValueError('请先确认发送测试图片')
                    if engine.enabled or engine.starting:
                        raise ValueError('请先暂停自动回复再测试')
                    from stickers import render
                    from image_sender import send_image
                    from engine import digest
                    from config import atomic_json
                    snapshot = adapter.read()
                    if snapshot.get('chat_title') != '文件传输助手':
                        raise ValueError('测试图片只能发到文件传输助手，请先切换到该会话')
                    key = 'image-test:' + digest(snapshot)
                    if key in engine.processed:
                        raise ValueError('这次测试已执行或结果未确认，不会重复发送')
                    engine.processed.add(key)
                    atomic_json(engine.ledger_path, sorted(engine.processed))
                    path = str(render('自然友好', '收到'))
                    try:
                        sent = send_image(adapter, path, snapshot, lambda: not engine.enabled)
                    except ValueError as error:
                        if str(error) == '微信不在前台或已经暂停，图片未发送':
                            engine.processed.discard(key)
                            atomic_json(engine.ledger_path, sorted(engine.processed))
                        raise
                    with engine.lock:
                        engine.processed.add('image:' + digest(sent))
                        atomic_json(engine.ledger_path, sorted(engine.processed))
                        engine.latest = {'incoming': '图片发送测试', 'reply': '[表情图片] 收到',
                                         'state': '已发送并确认 · 文件传输助手', 'image_path': path}
                        engine.status = '图片发送测试已确认，自动回复未开启'
                        engine.event('测试图片已发送并确认 · 文件传输助手')
                    return self.respond({'message': '测试图片已发送并确认'})
                elif self.path == '/api/test':
                    merged = {**config.data, **{k: v for k, v in data.items() if k in config.data and v}}
                    if merged['base_url'].rstrip('/') != config.data['base_url'] and not data.get('api_key'):
                        raise ValueError('请填写新服务的 API Key')
                    completion(validate(merged), [{'role': 'user', 'content': '只回复：连接成功'}], probe=True)
                    return self.respond({'message': '连接成功，云端模型可用'})
                elif self.path == '/api/preview':
                    text = str(data.get('text', '')).strip()
                    if not text or len(text) > 2000:
                        raise ValueError('请输入 1–2000 字的测试消息')
                    with engine.lock:
                        cfg = config.data.copy()
                    if data.get('style') in STYLES:
                        cfg['style'] = data['style']
                    answer = reply(cfg, [SimpleNamespace(side='them', text=text)])
                    return self.respond({'reply': answer})
                elif self.path == '/api/permissions':
                    return self.respond({'error': '请在聊天助手 App 中使用原生申请授权按钮'}, 400)
                else:
                    return self.respond({'error': '接口不存在'}, 404)
                self.respond(state_for(platform))
            except (ValueError, CloudError) as error:
                self.respond({'error': str(error)}, 400)
            except Exception:
                self.respond({'error': '操作失败，请检查系统权限和服务状态'}, 500)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.engine = engine
    server.engines = engines
    return server


def start_server(port=18766):
    server = make_server(port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for worker in server.engines.values():
        threading.Thread(target=worker.run, daemon=True).start()
    return server


if __name__ == '__main__':
    import fcntl
    from config import DATA_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(DATA_DIR / 'service.lock', 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('助手已在运行')
    import os
    import sys
    import time
    server = start_server()
    if '--parent-pid' in sys.argv:
        parent = int(sys.argv[sys.argv.index('--parent-pid') + 1])
        def watch_parent():
            while os.getppid() == parent:
                time.sleep(0.25)
            for worker in server.engines.values():
                worker.stop('桌面应用已关闭')
            os._exit(0)
        threading.Thread(target=watch_parent, daemon=True).start()
    print('聊天助手 http://127.0.0.1:18766', flush=True)
    threading.Event().wait()
