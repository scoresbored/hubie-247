#!/usr/bin/env python3
"""
Find and download hubie-art-pack.zip from the Housebets contest page.

Uses the same logged-in browser profile as spike.py (hubie_profile/), opens the
"Hubie, live 24/7" contest, clicks through every tab, and collects any link or
API data that mentions a zip / art pack. Anything downloadable is saved to
./hubie_pack/.

Run from ~/hubie with the venv active:
  python3 find_pack.py
"""
import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

BASE = "https://www.housebets.com"
PROFILE = Path(os.environ.get("HUBIE_PROFILE", "./hubie_profile"))
OUT = Path("./hubie_pack")
SHOTS = OUT / "shots"
HIT_RX = re.compile(r"(\.zip\b|art[-_ ]?pack|hubie[-_ ]?art|voice[-_ ]?notes|press[-_ ]?kit|assets?\.zip)", re.I)
TAB_RX = re.compile(r"^\s*(task|rules|brief|details|overview|resources|assets|files|downloads?|faq|about)\s*$", re.I)

hits = {}       # url -> where we saw it
api_mentions = []


def note(url, where):
    if url and url not in hits:
        hits[url] = where
        print(f"  FOUND: {url}   ({where})")


async def collect_links(page, where):
    links = await page.evaluate("""() => [...document.querySelectorAll('a[href]')]
        .map(a => ({href: a.href, text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 80),
                    dl: a.hasAttribute('download')}))""")
    for l in links:
        if HIT_RX.search(l["href"]) or HIT_RX.search(l["text"]) or l["dl"]:
            note(l["href"], f"{where} link: {l['text']!r}")
    text = await page.evaluate("() => document.body ? document.body.innerText : ''")
    for m in re.finditer(r".{0,80}(art[- ]?pack|\.zip|voice notes).{0,80}", text, re.I):
        print(f"  TEXT on {where}: …{m.group(0).strip()}…")


async def main():
    OUT.mkdir(exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE), headless=False, viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_response(resp):
            try:
                if resp.request.resource_type not in ("xhr", "fetch"):
                    return
                body = await resp.text()
                if HIT_RX.search(body):
                    for m in re.finditer(r'https?://[^"\'\s]+?\.zip[^"\'\s]*', body):
                        note(m.group(0), f"API {resp.url[:80]}")
                    for m in re.finditer(r'"[^"]*(art[-_ ]?pack|hubie[-_ ]?art)[^"]*"', body, re.I):
                        api_mentions.append({"api": resp.url, "snippet": m.group(0)[:200]})
            except Exception:
                pass
        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

        print("Opening contests page...")
        await page.goto(f"{BASE}/contests", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)
        try:
            rej = page.get_by_role("button", name=re.compile(r"^\s*reject\s*$", re.I))
            if await rej.count():
                await rej.first.click(timeout=3000)
        except Exception:
            pass
        await page.screenshot(path=str(SHOTS / "contests.png"), full_page=True)
        await collect_links(page, "contests list")

        print("Opening the Hubie contest...")
        opened = False
        for rx in (r"hubie,?\s*live\s*24/7", r"hubie"):
            try:
                el = page.get_by_text(re.compile(rx, re.I)).first
                if await el.count():
                    await el.click(timeout=5000)
                    await page.wait_for_timeout(6000)
                    opened = True
                    break
            except Exception:
                continue
        print(f"  contest page: {page.url}" if opened else "  !! couldn't click into the Hubie contest")
        await page.screenshot(path=str(SHOTS / "contest_main.png"), full_page=True)
        (OUT / "contest_main.html").write_text(await page.content())
        await collect_links(page, "contest main")

        # click every tab-like control (Task, Rules, Resources...) and look again
        tabs = page.get_by_role("tab")
        names = []
        for i in range(await tabs.count()):
            names.append((await tabs.nth(i).inner_text()).strip())
        for b in await page.get_by_role("button").all() + await page.get_by_role("link").all():
            try:
                t = (await b.inner_text()).strip()
                if TAB_RX.match(t) and t not in names:
                    names.append(t)
            except Exception:
                pass
        print(f"  tabs to check: {names}")
        for n in names:
            try:
                el = page.get_by_role("tab", name=n)
                if not await el.count():
                    el = page.get_by_text(n, exact=True)
                await el.first.click(timeout=4000)
                await page.wait_for_timeout(2500)
                safe = re.sub(r"\W+", "_", n.lower())
                await page.screenshot(path=str(SHOTS / f"tab_{safe}.png"), full_page=True)
                (OUT / f"tab_{safe}.html").write_text(await page.content())
                await collect_links(page, f"tab {n}")
            except Exception as e:
                print(f"  couldn't open tab {n!r}: {e.__class__.__name__}")

        # download anything that looks like the pack, using the logged-in session
        saved = []
        for url in hits:
            if not url.startswith("http"):
                continue
            try:
                r = await ctx.request.get(url, timeout=120000)
                ct = r.headers.get("content-type", "")
                if r.ok and ("zip" in ct or "octet-stream" in ct or url.lower().split("?")[0].endswith(".zip")):
                    name = url.split("?")[0].rstrip("/").split("/")[-1] or "download.zip"
                    (OUT / name).write_bytes(await r.body())
                    saved.append(name)
                    print(f"  DOWNLOADED: {name} ({len(await r.body()) // 1024} KB)")
            except Exception as e:
                print(f"  download failed for {url}: {e.__class__.__name__}")

        (OUT / "found.json").write_text(json.dumps({"hits": hits, "api_mentions": api_mentions,
                                                    "downloaded": saved}, indent=2))
        await ctx.close()

    print("\nSUMMARY")
    print(f"  links found: {len(hits)}   downloaded: {saved or 'nothing'}")
    print(f"  API mentions: {len(api_mentions)}")
    print(f"  Everything is in {OUT.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
