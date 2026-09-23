"""Stop only this project's native launcher, wait for its worker, build, open."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
root = Path(__file__).resolve().parent.parent
mode = sys.argv[1] if len(sys.argv) > 1 else 'run'
if mode not in ('run','--verify'):
    raise SystemExit('Usage: script/build_and_run.sh [run|--verify]')
launchers = [str(root / ('dist/' + name + '.app/Contents/MacOS/wechat-chat-assistant')) for name in ('微信聊天助手','聊天助手')]
workers = [str(root / ('dist/' + name + '.app/Contents/Resources/app/src/server.py')) for name in ('微信聊天助手','聊天助手')]
rows = subprocess.check_output(['ps','-axo','pid=,command='],text=True).splitlines()
owned=[]
for row in rows:
    pid, _, command = row.strip().partition(' ')
    if command.strip() in launchers or any(path in command and '--parent-pid' in command for path in workers):
        owned.append(int(pid))
        if command.strip() in launchers:
            os.kill(int(pid), signal.SIGTERM)
for _ in range(100):
    alive=[]
    for pid in owned:
        try: os.kill(pid,0); alive.append(pid)
        except ProcessLookupError: pass
    if not alive: break
    time.sleep(.1)
else:
    raise SystemExit('原应用仍在退出，请稍后重试；未启动重复实例')
subprocess.run([sys.executable,str(root/'packaging/build_app.py')],check=True)
app=root/'dist/聊天助手.app'
subprocess.run(['/usr/bin/open','-n',str(app)],check=True)
if mode=='--verify':
    for _ in range(80):
        try:
            body=urllib.request.urlopen('http://127.0.0.1:18766/',timeout=.3).read().decode()
            if '<title>聊天助手</title>' in body:
                print('聊天助手已启动，新版页面可访问');break
        except OSError:pass
        time.sleep(.25)
    else:raise SystemExit('新版后台尚未就绪，请检查启动窗口')
