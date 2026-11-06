#!/usr/bin/env bash
# Pulls the latest code from GitHub and restarts Hubie. Run as the hubie user in /opt/hubie/app.
set -e
cd "$(dirname "$0")"
before=$(git rev-parse --short HEAD)
git pull --ff-only --quiet
after=$(git rev-parse --short HEAD)
/opt/hubie/venv/bin/pip install --quiet httpx pillow piper-tts websockets >/dev/null 2>&1 || true
chmod +x ./*.sh
if [ "$before" = "$after" ]; then
  echo "Already up to date ($after)."
  exit 0
fi
echo "Updated $before -> $after:"
git log --oneline "$before..$after" | head -10
if [ "${1:-}" != "--no-restart" ]; then
  ./stop.sh >/dev/null; sleep 2; ./start.sh
fi
