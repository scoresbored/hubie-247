#!/usr/bin/env bash
# Stops Hubie completely (stream, game browser, restart loop).
pkill -f "[r]un.sh"; pkill -f "[h]ubie.main"; pkill -x ffmpeg; pkill -f "[c]hrome"
tmux kill-session -t hubie 2>/dev/null
sleep 2
pgrep -a ffmpeg || echo "stopped, nothing streaming"
