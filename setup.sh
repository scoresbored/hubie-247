#!/usr/bin/env bash
# One-time setup for Hubie v1. Run as root:  bash /opt/hubie/app/setup.sh
set -euo pipefail
V=/opt/hubie/voices
sudo -u hubie /opt/hubie/venv/bin/pip install --quiet httpx pillow piper-tts websockets numpy
install -d -o hubie -g hubie "$V" /opt/hubie/logs
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high
for f in en_US-ryan-high.onnx en_US-ryan-high.onnx.json; do
  [ -s "$V/$f" ] || sudo -u hubie curl -sSL -o "$V/$f" "$BASE/$f"
done
chown -R hubie:hubie /opt/hubie/app
chmod +x /opt/hubie/app/run.sh
echo "Testing the voice..."
echo "Hubie voice test." | sudo -u hubie /opt/hubie/venv/bin/python -m piper -m "$V/en_US-ryan-high.onnx" -f /tmp/hubie_voice_test.wav \
  && echo "VOICE OK" || echo "!! voice test failed (Hubie will still run, captions only)"
echo "SETUP DONE"
