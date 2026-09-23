"""Build and open the native macOS application from the source checkout."""
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, str(root / 'packaging/build_app.py')], check=True)
    subprocess.run(['open', str(root / 'dist/聊天助手.app')], check=True)
