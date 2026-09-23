#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
.venv/bin/python script/relaunch.py "${1:-run}"
