#!/usr/bin/env bash
# Starts Hubie: virtual screen, then the app. If the app ever exits, it restarts.
#   ./run.sh            stream to Kick
#   ./run.sh --offline  no stream, writes /opt/hubie/logs/preview.png every 10s
# (Audio no longer uses PulseAudio: the app mixes Hubie's voice straight into the stream.)
cd "$(dirname "$0")"
export DISPLAY=:99
mkdir -p /opt/hubie/logs
pgrep -u "$(id -u)" -f "Xvfb :99" >/dev/null || { Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/dev/null 2>&1 & sleep 1; }
while true; do
  /opt/hubie/venv/bin/python -m hubie.main "$@"
  echo "hubie exited ($?), restarting in 5s" >&2
  sleep 5
done
