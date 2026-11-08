"""House originals Hubie can play, and the strategies (target / risk settings) he rotates through.

Every strategy keeps the bet at $0. A "strategy" only changes the target or risk, never the bet size.
If a game misbehaves several times in a row it gets benched for an hour automatically.
"""
import re

BASE = "https://www.housebets.com/games/housegames-"
HOST = '[data-housegames-direct="true"]'

GAMES = {
    "dice": {"name": "Dice", "url": BASE + "dice", "strategies": [
        {"name": "coin-flip odds, 2x", "mult": "2"},
        {"name": "safe and steady, 1.5x", "mult": "1.5"},
        {"name": "long shots, 10x", "mult": "10"},
        {"name": "moonshots, 50x", "mult": "50"},
    ]},
    "limbo": {"name": "Limbo", "url": BASE + "limbo", "strategies": [
        {"name": "2x targets", "target": "2"},
        {"name": "5x targets", "target": "5"},
        {"name": "10x targets", "target": "10"},
        {"name": "100x dreams", "target": "100"},
    ]},
    "wheel": {"name": "Wheel", "url": BASE + "wheel", "strategies": [
        {"name": "low risk", "risk": "Low"},
        {"name": "medium risk", "risk": "Medium"},
        {"name": "high risk", "risk": "High"},
    ]},
    "maxwin": {"name": "Max Win", "url": BASE + "max-win", "strategies": [{"name": "classic"}]},
    "plinko": {"name": "Plinko", "url": BASE + "plinko", "strategies": [{"name": "classic"}]},
    "coinflip": {"name": "Coinflip", "url": BASE + "coin-flip", "strategies": [{"name": "classic"}]},
}

# Games taken out of the rotation (comma-separated keys). Max Win is off by default: its animations are
# heavy enough to eat most of the server's CPU in the headless browser.
import os as _os
DISABLED = [k.strip() for k in _os.environ.get("HUBIE_DISABLED_GAMES", "maxwin").split(",") if k.strip()]
for _k in DISABLED:
    GAMES.pop(_k, None)


async def _fill(page, selector, value):
    box = page.locator(f"{HOST} {selector}").first
    await box.click(timeout=5000)
    await box.fill(value)
    await box.press("Tab")


async def apply_strategy(page, game_key, strat):
    """Set the game's target/risk controls. Never touches the bet amount."""
    if game_key == "dice" and "mult" in strat:
        await _fill(page, "#hd-g-in-mult", strat["mult"])
    elif game_key == "limbo" and "target" in strat:
        await _fill(page, 'input[aria-label="Target Multiplier"]', strat["target"])
    elif game_key == "wheel" and "risk" in strat:
        btn = page.locator(HOST).get_by_role("button", name=re.compile(rf"^\s*{strat['risk']}\s*$", re.I))
        await btn.first.click(timeout=5000)
    await page.wait_for_timeout(500)
