#!/bin/zsh
set -eu
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
uv sync --frozen --python 3.12 --quiet
exec .venv/bin/python src/app.py
