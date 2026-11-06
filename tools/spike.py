#!/usr/bin/env python3
"""
Hubie game-driver spike.

Two modes:
  recon  Open the games lobby and a few games. Save screenshots, page HTML, the
         buttons/links/iframes on each page, and all JSON + websocket traffic,
         so we can learn how the House originals work.
  play   Play one game on a loop for N minutes and log every round, every
         error and every recovery. This is the endurance test.

Login: demo play on Housebets = a logged-in account betting 0. Run `spike.py login` once and log in
by hand; the session is saved in PROFILE (hubie_profile/) and reused.
Safety: before EVERY bet, play checks the Bet Amount box reads 0 (and sets
it to 0 if not); if it can't prove the bet is 0 it does not click. Keep the
Hubie account's real wallet at zero as a second safety net.
NEVER commit hubie_profile/ to git: it holds the login session.

Run on the server as the hubie user:
  cd /opt/hubie
  xvfb-run -a -s "-screen 0 1280x720x24" venv/bin/python spike.py recon
  xvfb-run -a -s "-screen 0 1280x720x24" venv/bin/python spike.py play --game <url> --minutes 60

Output goes to /opt/hubie/spike_out/ (set HUBIE_OUT to change it, e.g. on a Mac).
"""
import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright

LOBBY = "https://www.housebets.com/house-games"  # /games is a 404; the originals live here
GAME_LINK_RX = re.compile(r"/games/(housegames-|housebets-engine-)[a-z0-9-]+$")
OUT = Path(os.environ.get("HUBIE_OUT", "/opt/hubie/spike_out" if os.path.isdir("/opt/hubie") else "./spike_out"))
SHOTS = OUT / "shots"

DEMO_RX = re.compile(r"(\b(demo|play money|try fun mode|fun mode|fun play|play for fun|practice|free play)\b|^\s*fun\s*$)", re.I)  # Housebets calls it "Fun mode"
LOGIN_WALL_RX = re.compile(r"\b(log ?in|sign ?in|sign ?up|register|create account)\b", re.I)
ACTION_RX = re.compile(r"^\s*(spin|bet|place bet|play|roll|drop|deal|start|flip|cash ?out)\b", re.I)
MAX_SHOTS = 200


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Log:
    """Append-only JSON-lines log, flushed every line so a crash loses nothing."""

    def __init__(self, path):
        self.f = open(path, "a", buffering=1)

    def write(self, **kv):
        self.f.write(json.dumps({"t": now(), **kv}, default=str) + "\n")


# ---------- browser ----------

PROFILE = Path(os.environ.get("HUBIE_PROFILE", "/opt/hubie/profile" if os.path.isdir("/opt/hubie") else "./hubie_profile"))


async def open_browser(p):
    """Opens Chromium with Hubie's saved profile, so the logged-in session survives restarts."""
    context = await p.chromium.launch_persistent_context(
        str(PROFILE),
        headless=False,  # real window on the Xvfb display; looks less like a bot
        viewport={"width": 1280, "height": 720},
        args=["--window-size=1280,720", "--disable-blink-features=AutomationControlled"],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    return context, page  # context.close() works like browser.close()


def attach_network(page, log):
    """Record every JSON API response and websocket frame. Game results usually live here."""

    async def on_response(resp):
        try:
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            if "json" not in resp.headers.get("content-type", ""):
                return
            body = await resp.text()
            log.write(kind="http", url=resp.url, status=resp.status, body=body[:4000])
        except Exception:
            pass

    def on_ws(ws):
        log.write(kind="ws_open", url=ws.url)
        ws.on("framereceived", lambda d: log.write(kind="ws_in", url=ws.url, data=str(d)[:4000]))
        ws.on("framesent", lambda d: log.write(kind="ws_out", url=ws.url, data=str(d)[:2000]))

    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
    page.on("websocket", on_ws)


shot_count = 0


async def shot(page, name):
    global shot_count
    if shot_count >= MAX_SHOTS:
        return
    try:
        await page.screenshot(path=str(SHOTS / f"{name}.png"))
        shot_count += 1
    except Exception:
        pass


async def dismiss_cookies(page):
    """Click Reject on the cookie banner so it never covers the game buttons."""
    try:
        btn = page.get_by_role("button", name=re.compile(r"^\s*reject\s*$", re.I))
        if await btn.count() and await btn.first.is_visible():
            await btn.first.click(timeout=3000)
            await page.wait_for_timeout(500)
    except Exception:
        pass


def capture_catalog(page, found):
    """Collect House originals from the site's own game-search API as pages load."""

    async def on_response(resp):
        try:
            if "/api/v1/casino/search" not in resp.url:
                return
            data = await resp.json()
            for sec in data.get("sections", []):
                for g in sec.get("games", []):
                    if g.get("provider") == "housegames" or g.get("slug", "").startswith(("housegames-", "housebets-engine-")):
                        found[g["slug"]] = {"name": g.get("name"), "provider": g.get("provider_name"),
                                            "rtp": g.get("rtp"), "category": g.get("category")}
        except Exception:
            pass

    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))


