"""Hubie v1: plays a House original at $0, commentates every round, streams to Kick.

Run with ./run.sh (it sets up the virtual screen and audio first).
  ./run.sh            stream to Kick
  ./run.sh --offline  no stream; saves logs/preview.png every 10s so you can check the picture
"""
import argparse
import asyncio
import random
import re
import time

from . import chatmod, safety
from .brain import Brain
from .config import GAME_URL, LOG_DIR
from .game import GameDriver, NonZeroBet
from .kick import Kick
from .log import log
from .scene import Scene
from .stream import Streamer
from .voice import Voice

BANTER = ["why sloths make great dice players", "the 3am crowd", "what Hubie does between rolls",
          "the perfect nap schedule", "top hats as a lifestyle", "the calm of demo mode",
          "favorite House originals", "chilling with chat"]


SPEECH = asyncio.Lock()


async def say(scene, voice, brain, line):
    async with SPEECH:
        await _say(scene, voice, brain, line)


async def _say(scene, voice, brain, line):
    brain.remember(line)
    scene.set_caption(line)
    log("say", line=line)
    clip = await voice.synth(line)
    if clip:
        wav, env, secs = clip
        await voice.play(wav)
    else:
        await asyncio.sleep(min(8, 1.5 + len(line) * 0.06))


TRIVIAL = re.compile(r"^\s*(lol+|lmao+|gg+|ok+|k|w+|l+|nice|hi+|hey+|yo+|xd+|haha+|\?+|!+|[\W_]+)\s*$", re.I)


async def chat_worker(scene, voice, brain, kick, chat_q):
    """Replies to chat in TEXT quickly. Only some replies are also spoken out loud:
    first-time chatters, real questions to Hubie, and nothing more than about once every 40s."""
    seen_users = set()
    last_spoken = 0.0
    while True:
        user, text, ts = await chat_q.get()
        if chat_q.qsize() > 3 or time.time() - ts > 45:      # flooding or stale: skip
            continue
        first_time = user.lower() not in seen_users
        seen_users.add(user.lower())
        if TRIVIAL.match(text) and not first_time:            # "lol", "gg", emotes: no reply needed
            continue
        if chatmod.says_underage(text):
            line = f"{user}, this stream is eighteen plus only, so I can't chat about this one with you. Take care."
            speak_it = True
        else:
            raw = await brain.reply(user, text, first_time=first_time)
            line = safety.clean(raw)
            if not line:
                log("line_rejected", raw=raw)
                continue
            question = "?" in text and len(text) > 10
            mentions = "hubie" in text.lower()
            gap = time.time() - last_spoken
            speak_it = (first_time and gap > 20) or ((question or mentions) and gap > 40)
        scene.add_chat("Hubie", line)
        if kick:
            await kick.say(line)
        log("chat_reply", to=user, spoken=speak_it, first_time=first_time, secs=round(time.time() - ts, 1))
        if speak_it:
            last_spoken = time.time()
            await say(scene, voice, brain, line)


class Moments:
    """Decides which rounds are worth talking about. Most rounds Hubie stays quiet."""
    WIN_MARKS = {3, 5, 7, 10}
    LOSS_MARKS = {6, 10, 15, 20}

    def __init__(self):
        self.streak = 0          # +n wins in a row, -n losses in a row
        self.rounds = self.hits = 0

    def update(self, ev):
        self.rounds += 1
        self.hits += int(ev["win"])
        prev = self.streak
        self.streak = (self.streak + 1 if self.streak >= 0 else 1) if ev["win"] else (self.streak - 1 if self.streak <= 0 else -1)
        mult = ev.get("multiplier") or 0
        chance = ev.get("win_chance") or 100
        if ev["win"] and (mult >= 10 or chance < 10):
            return f"a big hit: {mult}x"
        if ev["win"] and prev <= -8:
            return f"finally a hit after {-prev} misses in a row"
        if self.streak in self.WIN_MARKS:
            return f"{self.streak} hits in a row"
        if -self.streak in self.LOSS_MARKS:
            return f"{-self.streak} misses in a row"
        if self.rounds % 100 == 0:
            return f"round {self.rounds} of this game"
        return None

    def summary(self):
        pct = f", {100 * self.hits // self.rounds}% hit rate" if self.rounds else ""
        return f"{self.rounds} rounds, {self.hits} hits{pct}"


def pick_segment(benched, recent):
    from .games import GAMES
    now = time.time()
    keys = [k for k in GAMES if benched.get(k, 0) < now] or list(GAMES)
    fresh = [k for k in keys if k not in recent[-2:]] or keys
    key = random.choice(fresh)
    return key, random.choice(GAMES[key]["strategies"])


