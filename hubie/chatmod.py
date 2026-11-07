"""Chat moderation. check() returns None (fine) or (minutes, reason) for a timeout.

Every flagged message is DELETED from Kick chat, the user is muted, and the message never
reaches Hubie's brain, his voice or the on-screen chat. Two layers:
  1. fast word rules (below), instant
  2. an AI check (brain.moderate) for rudeness, harassment, sexual content and threats that
     the word rules miss; it runs before a message is shown on stream
Repeat offenders get longer mutes (strikes reset after 24h).
"""
import re
import time
from collections import defaultdict, deque

LINK = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|net|org|io|gg|xyz|bet|casino|co|me|ly|tv)\b|discord\.gg)", re.I)
PROMO = re.compile(r"(promo ?code|ref(erral)? ?code|use (my )?code|bonus code|free (money|spins|crypto)|"
                   r"giveaway.*(dm|link)|dm me|deposit (now|here)|sign ?up (with|using)|airdrop)", re.I)

# letters people swap in to dodge filters
_LEET = str.maketrans({"0": "o", "1": "i", "!": "i", "3": "e", "4": "a", "@": "a", "$": "s", "5": "s", "7": "t", "+": "t"})


def _norm(text):
    t = text.lower().translate(_LEET)
    t = re.sub(r"[\*\.\-_~]", "", t)              # f.u.c.k, f*ck, s-h-i-t
    return re.sub(r"(.)\1{2,}", r"\1\1", t)       # fuuuuck -> fuuck

# (minutes, reason, pattern) - checked in order, worst first. Kick's own AutoMod catches more on top.
RULES = [
    (60, "hateful language", re.compile(
        r"\b(n[i]gg(er|a|uh)s?|f[a]gg?(ot)?s?|k[i]ke|sp[i]c|ch[i]nk|r[e]t[a]rd(ed)?s?|tr[a]nn(y|ie)s?|"
        r"towel ?head|wetback|coon|dyke)\b")),
    (60, "threats or telling people to hurt themselves", re.compile(
        r"\b(kys|kill (your|ur) ?self|go die|hope you die|i('?ll| will) (kill|find|hurt) (you|u)|neck (your|ur)self|"
        r"unalive (your|ur)self)\b")),
    (30, "sexual content", re.compile(
        r"\b(porn|nudes?|onlyfans|of link|sex(ting)?|blowjob|bj|handjob|cum(ming|shot)?|horny|dildo|"
        r"suck (my|ur|your) (dick|cock|d)|rape|raping|molest)\b")),
    (15, "vulgar language", re.compile(
        r"\b(cunt|whore|slut|pussy|cock|dick ?(head|sucker)?|motherfuck\w*|mf|twat|wank\w*|bastard|"
        r"bitch(es|ass)?|skank|hoe)\b")),
    (5, "no swearing in Hubie's chat", re.compile(
        r"(f+u+c*k+|fck|fuk|\bfk\b|sh+i+t+|\bass(hole|hat|wipe)?\b|\bwtf\b|\bstfu\b|\bgtfo\b|\bpiss|"
        r"\bdamn ?you\b|\bbs\b)")),
    (5, "be respectful", re.compile(
        r"\b(shut ?up|stupid|idiot|moron|dumb ?(ass|bot|sloth)?|loser|you suck|u suck|trash (bot|stream|sloth)|"
        r"ugly|pathetic|clown|nobody (cares|asked)|f off|get lost|go away)\b")),
]
# short harmless messages skip the AI check (saves time and money): emotes, "lol", numbers, "gg", "!play dice"
TRIVIAL_SAFE = re.compile(r"^\s*(lol+|lmao+|gg+|ok+|k|w+|l+|nice|hi+|hey+|yo+|xd+|haha+|let'?s go+|[\d\s.x!?]+|"
                          r"!\w+( \w+)?|[\W_]+)\s*$", re.I)
UNDERAGE = re.compile(r"\bi(?:'?m| am)\s+(1[0-7]|[5-9])\b(?!\s*(?:k|%|x|\d))", re.I)

_recent = defaultdict(lambda: deque(maxlen=4))
_strikes = defaultdict(list)           # user -> times they were muted


def bad_words(text):
    """(minutes, reason) if the text breaks a word rule, else None. Also used on usernames."""
    t = _norm(text)
    for minutes, reason, rx in RULES:
        if rx.search(t):
            return minutes, reason
    return None


def escalate(user, minutes):
    """2nd strike in 24h = 3x, 3rd+ = 24 hours."""
    now = time.time()
    s = _strikes[user.lower()] = [t for t in _strikes[user.lower()] if now - t < 86400] + [now]
    if len(s) >= 3:
        return 1440
    return minutes * 3 if len(s) == 2 else minutes


def check(user, text):
    t = text.strip()
    hit = bad_words(t)
    if hit:
        return hit
    if LINK.search(t):
        return (5, "no links in chat")
    if PROMO.search(t):
        return (10, "no promo or referral spam")
    letters = [c for c in t if c.isalpha()]
    if len(letters) > 20 and sum(c.isupper() for c in letters) / len(letters) > 0.8:
        return (1, "caps spam")
    q = _recent[user.lower()]
    now = time.time()
    q.append((now, t.lower()))
    if len(q) >= 3 and len({m for _, m in list(q)[-3:]}) == 1 and now - q[-3][0] < 60:
        return (2, "repeat spam")
    return None


def says_underage(text):
    return bool(UNDERAGE.search(text))