async def enable_demo(page):
    """Turn on the game's demo / play-money toggle. Returns a short description or None."""
    for fr in page.frames:
        # 1) a real switch or checkbox: only click it if it's off
        for role in ("switch", "checkbox"):
            try:
                loc = fr.get_by_role(role, name=DEMO_RX)
                if await loc.count() and await loc.first.is_visible():
                    el = loc.first
                    state = await el.get_attribute("aria-checked")
                    on = state == "true" or (state is None and await el.is_checked())
                    if not on:
                        await el.click(timeout=5000)
                        await page.wait_for_timeout(1500)
                    return f"{role} (was {'on' if on else 'off'}, now on)"
            except Exception:
                continue
        # 2) a plain button or tab labelled Demo / Play money
        try:
            loc = fr.get_by_role("button", name=DEMO_RX)
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(timeout=5000)
                await page.wait_for_timeout(1500)
                return "button: " + (await loc.first.inner_text())[:30]
        except Exception:
            pass
    return None


# House originals render inside an open shadow root on this host element.
# Playwright CSS locators pierce open shadow roots (XPath does not).
GAME_HOST = '[data-housegames-direct="true"]'
BET_INPUT = 'input[aria-label="Bet Amount"]'


def _num(v):
    try:
        return float(re.sub(r"[^0-9.]", "", v or "") or "nan")
    except ValueError:
        return float("nan")


async def wait_game_ready(page, timeout=30):
    """Wait until the game's bet panel has finished connecting (input no longer a skeleton)."""
    ready = page.locator(f'{GAME_HOST} {BET_INPUT}:not([data-hg-connecting])')
    end = time.time() + timeout
    while time.time() < end:
        try:
            for i in range(await ready.count()):
                if await ready.nth(i).is_visible():
                    return ready.nth(i)
        except Exception:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("bet panel never finished connecting")


async def dismiss_popups(page):
    """Close one-off tips like 'New: tap to switch designs'."""
    try:
        b = page.get_by_role("button", name=re.compile(r"^\s*got it\s*$", re.I))
        if await b.count() and await b.first.is_visible():
            await b.first.click(timeout=2000)
    except Exception:
        pass


async def ensure_zero_bet(page):
    """Housebets' demo = betting 0. Make sure the visible Bet Amount box is 0 before EVERY bet.
    Returns the value we saw, or raises (so nothing gets clicked) if we can't prove it's 0."""
    box = await wait_game_ready(page)
    val = await box.input_value()
    if val.strip() == "" or _num(val) == 0:  # empty box shows the 0.00000000 placeholder
        return val or "0 (empty)"
    await box.fill("0")
    await box.press("Tab")
    await page.wait_for_timeout(300)
    val = await box.input_value()
    if val.strip() == "" or _num(val) == 0:
        return val or "0 (empty)"
    raise RuntimeError(f"bet amount won't go to 0 (shows {val!r})")


async def login_wall_up(page):
    """True if a log-in / register dialog popped up (= we are NOT in demo mode)."""
    for fr in page.frames:
        try:
            dlg = fr.get_by_role("dialog")
            for i in range(await dlg.count()):
                d = dlg.nth(i)
                if await d.is_visible() and LOGIN_WALL_RX.search(await d.inner_text()):
                    return True
        except Exception:
            continue
    return False


# ---------- page inspection ----------

