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
            return key
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
