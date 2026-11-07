"""Telegram control panel for Hubie. Runs separately from Hubie, so it can start/stop/restart him.

.env:  TG_BOT_TOKEN=...   TG_ADMIN_IDS=123456789   (only these Telegram user IDs can use it)
Start: ./bot.sh   (tmux session "hubiebot")

READ-ONLY (safe during the contest run): /status /preview /logs /errors /chat /run
CONTROL (counts as a human action during the run, asks to confirm): /start /stop /restart
OPTIONAL, OFF BY DEFAULT (future feature): /poll /giveaway - only work with HUBIE_HUMAN_CONTROLS=1,
and every use is logged as a human touch, since the contest counts any human action against the run.
"""
import asyncio
import json
import os
import subprocess
import time
from collections import Counter, deque
from pathlib import Path

import httpx

from .config import LOG_DIR, ROOT

TOKEN = os.environ.get("TG_BOT_TOKEN", "")
ADMINS = {int(x) for x in os.environ.get("TG_ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().isdigit()}
API = f"https://api.telegram.org/bot{TOKEN}"
APP = Path(__file__).resolve().parent.parent
STATE = ROOT / "run_state.json"
LOG = LOG_DIR / "hubie.jsonl"
HELP = (
    "<b>Hubie control</b>\n"
    "/status - is he live, what he's playing, health\n"
    "/preview - current stream picture\n"
    "/logs - recent things he said and did\n"
    "/errors - recent problems\n"
    "/chat - recent chat replies\n"
    "/run - contest run timer (start/end it)\n\n"
    "<b>Controls</b> (during the contest run these RESET the run)\n"
    "/start  /stop  /restart  /update (pull latest code from GitHub + restart)\n\n"
    "<b>Community controls</b> (future feature, off during contest runs)\n"
    "/poll - start a chat poll for Hubie's next game now\n"
    "/giveaway 5 Shoutout from Hubie - 5-minute giveaway, viewers type !enter"
)
HUMAN_CONTROLS = os.environ.get("HUBIE_HUMAN_CONTROLS", "0") == "1"
OFF_NOTE = ("🧪 <b>Community controls are a planned feature, switched off for the contest.</b>\n"
            "Hubie already runs polls by himself every other segment. Live /poll and /giveaway would be a "
            "human action, which resets the contest run, so they stay off.\n"
            "To try them outside a run: add <code>HUBIE_HUMAN_CONTROLS=1</code> to /opt/hubie/.env and restart.")


def tail(n=4000):
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 1_500_000))
            lines = f.read().decode(errors="replace").splitlines()[-n:]
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def ago(ts):
    try:
        t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except Exception:
        return "?"
    s = int(time.time() - t)
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago" if s < 7200 else f"{s // 3600}h ago"


def dur(secs):
    secs = int(secs)
    return f"{secs // 3600}h {secs % 3600 // 60}m"


def proc_uptime(pattern):
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
        if not out:
            return None
        et = subprocess.run(["ps", "-o", "etimes=", "-p", out[0]], capture_output=True, text=True).stdout.strip()
        return int(et or 0)
    except Exception:
        return None


def run_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def status_text():
    recs = tail()
    hubie_up = proc_uptime(r"[h]ubie\.main")
    ff_up = proc_uptime("[f]fmpeg -hide_banner")
    now = time.time()

    def within(r, secs):
        try:
            return now - (time.mktime(time.strptime(r["t"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone) < secs
        except Exception:
            return False

    last = {}
    for r in recs:
        last[r.get("kind")] = r
    hour = [r for r in recs if within(r, 3600)]
    kinds = Counter(r.get("kind") for r in hour)
    rounds = [r for r in hour if r.get("kind") == "round"]
    hits = sum(1 for r in rounds if r.get("win"))
    sw = last.get("switched", {})
    lines = []
    lines.append(f"🦥 Hubie: {'🟢 running ' + dur(hubie_up) if hubie_up is not None else '🔴 stopped'}")
    lines.append(f"📡 Stream: {'🟢 sending ' + dur(ff_up) if ff_up is not None else '🔴 not sending'}"
                 f" · drops last hour: {kinds.get('stream_down', 0)}")
    if sw:
        lines.append(f"🎲 Playing: {sw.get('game')} – {sw.get('strategy')} ({ago(sw.get('t'))})")
    lines.append(f"🎯 Last hour: {len(rounds)} rounds, {hits} hits · spoke {kinds.get('say', 0)} lines")
    lines.append(f"💬 Chat: {kinds.get('chat_reply', 0)} replies, {kinds.get('kick_timeout', 0)} timeouts last hour")
    cd = last.get("kick_chat_down", {}).get("t", "")
    cc = last.get("kick_chat_connected", {}).get("t", "")
    lines.append(f"   chat feed: {'🟢 connected' if cc >= cd and cc else '🟠 reconnecting' if cd else '⚪ unknown'}")
    errs = sum(v for k, v in kinds.items() if k and ("error" in k or k in ("benched", "STOP_NONZERO_BET")))
    lines.append(f"⚠️ Errors last hour: {errs}")
    try:
        load = open("/proc/loadavg").read().split()[0]
        mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if ":" in l}
        used = 100 - 100 * mem["MemAvailable"] // mem["MemTotal"]
        lines.append(f"🖥 Server: load {load} · memory {used}%")
    except Exception:
        pass
    st = run_state()
    if st.get("run_started"):
        lines.append(f"⏱ Contest run: {dur(now - st['run_started'])} (since {time.strftime('%b %d %H:%M UTC', time.gmtime(st['run_started']))})")
    return "\n".join(lines)


class Bot:
    def __init__(self):
        self.http = httpx.AsyncClient(timeout=70)
        self.offset = 0
        self.pending = {}                   # chat -> giveaway waiting for confirmation

    async def call(self, method, **kw):
        files = kw.pop("files", None)
        r = await self.http.post(f"{API}/{method}", data=kw if files else None, json=None if files else kw, files=files)
        return r.json()

    async def send(self, chat, text, buttons=None):
        kw = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            kw["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        await self.call("sendMessage", **kw)

    async def confirm(self, chat, action):
        in_run = bool(run_state().get("run_started"))
        warn = ("⚠️ <b>The contest run is on.</b> This is a human action: it <b>resets your run to zero</b>.\n"
                if in_run else "")
        await self.send(chat, f"{warn}Really <b>{action}</b> Hubie?",
                        [[(f"Yes, {action}" + (" (reset run)" if in_run else ""), f"do:{action}"), ("Cancel", "cancel")]])

    async def script(self, chat, name):
        p = await asyncio.create_subprocess_exec(f"./{name}.sh", cwd=str(APP),
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await p.communicate()
        await self.send(chat, f"<code>{out.decode(errors='replace')[-800:] or 'done'}</code>")

    async def do(self, chat, action):
        st = run_state()
        if st.get("run_started") and action in ("stop", "restart", "start", "update", "poll", "giveaway"):
            st["reset_by"] = action
            st.pop("run_started", None)
            STATE.write_text(json.dumps(st))
            await self.send(chat, "⏱ Contest run timer cleared (human action).")
        if action == "stop":
            await self.script(chat, "stop")
        elif action == "start":
            await self.script(chat, "start")
        elif action == "restart":
            await self.script(chat, "stop")
            await asyncio.sleep(3)
            await self.script(chat, "start")
        elif action == "update":
            await self.script(chat, "update")
            await self.send(chat, "🤖 Restarting the bot to load any bot changes...")
            await asyncio.sleep(1)
            os._exit(0)                     # bot.sh's loop starts the new version in 5s
        elif action in ("poll", "giveaway"):
            cmd = {"cmd": action, "t": time.time()}
            if action == "giveaway":
                cmd.update(self.pending.pop(chat, {}))
                if not cmd.get("prize"):
                    await self.send(chat, "That giveaway expired, send /giveaway again.")
                    return
            inbox = ROOT / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            tmp = inbox / f".{int(time.time() * 1000)}.tmp"
            tmp.write_text(json.dumps(cmd))
            tmp.rename(inbox / f"{int(time.time() * 1000)}.json")
            await self.send(chat, "✅ Sent to Hubie" + (": poll starting." if action == "poll"
                                                       else f": giveaway for <b>{cmd['prize']}</b>, {cmd['minutes']:g} min."))
        elif action == "runstart":
            STATE.write_text(json.dumps({"run_started": time.time()}))
            await self.send(chat, "⏱ Contest run timer started. Hands off from here! Only read-only commands now.")
        elif action == "runend":
            STATE.write_text(json.dumps({}))
            await self.send(chat, "⏱ Contest run timer ended.")

    async def handle(self, upd):
        if "callback_query" in upd:
            q = upd["callback_query"]
            if q["from"]["id"] not in ADMINS:
                return
            await self.call("answerCallbackQuery", callback_query_id=q["id"])
            chat = q["message"]["chat"]["id"]
            data = q.get("data", "")
            if data.startswith("do:"):
                await self.do(chat, data[3:])
            else:
                await self.send(chat, "Cancelled.")
            return
        msg = upd.get("message") or {}
        user = (msg.get("from") or {}).get("id")
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat or not text:
            return
        if user not in ADMINS:
            await self.send(chat, f"Not authorized. Your Telegram ID is <code>{user}</code>.")
            return
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/help", "/menu"):
            await self.send(chat, HELP)
        elif cmd == "/status":
            await self.send(chat, status_text())
        elif cmd == "/preview":
            p = LOG_DIR / "preview.png"
            if p.exists() and time.time() - p.stat().st_mtime < 300:
                with open(p, "rb") as f:
                    await self.call("sendPhoto", chat_id=str(chat),
                                    caption=f"Stream picture, {int(time.time() - p.stat().st_mtime)}s old",
                                    files={"photo": ("preview.png", f.read(), "image/png")})
            else:
                await self.send(chat, "No fresh picture. Is Hubie running? (/status)")
        elif cmd == "/logs":
            keep = ("say", "switched", "chat_reply", "kick_timeout", "benched", "stream_start", "stream_down")
            recs = [r for r in tail(600) if r.get("kind") in keep][-15:]
            lines = []
            for r in recs:
                k = r.get("kind")
                detail = r.get("line") or r.get("game") or r.get("to") or r.get("reason") or r.get("err") or ""
                if k == "switched":
                    detail = f"{r.get('game')} – {r.get('strategy')}"
                lines.append(f"<b>{k}</b> {ago(r.get('t'))}: {str(detail)[:140]}")
            await self.send(chat, "\n".join(lines) or "No log yet.")
        elif cmd == "/errors":
            recs = [r for r in tail(2000) if "error" in (r.get("kind") or "") or r.get("kind") in
                    ("benched", "stream_down", "kick_chat_down", "STOP_NONZERO_BET", "line_rejected")][-12:]
            await self.send(chat, "\n".join(f"<b>{r['kind']}</b> {ago(r.get('t'))}: "
                                            f"{str(r.get('err') or r.get('game') or r.get('raw') or '')[:150]}"
                                            for r in recs) or "No errors. 🎉")
        elif cmd == "/chat":
            recs = [r for r in tail(1500) if r.get("kind") in ("chat_reply", "kick_timeout")][-12:]
            await self.send(chat, "\n".join(
                f"{'🔇' if r['kind'] == 'kick_timeout' else ('🗣' if r.get('spoken') else '💬')} "
                f"{r.get('to') or r.get('user_id')} {ago(r.get('t'))}"
                + (f" – timeout {r.get('minutes')}m: {r.get('reason')}" if r['kind'] == 'kick_timeout' else "")
                for r in recs) or "No chat yet.")
        elif cmd == "/run":
            st = run_state()
            if st.get("run_started"):
                await self.send(chat, f"⏱ Run going for {dur(time.time() - st['run_started'])}.",
                                [[("End run timer", "do:runend"), ("Cancel", "cancel")]])
            else:
                await self.send(chat, "Start the contest run timer now? From then on, /start /stop /restart will warn you.",
                                [[("Start run timer", "do:runstart"), ("Cancel", "cancel")]])
        elif cmd in ("/start", "/stop", "/restart", "/update"):
            await self.confirm(chat, cmd[1:])
        elif cmd in ("/poll", "/giveaway"):
            if not HUMAN_CONTROLS:
                await self.send(chat, OFF_NOTE)
            elif cmd == "/poll":
                await self.confirm(chat, "poll")
            else:
                from .safety import safe_prize
                parts = text.split(maxsplit=2)
                try:
                    minutes = max(1.0, min(15.0, float(parts[1])))
                    prize = safe_prize(parts[2])
                except (IndexError, ValueError):
                    await self.send(chat, "Use: <code>/giveaway 5 Shoutout from Hubie</code> (minutes, then the prize)")
                    return
                if not prize:
                    await self.send(chat, "❌ Prizes have to be non-monetary: no cash, crypto, bonuses, codes or "
                                          "credits (demo-only stream). Try a shoutout, picking Hubie's next game, "
                                          "or a gifted Kick sub.")
                    return
                self.pending[chat] = {"prize": prize, "minutes": minutes}
                await self.confirm(chat, "giveaway")
        else:
            await self.send(chat, HELP)

    async def run(self):
        if not TOKEN or not ADMINS:
            raise SystemExit("Set TG_BOT_TOKEN and TG_ADMIN_IDS in /opt/hubie/.env")
        await self.call("setMyCommands", commands=[
            {"command": c, "description": d} for c, d in [
                ("status", "Is Hubie live + health"), ("preview", "Current stream picture"),
                ("logs", "What he said and did"), ("errors", "Recent problems"), ("chat", "Recent chat replies"),
                ("run", "Contest run timer"), ("start", "Start Hubie"), ("stop", "Stop Hubie"),
                ("restart", "Restart Hubie"), ("update", "Pull latest code + restart"),
                ("poll", "Start a chat poll (future, off)"), ("giveaway", "Run a giveaway (future, off)")]])
        for a in ADMINS:
            try:
                await self.send(a, "🦥 Hubie control bot is online. /status")
            except Exception:
                pass
        while True:
            try:
                r = await self.call("getUpdates", offset=self.offset, timeout=50)
                for upd in r.get("result", []):
                    self.offset = upd["update_id"] + 1
                    try:
                        await self.handle(upd)
                    except Exception as e:
                        print("handle error", repr(e))
            except Exception as e:
                print("poll error", repr(e))
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(Bot().run())
