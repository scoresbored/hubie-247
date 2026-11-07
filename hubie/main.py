"""Hubie v1: plays a House original at $0, commentates every round, streams to Kick.

Run with ./run.sh (it sets up the virtual screen and audio first).
  ./run.sh            stream to Kick
  ./run.sh --offline  no stream; saves logs/preview.png every 10s so you can check the picture
"""
import argparse
import asyncio
import json
import os
import random
import re
import time

from . import chatfun, chatmod, safety
from .brain import Brain
from .config import GAME_URL, LOG_DIR, ROOT
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


async def say(scene, voice, brain, line, max_age=20):
    """Speak a line. If Hubie is busy and the line waits longer than max_age seconds,
    it's dropped instead of being said late (so he never falls behind what's happening)."""
    asked = time.time()
    async with SPEECH:
        waited = time.time() - asked
        if waited > max_age:
            log("say_dropped", line=line[:80], waited=round(waited, 1))
            return
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


SPEAK_GAP = {"quiet": 40, "normal": 60, "busy": 90}     # min seconds between spoken chat answers


async def chat_worker(scene, voice, brain, kick, chat_q, state=None):
    """Replies to chat, scaled to how busy chat is (ChatRate):
    quiet  -> answers most messages (but not the same person every line)
    normal -> first-timers, questions, people who say "Hubie", plus a few others
    busy   -> first-timers (text only), questions and mentions, with a cap per minute
    Only some replies are spoken out loud: newcomers (unless chat is busy) and real questions to Hubie."""
    rate = state.rate if state else chatfun.ChatRate()
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
        question = "?" in text and len(text) > 10
        mentions = "hubie" in text.lower()
        if chatmod.says_underage(text):
            line = f"{user}, this stream is eighteen plus only, so I can't chat about this one with you. Take care."
            speak_it, mode = True, rate.mode()
        else:
            ok, mode = rate.should_reply(user, first_time, question, mentions)
            if not ok:
                log("chat_skip", user=user, mode=mode)
                continue
            if first_time:
                scene.react("happy", 2.5, soft=True)
            elif question or mentions:
                scene.react("thinking", 2.5, soft=True)
            raw = await brain.reply(user, text, first_time=first_time)
            line = safety.clean(raw)
            if not line:
                log("line_rejected", raw=raw)
                continue
            gap = time.time() - last_spoken
            speak_it = (first_time and mode != "busy" and gap > 15) or ((question or mentions) and gap > SPEAK_GAP[mode])
        rate.replied(user)
        log("chat_reply", to=user, spoken=speak_it, first_time=first_time, mode=mode,
            per_min=rate.per_min(), secs=round(time.time() - ts, 1))
        if speak_it:                                   # Hubie answers out loud (no chat-bot text)
            last_spoken = time.time()
            scene.add_chat("Hubie", line)
            await say(scene, voice, brain, line, max_age=25)
        else:                                          # quick text answer from the chat bot
            scene.add_chat("HubieBot", line)
            if kick:
                await kick.bot(line)


ALERT_TEMPLATES = {
    "follow": ("Thank you for the follow, {u}! Welcome to the branch.", "Thanks for the follow, whoever that was! Welcome in."),
    "sub": ("{u}, thank you for subscribing! Top hat tip to you.", "Thanks for the sub!"),
    "gift": ("{u}, thank you for gifting {n} subs! Absolute legend.", "Thanks for the gifted subs!"),
}
BOT_ALERTS = {
    "follow": ("Thanks for the follow, @{u}! 🦥", "Thanks for the follow! 🦥"),
    "sub": ("@{u} just subscribed! Thank you! 🎩", "New subscriber! Thank you! 🎩"),
    "gift": ("@{u} gifted {n} subs! Thank you! 🎁", "Gifted subs! Thank you! 🎁"),
}


