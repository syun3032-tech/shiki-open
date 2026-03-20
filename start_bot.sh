#!/bin/bash
# 識ちゃん Discord Bot 起動スクリプト
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# venvがあれば使う
if [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
    exec "$SCRIPT_DIR/venv/bin/python" discord_bot.py
elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    exec "$SCRIPT_DIR/.venv/bin/python" discord_bot.py
else
    exec python3 discord_bot.py
fi
