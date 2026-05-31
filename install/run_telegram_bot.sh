#!/bin/bash
# run_telegram_bot.sh — 启动 Telegram Bot
# 由 LaunchAgent com.sjtu.telegram-bot 调用

set -e
cd "$(dirname "$0")/.."

# 激活虚拟环境（若存在）
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

exec python3 scripts/telegram_bot.py