async def alert(scene, voice, brain, kick, kind, info):
    """Follows, subs and gifts: the chat bot posts it, and Hubie always thanks them out loud."""
    user, n = info.get("user"), info.get("count") if kind == "gift" else info.get("new", 1)
    named, anon = BOT_ALERTS[kind]
    msg = (named if user else anon).format(u=user, n=n)
    scene.add_chat("HubieBot", msg)
    scene.react("happy" if kind == "follow" else "cheering", 4.0)
    if kick:
        await kick.bot(msg)
    extra = f"{info.get('months')} months" if kind == "sub" and info.get("months") else (f"{n} subs" if kind == "gift" else "")
    line = safety.clean(await brain.alert(kind, user, extra))
    if not line:
        t_named, t_anon = ALERT_TEMPLATES[kind]
        line = (t_named if user else t_anon).format(u=user, n=n)
    await say(scene, voice, brain, line, max_age=45)


class ChatState:
    def __init__(self):
        self.poll = None
        self.pending = None                 # (game_key, strategy, note) chosen by chat
        self.suggestions = chatfun.Suggestions()
        self.rate = chatfun.ChatRate()
        self.giveaway = None
        self.benched, self.current = {}, None


async def run_poll(scene, voice, brain, kick, state, benched, current):
    from .games import GAMES
    if state.poll:                                     # one poll at a time
        return
    poll = chatfun.make_poll(benched, current)
    if len(poll.options) < 2:
        return
    state.poll = poll
    n = len(poll.options)
    listing = "   ".join(f"{i + 1}) {poll.label(i)}" for i in range(n))
    log("poll_start", options=[poll.label(i) for i in range(n)])
    scene.react("thinking", 3.0)
    if kick:
        await kick.bot(f"POLL: what should Hubie play next? Type 1-{n} in chat.   {listing}")
    await say(scene, voice, brain, max_age=40, line=f"Chat, you're picking my next game. Type a number from one to {['', 'one', 'two', 'three', 'four'][n]}, the options are on screen.")
    while poll.active:
        scene.set_poll((f"Next game? Type 1-{n}", [(poll.label(i), v) for i, v in enumerate(poll.tally())],
                        max(0, int(poll.ends - time.time()))))
        await asyncio.sleep(1)
    poll.done = True
    (key, strat), tally = poll.winner()
    scene.set_poll(None)
    state.poll = None
    log("poll_end", tally=tally, winner=key)
    if sum(tally) == 0:
        if kick:
            await kick.bot("Poll closed with no votes, so Hubie picks. Next poll soon!")
        return
    state.pending = (key, strat, "picked by chat")
    scene.react("happy", 3.0)
    label = f"{GAMES[key]['name']} ({strat['name']})"
    if kick:
        await kick.bot(f"Poll closed: {label} wins with {max(tally)} vote{'s' if max(tally) != 1 else ''}. Up next!")
    await say(scene, voice, brain, max_age=40, line=f"Chat has spoken. {GAMES[key]['name']}, {strat['name']}, is up next.")


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


def react_to(scene, reason):
    """Pick Hubie's face for a notable round (the Moments reason text)."""
    if reason.startswith("a big hit"):
        scene.react("shocked", 1.3, then=[("cheering", 3.5)])
    elif reason.startswith("finally a hit"):
        scene.react("cheering", 3.0)
    elif "hits in a row" in reason:
        scene.react("cheering" if int(reason.split()[0]) >= 7 else "laughing", 3.0)
    elif "misses in a row" in reason:
        scene.react("facepalm", 3.5)
    elif reason.startswith("round "):
        scene.react("happy", 2.5)


def pick_segment(benched, recent):
    from .games import GAMES
    now = time.time()
    keys = [k for k in GAMES if benched.get(k, 0) < now] or list(GAMES)
    fresh = [k for k in keys if k not in recent[-2:]] or keys
    key = random.choice(fresh)
    return key, random.choice(GAMES[key]["strategies"])


