#!/usr/bin/env bash
# Starts Hubie in the background (tmux session "hubie"). Watch with: tmux attach -t hubie  (leave with Ctrl+B then D)
cd "$(dirname "$0")"
tmux has-session -t hubie 2>/dev/null && { echo "Hubie is already running"; exit 0; }
tmux new -d -s hubie "./run.sh $*"
echo "Hubie started. Watch: tmux attach -t hubie"
