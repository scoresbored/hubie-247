"""Chat games: polls for the next game, and !play suggestions."""
import random
import re
import time
from collections import Counter

from .games import GAMES

ALIASES = {
    "dice": "dice", "limbo": "limbo", "wheel": "wheel", "max win": "maxwin", "maxwin": "maxwin",
    "plinko": "plinko", "coinflip": "coinflip", "coin flip": "coinflip", "flip": "coinflip",
}
VOTE = re.compile(r"^\s*!?(?:vote\s*)?([1-4])\s*!*\s*$", re.I)
SUGGEST = re.compile(r"^\s*!(?:play|suggest|next)\s+(.+)$", re.I)


def match_game(text):
    t = text.lower()
    for name, key in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if name in t:
            return key if key in GAMES else None
    return None


class Poll:
    def __init__(self, options, seconds=75):
        self.options = options              # list of (game_key, strategy dict)
        self.ends = time.time() + seconds
        self.votes = {}                     # user -> option index
        self.done = False

    @property
    def active(self):
        return not self.done and time.time() < self.ends

    def vote(self, user, text):
        m = VOTE.match(text)
        if not m or not self.active:
            return False
        i = int(m.group(1)) - 1
        if 0 <= i < len(self.options):
            self.votes[user.lower()] = i
            return True
        return False

    def tally(self):
        c = Counter(self.votes.values())
        return [c.get(i, 0) for i in range(len(self.options))]

    def winner(self):
        t = self.tally()
        best = max(t)
        return self.options[random.choice([i for i, v in enumerate(t) if v == best])], t

    def label(self, i):
        k, s = self.options[i]
        return f"{GAMES[k]['name']} ({s['name']})"


class Suggestions:
    def __init__(self):
        self.reqs = []                      # (time, user, game_key)

    def add(self, user, text):
        m = SUGGEST.match(text)
        if not m:
            return None
        key = match_game(m.group(1))
        if key:
            self.reqs = [r for r in self.reqs if r[1].lower() != user.lower()] + [(time.time(), user, key)]
        return key or ""

    def waiting(self, benched, current):
        """True if chat has asked for a game other than the one being played (in the last 20 minutes)."""
        now = time.time()
        return any(now - t < 1200 and k != current and benched.get(k, 0) < now for t, _, k in self.reqs)

    def take(self, benched):
        """Most-requested game from the last 20 minutes, with who asked first."""
        now = time.time()
        live = [r for r in self.reqs if now - r[0] < 1200 and benched.get(r[2], 0) < now]
        if not live:
            return None
        top = Counter(r[2] for r in live).most_common(1)[0][0]
        user = next(r[1] for r in live if r[2] == top)
        self.reqs = [r for r in self.reqs if r[2] != top]
        return top, user


def make_poll(benched, current):
    now = time.time()
    keys = [k for k in GAMES if benched.get(k, 0) < now and k != current]
    random.shuffle(keys)
    return Poll([(k, random.choice(GAMES[k]["strategies"])) for k in keys[:3]])


ENTER = re.compile(r"^\s*!(?:enter|join|giveaway)\b", re.I)


class Giveaway:
    """Chat giveaway (optional human-started feature, off by default during contest runs).
    Viewers type !enter; one entry each; a random entrant wins when time runs out.
    Prizes must be non-monetary (shoutouts, picking Hubie's next game, a gifted Kick sub...)."""

    def __init__(self, prize, seconds=180):
        self.prize = prize
        self.ends = time.time() + seconds
        self.entrants = {}                  # lower name -> display name
        self.done = False

    @property
    def active(self):
        return not self.done and time.time() < self.ends

    def enter(self, user, text):
        if not ENTER.match(text) or not self.active:
            return False
        self.entrants.setdefault(user.lower(), user)
        return True

    def draw(self):
        self.done = True
        return random.choice(list(self.entrants.values())) if self.entrants else None


class ChatRate:
    """How busy chat is, so Hubie and the bot don't answer every single line."""

    def __init__(self):
        self.times = []
        self.last_reply = {}                # lower user -> last time the bot/Hubie answered them
        self.sent = []                      # times of bot text replies

    def seen(self, ts):
        self.times = [t for t in self.times if ts - t < 120] + [ts]

    def per_min(self):
        now = time.time()
        return len([t for t in self.times if now - t < 120]) / 2

    def mode(self):
        r = self.per_min()
        return "quiet" if r < 4 else ("normal" if r < 12 else "busy")

    def should_reply(self, user, first_time, question, mention):
        """Decide if this message gets an answer at all."""
        now = time.time()
        mode = self.mode()
        cap, user_gap, chance = {"quiet": (8, 45, 1.0), "normal": (6, 75, 0.35), "busy": (4, 150, 0.0)}[mode]
        self.sent = [t for t in self.sent if now - t < 60]
        if first_time:                                  # always welcome newcomers (if not flooding)
            return len(self.sent) < cap + 2, mode
        if len(self.sent) >= cap:                       # bot already chatty this minute
            return False, mode
        if now - self.last_reply.get(user.lower(), 0) < (user_gap / 3 if (question or mention) else user_gap):
            return False, mode                          # don't answer the same person every line
        if question or mention:
            return True, mode
        return random.random() < chance, mode

    def replied(self, user):
        now = time.time()
        self.last_reply[user.lower()] = now
        self.sent.append(now)
