"""Plays House originals in demo mode ($0 bets) and turns every round into a clean event.

Results come from the site's own bet API reply (games.housebets.com/<game>/bet), not the screen.
Frames of the game page are streamed out with Chrome's screencast so the stream shows the game
without any browser bars.
"""
import asyncio
import base64
import re
import time

from playwright.async_api import async_playwright

from .config import PROFILE
from .log import log

GAME_HOST = '[data-housegames-direct="true"]'
BET_INPUT = 'input[aria-label="Bet Amount"]'


class NonZeroBet(Exception):
    """Raised if a bet ever comes back with a non-zero amount. Hubie stops betting."""


def _num(v):
    try:
        return float(re.sub(r"[^0-9.]", "", v or "") or "nan")
    except ValueError:
        return float("nan")


class GameDriver:
    def __init__(self, url, on_frame):
        self.url = url
        self.key = None
        self.name = url.rstrip("/").split("/")[-1].replace("housegames-", "").replace("-", " ").title()
        self.strategy = None
        self.on_frame = on_frame
        self.results = asyncio.Queue()
        self.pw = self.ctx = self.page = self.cdp = None

    # ---------- browser lifecycle ----------
    async def start(self):
        self.pw = await async_playwright().start()
        await self._open_browser()

    async def _open_browser(self):
        self.ctx = await self.pw.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport={"width": 1280, "height": 720},
            args=["--window-size=1280,800", "--mute-audio",
                  "--disable-blink-features=AutomationControlled"])
        self.page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        self.page.on("response", lambda r: asyncio.ensure_future(self._on_response(r)))
        await self._load()

    async def _load(self):
        await self.page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
        await self.page.wait_for_timeout(6000)
        for rx in (r"^\s*reject\s*$", r"^\s*got it\s*$"):
            try:
                b = self.page.get_by_role("button", name=re.compile(rx, re.I))
                if await b.count() and await b.first.is_visible():
                    await b.first.click(timeout=2000)
            except Exception:
                pass
        await self._start_screencast()

    async def _start_screencast(self):
        try:
            self.cdp = await self.ctx.new_cdp_session(self.page)
            self.cdp.on("Page.screencastFrame", lambda p: asyncio.ensure_future(self._frame(p)))
            await self.cdp.send("Page.startScreencast", {
                "format": "jpeg", "quality": 75, "maxWidth": 1280, "maxHeight": 720, "everyNthFrame": 1})
        except Exception as e:
            log("screencast_error", err=repr(e)[:200])

    async def _frame(self, params):
        try:
            self.on_frame(base64.b64decode(params["data"]))
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:
            pass

    async def switch(self, key, strategy):
        """Load another game and apply a strategy (target/risk only, bet stays $0)."""
        from .games import GAMES, apply_strategy
        g = GAMES[key]
        self.key, self.url, self.name, self.strategy = key, g["url"], g["name"], strategy
        await self._load()
        await self._bet_box()                     # wait until the panel is ready
        try:
            await apply_strategy(self.page, key, strategy)
        except Exception as e:
            log("strategy_error", game=key, strategy=strategy.get("name"), err=repr(e)[:150])
        log("switched", game=key, strategy=strategy.get("name"))

    async def recover(self, hard=False):
        log("game_recover", hard=hard)
        try:
            if hard:
                try:
                    await self.ctx.close()
                except Exception:
                    pass
                await self._open_browser()
            else:
                await self._load()
            if self.key and self.strategy:
                from .games import apply_strategy
                await self._bet_box()
                await apply_strategy(self.page, self.key, self.strategy)
        except Exception as e:
            log("recover_error", err=repr(e)[:200])

    # ---------- results from the site's own API ----------
    async def _on_response(self, resp):
        try:
            if "games.housebets.com" in resp.url and resp.url.split("?")[0].rstrip("/").endswith("/bet") \
                    and resp.request.method == "POST":
                await self.results.put(await resp.json())
        except Exception:
            pass

    # ---------- one round ----------
    async def _bet_box(self, timeout=30):
        ready = self.page.locator(f"{GAME_HOST} {BET_INPUT}:not([data-hg-connecting])")
        end = time.time() + timeout
        while time.time() < end:
            for i in range(await ready.count()):
                if await ready.nth(i).is_visible():
                    return ready.nth(i)
            await asyncio.sleep(0.5)
        raise RuntimeError("bet panel never finished connecting")

    async def _ensure_zero(self):
        box = await self._bet_box()
        val = await box.input_value()
        if val.strip() == "" or _num(val) == 0:
            return
        await box.fill("0")
        await box.press("Tab")
        await asyncio.sleep(0.3)
        val = await box.input_value()
        if not (val.strip() == "" or _num(val) == 0):
            raise RuntimeError(f"bet amount won't go to 0 ({val!r})")

    async def _bet_button(self):
        loc = self.page.locator(GAME_HOST).get_by_role("button", name=re.compile(r"^\s*bet\s*$", re.I))
        for i in range(await loc.count()):
            if await loc.nth(i).is_visible():
                return loc.nth(i)
        raise RuntimeError("no Bet button (logged out?)")

    async def play_round(self):
        await self._ensure_zero()           # never click unless the bet is provably 0
        btn = await self._bet_button()
        while not self.results.empty():
            self.results.get_nowait()
        await btn.click(timeout=10000)
        data = await asyncio.wait_for(self.results.get(), 30)
        ev = self._parse(data)
        end = time.time() + 20               # wait until the button is ready again
        while time.time() < end:
            if await btn.is_enabled() and await btn.get_attribute("aria-busy") != "true":
                break
            await asyncio.sleep(0.2)
        return ev

    def _parse(self, data):
        """Works for any House original: the inner game record has amount + payoutMultiplier
        (multiplier on a win, 0 on a loss). Game-specific fields are kept as 'details'."""
        d = data.get("game") if isinstance(data.get("game"), dict) else data
        inner = d.get("game") if isinstance(d.get("game"), dict) else d
        if "amount" not in inner:
            raise RuntimeError("bet reply had no amount field; not trusting it")
        amount = inner.get("amount") or 0
        if amount != 0:
            raise NonZeroBet(f"bet amount came back as {amount}")
        pm = inner.get("payoutMultiplier") or 0
        skip = {"id", "seedId", "userId", "gameId", "createdAt", "updatedAt", "deletedAt", "finishedAt",
                "fairnessVersion", "seed", "game", "clientSeed", "hashedSeed", "serverSeed", "nonce"}
        details = {k: v for k, v in d.items()
                   if k not in skip and isinstance(v, (int, float, str, bool)) and len(str(v)) < 40}
        outcome = d.get("outcome", d.get("result"))
        win = pm >= 1          # Plinko/Wheel pay e.g. 0.2x on every drop: only 1x+ counts as a hit
        if self.key in (None, "dice") and isinstance(outcome, (int, float)) and isinstance(d.get("rollOver"), (int, float)):
            direction = d.get("betDirection") or "over"
            win = outcome > d["rollOver"] if direction == "over" else outcome < d["rollOver"]
        return {"game": self.name, "strategy": (self.strategy or {}).get("name", ""), "outcome": outcome,
                "multiplier": pm, "win": bool(win), "amount": amount, "details": details,
                "target": d.get("rollOver"), "direction": d.get("betDirection"), "win_chance": d.get("winChance")}
