#!/usr/bin/env bash
# Daily server cleanup for a long-running stream (cron, root). Keeps the disk from slowly filling.
journalctl --vacuum-size=200M >/dev/null 2>&1
apt-get clean >/dev/null 2>&1
find /tmp -xdev -type f -mtime +2 -not -path "/tmp/.X11-unix/*" -not -path "/tmp/hubie_tts/*" -delete 2>/dev/null
find /opt/hubie/logs -name "watchdog.log" -size +5M -exec truncate -s 1M {} \;
echo "$(date -u +%FT%TZ) daily cleanup done, $(df -h / | awk 'NR==2{print $4}') free" >> /opt/hubie/logs/watchdog.log
