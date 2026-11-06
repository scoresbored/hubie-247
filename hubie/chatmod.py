"""Chat moderation rules. Returns None (fine) or (minutes, reason) for a timeout."""
import re
import time
from collections import defaultdict, deque

LINK = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|net|org|io|gg|xyz|bet|casino|co|me|ly|tv)\b|discord\.gg)", re.I)
PROMO = re.compile(r"(promo ?code|ref(erral)? ?code|use (my )?code|bonus code|free (money|spins|crypto)|"
                   r"giveaway.*(dm|link)|dm me|deposit (now|here)|sign ?up (with|using)|airdrop)", re.I)
# Short starter list; Kick's own banned-words list and AI moderation catch more.
SLURS = re.compile(r"\b(n[i1]gg(er|a)s?|f[a@]gg?(ot)?s?|k[i1]ke|sp[i1]c|r[e3]t[a@]rd(ed)?|tr[a@]nn(y|ie))\b", re.I)
UNDERAGE = re.compile(r"\bi(?:'?m| am)\s+(1[0-7]|[5-9])\b(?!\s*(?:k|%|x|\d))", re.I)

_recent = defaultdict(lambda: deque(maxlen=4))


def check(user, text):
    t = text.strip()
    if SLURS.search(t):
        return (60, "hateful language")
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