async def director(scene, game, voice, brain, kick=None, chat_q=None, state=None):
    from .games import GAMES
    state = state or ChatState()
    benched, recent = state.benched, []
    seg_no = 0
    last_spoke = time.time()
    last_rg = time.time()

    async def speak(line, max_age=12):
        nonlocal last_spoke
        await say(scene, voice, brain, line, max_age)
        last_spoke = time.time()

    await speak("Hubie here. Grab a branch, we're playing some House originals in demo mode. Eighteen plus, zero real money.")
    while True:
        note = ""
        req = None if state.pending else state.suggestions.take(benched)
        if state.pending and benched.get(state.pending[0], 0) < time.time():
            key, strat, note = state.pending
        elif req:
            key, who = req
            strat, note = random.choice(GAMES[key]["strategies"]), f"requested by {who} in chat"
        else:
            key, strat = pick_segment(benched, recent)
        state.pending = None
        state.current = key
        seg_no += 1
        recent.append(key)
        try:
            await game.switch(key, strat)
        except Exception as e:
            log("switch_error", game=key, err=repr(e)[:200])
            benched[key] = time.time() + 3600
            await game.recover(hard=True)
            continue
        scene.set_status(f"Now playing: {game.name} - {strat['name']}")
        scene.react("happy", 2.5)
        m = Moments()
        scene.reset_session()
        label = strat["name"] + (f" ({note})" if note else "")
        line = safety.clean(await brain.segment(game.name, label, "start"))
        await speak(line or f"Switching it up. {game.name}, {strat['name']}. Let's see what happens.")
        if seg_no % 2 == 0 and kick:                     # every other segment, let chat pick what's next

            async def later_poll(k=key):
                await asyncio.sleep(180)
                await run_poll(scene, voice, brain, kick, state, benched, k)
            asyncio.create_task(later_poll())
        seg_end = time.time() + random.uniform(8, 15) * 60      # 8-15 minutes per segment
        fails = 0
        while time.time() < seg_end:
            try:
                ev = await game.play_round()
                fails = 0
            except NonZeroBet as e:
                log("STOP_NONZERO_BET", err=str(e))
                scene.set_status("Taking a short break. Back soon.")
                scene.react("sleepy", 3600)
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
            if reason:
                react_to(scene, reason)
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
                await speak(safety.rg_line(), max_age=120)
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


async def run_giveaway(scene, voice, brain, kick, state, prize, minutes):
    """Optional, human-started (off by default). Viewers type !enter; Hubie draws a random winner."""
    prize = safety.safe_prize(prize)
    if not prize:
        log("giveaway_rejected", reason="prize must be non-monetary")
        return
    if state.giveaway and state.giveaway.active:
        return
    seconds = int(max(1, min(15, minutes)) * 60)
    g = state.giveaway = chatfun.Giveaway(prize, seconds)
    log("giveaway_start", prize=prize, seconds=seconds)
    scene.react("cheering", 3.0)
    if kick:
        await kick.bot(f"GIVEAWAY: {prize}! Type !enter to join. Drawing in {seconds // 60} min. "
                       "Free to enter, nothing to buy, 18+.")
    await say(scene, voice, brain, max_age=40,
              line=f"Giveaway time! Type exclamation enter in chat for a chance at {prize}. Free to enter, eighteen plus.")
    while g.active:
        if not state.poll:                                   # share the on-screen box with polls
            scene.set_poll((f"GIVEAWAY: {prize}", [("type !enter", len(g.entrants))], max(0, int(g.ends - time.time()))))
        await asyncio.sleep(1)
    winner = g.draw()
    if not state.poll:
        scene.set_poll(None)
    log("giveaway_end", prize=prize, entrants=len(g.entrants), winner=winner)
    if not winner:
        if kick:
            await kick.bot("Giveaway closed with no entries. Next time!")
        return
    scene.react("shocked", 1.2, then=[("cheering", 3.5)])
    if kick:
        await kick.bot(f"🎉 @{winner} wins the giveaway: {prize}! ({len(g.entrants)} entered)")
    await say(scene, voice, brain, max_age=40, line=f"And the winner is {winner}! Congrats, enjoy your {prize}.")


HUMAN_CONTROLS = os.environ.get("HUBIE_HUMAN_CONTROLS", "0") == "1"
INBOX = ROOT / "inbox"


