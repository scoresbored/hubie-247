#!/usr/bin/env bash
# Server watchdog for Hubie (run by cron every 5 minutes as root, see deploy/hubie.cron).
# Hubie writes /opt/hubie/logs/heartbeat every 30s. If it's older than 10 minutes the app has frozen
# without crashing (run.sh only restarts it when it exits), so restart it here. No human involved.
HB=/opt/hubie/logs/heartbeat
LOG=/opt/hubie/logs/watchdog.log
now=$(date +%s)
last=$(cat "$HB" 2>/dev/null || echo 0)
# Only act if Hubie is supposed to be running (its tmux session exists)
if sudo -u hubie tmux has-session -t =hubie 2>/dev/null; then
  if [ $((now - last)) -gt 600 ]; then
    echo "$(date -u +%FT%TZ) heartbeat $((now - last))s old: restarting hubie" >> "$LOG"
    sudo -u hubie bash -c 'cd /opt/hubie/app && ./stop.sh >/dev/null 2>&1; sleep 3; ./start.sh >/dev/null 2>&1'
  fi
fi