DUMP_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const txt = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 60);
  const pick = sel => [...document.querySelectorAll(sel)].filter(vis).slice(0, 200);
  return {
    url: location.href,
    title: document.title,
    buttons: pick('button,[role=button],input[type=button],input[type=submit]').map(el => ({
      text: txt(el), id: el.id, testid: el.getAttribute('data-testid'),
      cls: (el.className || '').toString().slice(0, 80), disabled: !!el.disabled })),
    links: pick('a[href]').map(a => ({ text: txt(a), href: a.href })),
    inputs: pick('input').map(el => ({ type: el.type, name: el.name, value: el.value, ph: el.placeholder })),
    canvases: pick('canvas').length,
    iframes: [...document.querySelectorAll('iframe')].map(f => f.src),
    demo_text: /\b(demo|play money|try fun mode|fun mode|fun play|play for fun|practice|free play)\b/i.test(document.body ? document.body.innerText : ''),
    login_text: /\b(log ?in|sign ?in|sign ?up|register)\b/i.test(document.body ? document.body.innerText : ''),
  };
}"""

BALANCE_JS = r"""() => [...document.querySelectorAll('body *')]
  .filter(el => el.children.length === 0)
  .map(el => (el.innerText || '').trim())
  .filter(t => /^[$€£₮]?\s?\d[\d,]*\.\d{2,8}$/.test(t))
  .slice(0, 8)"""


async def dump_all(page):
    out = []
    for fr in page.frames:
        try:
            out.append(await fr.evaluate(DUMP_JS))
        except Exception as e:
            out.append({"url": fr.url, "error": repr(e)[:200]})
    return out


async def balances(page):
    vals = []
    for fr in page.frames:
        try:
            vals += await fr.evaluate(BALANCE_JS)
        except Exception:
            pass
    return vals[:8]


async def find_action(page, button_text=None):
    """Find the main bet/spin button in the page or any iframe."""
    rx = re.compile(rf"^\s*{re.escape(button_text)}\b", re.I) if button_text else ACTION_RX
    try:  # House originals: the main button lives in the game's shadow root
        loc = page.locator(GAME_HOST).get_by_role("button", name=rx)
        for i in range(await loc.count()):
            el = loc.nth(i)
            if await el.is_visible():
                return el
    except Exception:
        pass
    for fr in page.frames:
        try:
            loc = fr.get_by_role("button", name=rx)
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    return el
        except Exception:
            continue
    return None


async def wait_ready(btn, timeout):
    """After a click, wait for the action button to be clickable again (= round over)."""
    await asyncio.sleep(0.5)
    end = time.time() + timeout
    while time.time() < end:
        try:
            if (await btn.is_visible() and await btn.is_enabled()
                    and await btn.get_attribute("aria-busy") != "true"):
                return True
        except Exception:
            return False
        await asyncio.sleep(0.25)
    return False


# ---------- modes ----------

async def login(args):
    """One-time: you log in by hand in the window that opens. The session is saved to PROFILE."""
    print("Starting the browser... (a Chromium window will open, maybe behind Terminal)", flush=True)
    async with async_playwright() as p:
        context, page = await open_browser(p)
        print("Browser is open. Loading Housebets...", flush=True)
        try:
            await page.goto("https://www.housebets.com/house-games", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"Page was slow to load ({e.__class__.__name__}), but you can still use the window.", flush=True)
        await page.bring_to_front()
        print(f"\nA browser window is open. In it:\n"
              f"  1. Log in to the Hubie account.\n"
              f"  2. Open Dice and leave the bet amount at 0.\n"
              f"  3. Come back to this Terminal and press Enter.\n"
              f"Session will be saved to {PROFILE.resolve()}")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await context.close()
    print("Saved. recon and play will now use this login.")


async def recon(args):
    net = Log(OUT / "network.jsonl")
    async with async_playwright() as p:
        browser, page = await open_browser(p)
        attach_network(page, net)
        catalog = {}
        capture_catalog(page, catalog)

        resp = await page.goto(LOBBY, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        await dismiss_cookies(page)
        await dismiss_popups(page)
        # scroll so lazy-loaded game tiles render
        for _ in range(6):
            await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(800)
        print(f"Lobby HTTP {resp.status if resp else '??'} | title: {await page.title()}")
        await shot(page, "lobby")
        (OUT / "lobby.html").write_text(await page.content())
        lobby = await dump_all(page)
        (OUT / "lobby.json").write_text(json.dumps(lobby, indent=2))

        links, seen = [], set()
        for fr in lobby:
            for l in fr.get("links", []):
                h = l["href"].split("#")[0]
                if "housebets.com" in h and GAME_LINK_RX.search(h) and h not in seen:
                    seen.add(h)
                    links.append({"text": l["text"], "href": h})
        for slug, g in catalog.items():  # anything the API listed that the page didn't link
            h = f"https://www.housebets.com/games/{slug}"
            if h not in seen:
                seen.add(h)
                links.append({"text": g["name"] or slug, "href": h})
        (OUT / "catalog.json").write_text(json.dumps(catalog, indent=2))
        (OUT / "games.json").write_text(json.dumps(links, indent=2))
        print(f"Found {len(links)} game links")
        for l in links[:30]:
            print(f"  {l['text'][:30]:30} {l['href']}")

        for i, g in enumerate(links[: args.games]):
            try:
                await page.goto(g["href"], wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(10000)
                await dismiss_cookies(page)
                await dismiss_popups(page)
                await shot(page, f"game_{i}")
                (OUT / f"game_{i}.html").write_text(await page.content())
                d = await dump_all(page)
                (OUT / f"game_{i}.json").write_text(json.dumps(d, indent=2))
                try:
                    bet_seen = await ensure_zero_bet(page)
                except Exception as e:
                    bet_seen = f"PROBLEM: {e}"
                btn = await find_action(page)
                btn_text = (await btn.inner_text())[:30] if btn else None
                demo = any(f.get("demo_text") for f in d)
                login = any(f.get("login_text") for f in d)
                canv = sum(f.get("canvases", 0) for f in d)
                print(f"\n[{i}] {g['href']}\n    ZERO BET: {bet_seen}\n    action button: {btn_text!r} | demo text: {demo} | "
                      f"login text: {login} | canvases: {canv} | frames: {len(d)}")
            except Exception as e:
                print(f"\n[{i}] {g['href']} FAILED: {e!r}")
        await browser.close()
    print(f"\nDone. Everything is in {OUT}")


async def play(args):
    rounds = Log(OUT / "rounds.jsonl")
    events = Log(OUT / "events.jsonl")
    net = Log(OUT / "network.jsonl")
    s = {"rounds": 0, "errors": 0, "reloads": 0, "fresh_browsers": 0, "fail_streak": 0}
    started = time.time()
    deadline = started + args.minutes * 60

    async with async_playwright() as p:
        browser, page = await open_browser(p)
        attach_network(page, net)
        btn = None

        while time.time() < deadline:
            try:
                if btn is None:
                    await page.goto(args.game, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(8000)
                    await dismiss_cookies(page)
                    await dismiss_popups(page)
                    btn = await find_action(page, args.button)
                    if btn is None:
                        raise RuntimeError("no action button found")
                    events.write(kind="ready", button=(await btn.inner_text())[:40])

                bet = await ensure_zero_bet(page)  # raises (no click) unless the bet is 0
                before = await balances(page)
                t0 = time.time()
                await btn.click(timeout=10000)
                ok = await wait_ready(btn, args.timeout)
                if await login_wall_up(page):
                    events.write(kind="login_wall", note="bet asked for login: demo is not active")
                    await shot(page, "login_wall")
                    print("STOPPED: the game asked for a login, so demo mode isn't on. Send me shots/login_wall.png")
                    break
                if time.time() - t0 < args.min_round:
                    await asyncio.sleep(args.min_round - (time.time() - t0))
                after = await balances(page)

                s["rounds"] += 1
                rounds.write(round=s["rounds"], bet=bet, secs=round(time.time() - t0, 2), ready=ok,
                             before=before, after=after)
                if s["rounds"] % 25 == 0:
                    await shot(page, f"round_{s['rounds']:05d}")
                    mins = (time.time() - started) / 60
                    print(f"{now()} rounds={s['rounds']} errors={s['errors']} "
                          f"reloads={s['reloads']} ({s['rounds'] / max(mins, 0.01) * 60:.0f}/hr)")
                if not ok:
                    raise RuntimeError("button never came back after the round")
                s["fail_streak"] = 0

            except Exception as e:
                s["errors"] += 1
                s["fail_streak"] += 1
                events.write(kind="error", err=repr(e)[:300], streak=s["fail_streak"])
                await shot(page, f"error_{s['errors']:04d}")
                btn = None  # forces a reload on the next pass
                s["reloads"] += 1
                if s["fail_streak"] >= 5:
                    # Something is badly stuck: wait, then start a brand-new browser.
                    events.write(kind="fresh_browser", wait=60)
                    await asyncio.sleep(60)
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    browser, page = await open_browser(p)
                    attach_network(page, net)
                    s["fresh_browsers"] += 1
                    s["fail_streak"] = 0

        await browser.close()

    hrs = (time.time() - started) / 3600
    s.update(hours=round(hrs, 2), rounds_per_hour=round(s["rounds"] / max(hrs, 0.001)), game=args.game)
    (OUT / "summary.json").write_text(json.dumps(s, indent=2))
    print("\nSUMMARY\n" + json.dumps(s, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Hubie game-driver spike")
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("login", help="log in by hand once; the session is saved for recon/play")
    r = sub.add_parser("recon")
    r.add_argument("--games", type=int, default=8, help="how many games to open")
    pl = sub.add_parser("play")
    pl.add_argument("--game", required=True, help="game URL from games.json")
    pl.add_argument("--minutes", type=float, default=60)
    pl.add_argument("--button", help="exact action button text if auto-detect picks wrong (e.g. Spin)")
    pl.add_argument("--timeout", type=float, default=30, help="max seconds a round can take")
    pl.add_argument("--min-round", type=float, default=3, help="min seconds between rounds")
    args = ap.parse_args()

    SHOTS.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    asyncio.run({"login": login, "recon": recon, "play": play}[args.mode](args))


if __name__ == "__main__":
    main()
