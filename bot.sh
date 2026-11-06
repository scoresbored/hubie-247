#!/usr/bin/env bash
# Starts the Telegram control bot in its own tmux session (separate from Hubie, so it can restart him).
cd "$(dirname "$0")"
tmux has-session -t hubiebot 2>/dev/null && { echo "bot already running (tmux attach -t hubiebot)"; exit 0; }
tmux new -d -s hubiebot "while true; do /opt/hubie/venv/bin/python -m hubie.tgbot; echo 'bot exited, restarting in 5s'; sleep 5; done"
echo "Telegram bot started. Watch: tmux attach -t hubiebot"
