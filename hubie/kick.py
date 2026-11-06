"""Kick chat: read (Pusher websocket, no public URL needed), reply + moderate (official Kick API).

One-time setup:  /opt/hubie/venv/bin/python -m hubie.kick_auth   (saves tokens to /opt/hubie/kick_tokens.json)
.env needs: KICK_CLIENT_ID, KICK_CLIENT_SECRET, KICK_CHATROOM_ID
"""
import asyncio
import json
import os
import time

import httpx
import websockets

from .config import ROOT
from .log import log

API = "https://api.kick.com/public/v1"
TOKEN_URL = "https://id.kick.com/oauth/token"
TOKENS = ROOT / "kick_tokens.json"
PUSHER = ("wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
          "?protocol=7&client=js&version=8.4.0&flash=false")


class Kick:
    def __init__(self):
        self.client_id = os.environ.get("KICK_CLIENT_ID", "")
        self.secret = os.environ.get("KICK_CLIENT_SECRET", "")
        self.chatroom = os.environ.get("KICK_CHATROOM_ID", "")
        self.http = httpx.AsyncClient(timeout=15)
        self.tok = json.loads(TOKENS.read_text()) if TOKENS.exists() else {}
        self.me_id = None
        self.me_name = ""
        self.enabled = bool(self.client_id and self.secret and self.tok.get("refresh_token"))

    # ---------- auth ----------
    async def _refresh(self):
        r = await self.http.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": self.tok["refresh_token"],
            "client_id": self.client_id, "client_secret": self.secret})
        r.raise_for_status()
        new = r.json()
        new["obtained_at"] = time.time()
        self.tok.update(new)
        TOKENS.write_text(json.dumps(self.tok))
        os.chmod(TOKENS, 0o600)
        log("kick_token_refreshed")

    async def _token(self):
        age = time.time() - self.tok.get("obtained_at", 0)
        if age > max(60, int(self.tok.get("expires_in", 3600)) - 300):
            await self._refresh()
        return self.tok["access_token"]

    async def _req(self, method, path, **kw):
        for attempt in (1, 2):
            h = {"Authorization": f"Bearer {await self._token()}", "Accept": "application/json"}
            r = await self.http.request(method, API + path, headers=h, **kw)
            if r.status_code == 401 and attempt == 1:
                await self._refresh()
                continue
            return r

    async def start(self):
        if not self.enabled:
            log("kick_disabled", why="missing KICK_CLIENT_ID/SECRET or tokens (run hubie.kick_auth)")
            return False
        r = await self._req("GET", "/users")
        data = (r.json().get("data") or [{}])[0]
        self.me_id, self.me_name = data.get("user_id"), (data.get("name") or "").lower()
        log("kick_ready", user=self.me_name, user_id=self.me_id, chatroom=self.chatroom)
        return bool(self.me_id)

    # ---------- write ----------
    async def say(self, text):
        try:
            r = await self._req("POST", "/chat", json={
                "type": "user", "broadcaster_user_id": self.me_id, "content": text[:480]})
            if r.status_code >= 300:
                log("kick_say_error", status=r.status_code, body=r.text[:200])
        except Exception as e:
            log("kick_say_error", err=repr(e)[:200])

    async def timeout(self, user_id, minutes, reason):
        try:
            r = await self._req("POST", "/moderation/bans", json={
                "broadcaster_user_id": self.me_id, "user_id": int(user_id),
                "duration": int(minutes), "reason": reason[:100]})
            log("kick_timeout", user_id=user_id, minutes=minutes, reason=reason, status=r.status_code)
        except Exception as e:
            log("kick_mod_error", err=repr(e)[:200])

    # ---------- read ----------
    async def read_chat(self, on_message):
        """Reads chat by opening Kick's own pop-out chat page in a browser and listening to the
        live messages Kick sends it. This keeps working even when Kick changes its chat servers.
        Calls on_message(username, user_id, text, message_id). Reconnects forever."""
        from playwright.async_api import async_playwright
        slug = os.environ.get("KICK_CHANNEL", "housebetshubie").lower()
        url = f"https://kick.com/popout/{slug}/chat"
        seen = set()
        last_frame = [time.time()]

        samples = [0]

        def find_messages(obj, depth=0):
            """Kick's chat format changes; just look for anything shaped like a chat message."""
            if depth > 8:
                return
            if isinstance(obj, str) and obj[:1] in "{[":
                try:
                    obj = json.loads(obj)
                except Exception:
                    return
            if isinstance(obj, dict):
                sender = obj.get("sender")
                if isinstance(sender, dict) and sender.get("username") and "content" in obj:
                    yield obj
                    return
                for v in obj.values():
                    yield from find_messages(v, depth + 1)
            elif isinstance(obj, list):
                for v in obj:
                    yield from find_messages(v, depth + 1)

        async def handle(raw):
            last_frame[0] = time.time()
            if isinstance(raw, bytes):
                raw = raw.decode(errors="replace")
            found = False
            for line in str(raw).splitlines():          # some servers batch several JSON messages
                for d in find_messages(line):
                    found = True
                    mid = d.get("id")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    if len(seen) > 5000:
                        seen.clear()
                    sender = d["sender"]
                    name = sender.get("username") or "someone"
                    if name.lower() == self.me_name:
                        continue
                    await on_message(name, sender.get("id"), d.get("content") or "", mid)
            if not found and samples[0] < 8 and len(str(raw)) > 20:
                samples[0] += 1
                log("kick_frame_sample", data=str(raw)[:400])

        pw = await async_playwright().start()
        while True:
            browser = None
            try:
                browser = await pw.chromium.launch(headless=False, args=[
                    "--window-size=500,700", "--mute-audio", "--disable-blink-features=AutomationControlled"])
                page = await browser.new_page()

                def on_ws(ws):
                    log("kick_chat_socket", url=ws.url.split("?")[0])
                    ws.on("framereceived", lambda p: asyncio.ensure_future(handle(p)))
                page.on("websocket", on_ws)
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                log("kick_chat_connected", via="popout page")
                # Kick sends pings regularly; if nothing at all arrives for 5 min, reload.
                last_frame[0] = time.time()
                while time.time() - last_frame[0] < 300:
                    await asyncio.sleep(10)
                log("kick_chat_down", err="no websocket frames for 5 min, reloading")
            except Exception as e:
                log("kick_chat_down", err=repr(e)[:200])
            finally:
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
            await asyncio.sleep(5)
