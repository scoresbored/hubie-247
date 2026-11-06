#!/usr/bin/env bash
# Hubie 24/7 streamer - server bootstrap for Ubuntu 24.04.
# Run once as root:  bash bootstrap.sh
set -euo pipefail

APP_USER=hubie
APP_DIR=/opt/hubie
export DEBIAN_FRONTEND=noninteractive

echo "==> Base packages"
apt-get update
apt-get -y upgrade
apt-get install -y \
  git curl jq unzip htop tmux ufw chrony \
  python3 python3-venv python3-pip \
  xvfb x11-utils xdotool \
  pulseaudio pulseaudio-utils \
  ffmpeg \
  fonts-noto fonts-noto-color-emoji
timedatectl set-timezone UTC

echo "==> No surprise reboots or background upgrades (either one would reset the run)"
apt-get remove -y unattended-upgrades || true
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer || true

echo "==> 8G swap as an OOM cushion"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 8G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -w vm.swappiness=10
echo 'vm.swappiness=10' > /etc/sysctl.d/99-hubie.conf

echo "==> Firewall: SSH in, everything out"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

echo "==> App user ($APP_USER), no sudo"
id "$APP_USER" &>/dev/null || useradd -m -s /bin/bash "$APP_USER"
if [ -s /root/.ssh/authorized_keys ]; then
  install -d -m 700 -o "$APP_USER" -g "$APP_USER" /home/$APP_USER/.ssh
  install -m 600 -o "$APP_USER" -g "$APP_USER" /root/.ssh/authorized_keys /home/$APP_USER/.ssh/authorized_keys
  # Keys are in place, so turn off password logins
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  systemctl reload ssh
else
  echo "!! No root SSH key found; leaving password auth on. Add a key, then rerun."
fi
loginctl enable-linger "$APP_USER"   # lets the user's services run without a login

echo "==> Python venv + Playwright Chromium"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip playwright
"$APP_DIR/venv/bin/playwright" install-deps chromium
sudo -u "$APP_USER" "$APP_DIR/venv/bin/playwright" install chromium

echo "==> Smoke test: can a real (non-headless) Chromium load the games page?"
cat > "$APP_DIR/smoke_test.py" <<'PY'
import asyncio
from playwright.async_api import async_playwright

URL = "https://www.housebets.com/house-games"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--window-size=1280,720"])
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        resp = await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("HTTP status:", resp.status if resp else "no response")
        await page.wait_for_timeout(8000)  # let challenges / JS settle
        print("Page title:", await page.title())
        await page.screenshot(path="/opt/hubie/smoke.png")
        print("Screenshot saved to /opt/hubie/smoke.png")
        await browser.close()

asyncio.run(main())
PY
chown "$APP_USER:$APP_USER" "$APP_DIR/smoke_test.py"
sudo -u "$APP_USER" xvfb-run -a -s "-screen 0 1280x720x24" \
  "$APP_DIR/venv/bin/python" "$APP_DIR/smoke_test.py" || echo "!! Smoke test failed; send me the output"

echo
echo "Done. Pull the screenshot to check it:"
echo "  scp $APP_USER@<server-ip>:/opt/hubie/smoke.png ."
