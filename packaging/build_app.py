import os
import hashlib
import json
import plistlib
import platform
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
app = root / 'dist/聊天助手.app'
resources = app / 'Contents/Resources'
macos = app / 'Contents/MacOS'
resources.mkdir(parents=True, exist_ok=True)
macos.mkdir(parents=True, exist_ok=True)
shutil.copy2(root / 'assets/AppIcon.icns', resources / 'AppIcon.icns')
for dirname in ('src', 'web'):
    shutil.copytree(root / dirname, resources / 'app' / dirname, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
for name in ('pyproject.toml', 'uv.lock', 'LICENSE', 'README.md'):
    shutil.copy2(root / name, resources / 'app' / name)
with (app / 'Contents/Info.plist').open('wb') as f:
    plistlib.dump({'CFBundleName': '聊天助手', 'CFBundleDisplayName': '聊天助手',
                  'CFBundleIdentifier': 'local.xizheng.wechat-chat-assistant',
                  'CFBundleVersion': '1.0.0', 'CFBundleShortVersionString': '1.0.0',
                  'CFBundlePackageType': 'APPL', 'CFBundleExecutable': 'wechat-chat-assistant',
                  'CFBundleIconFile': 'AppIcon.icns',
                  'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '14.0', 'NSPrincipalClass': 'NSApplication',
                  'NSAppleEventsUsageDescription': '用于将回复填入并发送到您指定的聊天会话。'}, f)
launcher = macos / 'wechat-chat-assistant'
# Preserve the native code identity for Python/UI-only updates. A changed native
# launcher still needs an explicit permission refresh on ad-hoc-signed local builds.
source = root / 'packaging/Launcher.swift'
marker = app.parent / '.launcher-build.json'
fingerprint = hashlib.sha256(source.read_bytes() + platform.machine().encode() + b'macos14-O').hexdigest()
saved = json.loads(marker.read_text()).get('fingerprint') if marker.exists() else None
# Adopt this task's already-built launcher when no native source changed since build.
if saved is None and launcher.exists() and launcher.stat().st_mtime >= source.stat().st_mtime:
    saved = fingerprint
if not launcher.exists() or saved != fingerprint:
    subprocess.run(['swiftc', '-O', '-target', platform.machine() + '-apple-macosx14.0', str(source), '-o', str(launcher),
                    '-framework', 'AppKit', '-framework', 'WebKit'], check=True)
marker.write_text(json.dumps({'fingerprint': fingerprint}))
# Refresh Launch Services so Dock/Finder replace previously cached placeholder icons.
registry = Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')
if registry.exists():
    subprocess.run([str(registry), '-f', str(app)], check=True)
app.touch()
print(app)