async def director(scene, game, voice, brain, kick=None, chat_q=None):
    benched, recent = {}, []
    last_spoke = time.time()
    last_rg = time.time()

    async def speak(line):
        nonlocal last_spoke
        await say(scene, voice, brain, line)
        last_spoke = time.time()

    await speak("Hubie here. Grab a branch, we're playing some House originals in demo mode. Eighteen plus, zero real money.")
    while True:
        key, strat = pick_segment(benched, recent)
        recent.append(key)
        try:
            await game.switch(key, strat)
        except Exception as e:
            log("switch_error", game=key, err=repr(e)[:200])
            benched[key] = time.time() + 3600
            await game.recover(hard=True)
            continue
        scene.set_status(f"Now playing: {game.name} - {strat['name']}")
        m = Moments()
        scene.reset_session()
        line = safety.clean(await brain.segment(game.name, strat["name"], "start"))
        await speak(line or f"Switching it up. {game.name}, {strat['name']}. Let's see what happens.")
        seg_end = time.time() + random.uniform(8, 15) * 60      # 8-15 minutes per segment
        fails = 0
        while time.time() < seg_end:
            try:
                ev = await game.play_round()
                fails = 0
            except NonZeroBet as e:
                log("STOP_NONZERO_BET", err=str(e))
                scene.set_status("Taking a short break. Back soon.")
                await asyncio.sleep(3600)                    # never bet again this hour
                continue
            except Exception as e:
                fails += 1
                log("round_error", game=key, err=repr(e)[:200], fails=fails)
                if fails >= 3:
                    benched[key] = time.time() + 3600        # this game is misbehaving: bench it
                    log("benched", game=key)
                    break
                await game.recover(hard=fails >= 2)
                continue
            scene.on_event(ev)
            reason = m.update(ev)
            log("round", **{k: v for k, v in ev.items() if k != "details"})
            quiet = time.time() - last_spoke < 20 and (ev.get("multiplier") or 0) < 50
            if reason and not quiet:                         # max one comment per ~20s unless it's huge
                raw = await brain.moment(ev, reason, m.summary())
                line = safety.clean(raw)
                if not line:
                    log("line_rejected", raw=raw)
                else:
                    await speak(line)
            if time.time() - last_rg > 600:                  # responsible-gambling reminder every ~10 min
                await speak(safety.rg_line())
                last_rg = time.time()
            elif time.time() - last_spoke > 75:              # never dead air for long
                extra = safety.clean(await brain.banter(random.choice(BANTER)))
                if extra:
                    await speak(extra)
            await asyncio.sleep(random.uniform(1.5, 3.0))    # watchable pace
        if m.rounds:
            line = safety.clean(await brain.segment(game.name, strat["name"], "end", m.summary()))
            if line:
                await speak(line)


async def previewer(scene, every=30):
    """Saves the current frame to logs/preview.png (used by --offline and the Telegram /preview)."""
    from PIL import Image
    from .config import H, W
    while True:
        await asyncio.sleep(every)
        try:
            tmp = LOG_DIR / "preview.tmp.png"
            Image.frombytes("RGB", (W, H), scene.render()).save(tmp)
            tmp.replace(LOG_DIR / "preview.png")
        except Exception as e:
            log("preview_error", err=repr(e)[:150])


async def main(offline):
    scene = Scene()
    game = GameDriver(GAME_URL, scene.set_game_jpeg)
    voice, brain = Voice(), Brain()
    streamer = Streamer(scene, offline=offline)
    tasks = [asyncio.create_task(streamer.run())]
    tasks.append(asyncio.create_task(previewer(scene, every=10 if offline else 30)))
    while True:
        try:
            await game.start()
            break
        except Exception as e:
            log("game_start_error", err=repr(e)[:200])
            scene.set_caption("Hubie is stretching. One sec...")
            await asyncio.sleep(15)
    kick, chat_q = None, asyncio.Queue()
    try:
        k = Kick()
        if await k.start():
            kick = k

            async def on_message(user, user_id, text, msg_id):
                verdict = chatmod.check(user, text)
                if verdict:
                    minutes, reason = verdict
                    await kick.timeout(user_id, minutes, reason)
                    return
                scene.add_chat(user, text)
                await chat_q.put((user, text, time.time()))

            tasks.append(asyncio.create_task(kick.read_chat(on_message)))
    except Exception as e:
        log("kick_start_error", err=repr(e)[:200])
    tasks.append(asyncio.create_task(chat_worker(scene, voice, brain, kick, chat_q)))
    tasks.append(asyncio.create_task(director(scene, game, voice, brain, kick, chat_q)))
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in done:
        t.result()   # surfaces the crash; run.sh restarts us


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="don't stream; save logs/preview.png")
    asyncio.run(main(ap.parse_args().offline))