async def human_inbox(scene, voice, brain, kick, state):
    """OPTIONAL human controls from the Telegram bot (/poll, /giveaway). Off unless HUBIE_HUMAN_CONTROLS=1.
    Every command is logged as a human_touch, because the contest counts any human action against the run."""
    INBOX.mkdir(parents=True, exist_ok=True)
    while True:
        await asyncio.sleep(2)
        for f in sorted(INBOX.glob("*.json")):
            try:
                cmd = json.loads(f.read_text())
            except Exception:
                cmd = {}
            f.unlink(missing_ok=True)
            if not HUMAN_CONTROLS:
                log("human_cmd_ignored", cmd=cmd.get("cmd"))
                continue
            log("human_touch", cmd=cmd.get("cmd"), detail=cmd)
            if cmd.get("cmd") == "poll" and kick and not state.poll:
                asyncio.create_task(run_poll(scene, voice, brain, kick, state, state.benched, state.current))
            elif cmd.get("cmd") == "giveaway":
                asyncio.create_task(run_giveaway(scene, voice, brain, kick, state,
                                                 cmd.get("prize", ""), float(cmd.get("minutes", 3))))


async def sync_check(streamer, every=300):
    """Every 5 minutes: make sure voice and picture are still together, and fix it if not."""
    from .audio import MIXER
    while True:
        await asyncio.sleep(every)
        backlog, lag = MIXER.backlog(), getattr(streamer, "lag", 0.0)
        log("sync_check", audio_backlog_s=round(backlog, 2), video_lag_s=round(lag, 2),
            waiting_lines=len(SPEECH._waiters or []))
        if backlog > 15:                          # nothing Hubie says is that long: something piled up
            MIXER.skip(int((backlog - 1) * 30))
            log("resync", dropped_audio_s=round(backlog - 1, 1))


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
    tasks = [asyncio.create_task(streamer.run()), asyncio.create_task(sync_check(streamer))]
    tasks.append(asyncio.create_task(previewer(scene, every=10 if offline else 30)))
    while True:
        try:
            await game.start()
            break
        except Exception as e:
            log("game_start_error", err=repr(e)[:200])
            scene.set_caption("Hubie is stretching. One sec...")
            await asyncio.sleep(15)
    try:
        from .audio import MIXER
        from .music import ensure_tracks
        MIXER.set_music(await asyncio.get_running_loop().run_in_executor(None, ensure_tracks))
        log("music_ready", tracks=len(MIXER.tracks), volume=MIXER.music_vol)
    except Exception as e:
        log("music_error", err=repr(e)[:200])
    state = ChatState()
    kick, chat_q = None, asyncio.Queue()
    try:
        k = Kick()
        if await k.start():
            kick = k

            async def on_message(user, user_id, text, msg_id):
                state.rate.seen(time.time())
                if state.poll and state.poll.vote(user, text):        # votes: counted, not answered
                    return
                if state.giveaway and state.giveaway.enter(user, text):   # !enter: counted, not answered
                    return
                verdict = chatmod.check(user, text)
                if verdict:
                    minutes, reason = verdict
                    await kick.timeout(user_id, minutes, reason)
                    await kick.bot(f"@{user} timed out for {minutes} min: {reason}.")
                    return
                scene.add_chat(user, text)
                got = state.suggestions.add(user, text)
                if got is not None:                                   # !play <game>
                    from .games import GAMES
                    names = ", ".join(g["name"] for g in GAMES.values())
                    await kick.bot(f"@{user} got it, {GAMES[got]['name']} is on Hubie's request list." if got
                                   else f"@{user} try !play followed by one of: {names}")
                    return
                await chat_q.put((user, text, time.time()))

            async def on_event(kind, info):
                asyncio.create_task(alert(scene, voice, brain, kick, kind, info))

            tasks.append(asyncio.create_task(kick.read_chat(on_message, on_event)))
    except Exception as e:
        log("kick_start_error", err=repr(e)[:200])
    tasks.append(asyncio.create_task(chat_worker(scene, voice, brain, kick, chat_q, state)))
    tasks.append(asyncio.create_task(human_inbox(scene, voice, brain, kick, state)))
    tasks.append(asyncio.create_task(director(scene, game, voice, brain, kick, chat_q, state)))
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for t in done:
        t.result()   # surfaces the crash; run.sh restarts us


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="don't stream; save logs/preview.png")
    asyncio.run(main(ap.parse_args().offline))
