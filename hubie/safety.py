"""Every line Hubie says passes through here first.

Hard rules from the contest: never promise wins, never push deposits, demo play only.
If a line breaks a rule it is thrown away and a safe fallback is used instead.
"""
import random
import re

BLOCK = re.compile(
    r"(deposit|sign ?up|signing up|register|promo|bonus|\bcode\b|referr|affiliate|"
    r"guarantee|can'?t lose|sure (thing|win|bet)|free money|get rich|easy money|"
    r"real money|cash ?out|withdraw|you('| wi)ll win|you will win|we('| wi)ll win|"
    r"bet (more|bigger|it all|everything)|all[- ]in|chase|double down|"
    r"\$\s?\d|\d+\s?(usd|usdt|dollars|bucks)|make money|profit)",
    re.I,
)

FALLBACKS = [
    "Another one in the books. Slow and steady, that's the sloth way.",
    "The dice have spoken. I'm just here for the vibes.",
    "Okay, okay, I see you, dice. Let's go again.",
    "No rush. Sloths don't do rush. Next roll.",
    "That's the game. Demo mode, zero stress.",
    "Hanging in there. Literally. Next one.",
    "The numbers keep coming and I keep vibing.",
    "Somewhere a sloth is napping. Not this one. Roll it.",
]

RG_LINES = [
    "Quick reminder: this is demo play, zero real money on the line. Eighteen plus only, and play responsibly.",
    "Real talk, gambling is entertainment, not income. If it stops being fun, take a break.",
    "Everything you see here is demo mode, nothing real at stake. Eighteen plus, and know your limits.",
    "Sloth tip: set limits, take breaks, and never play with money you need. Eighteen plus only.",
]


def clean(line):
    """Return a safe line, or None if the line must not be said."""
    if not line:
        return None
    line = " ".join(line.replace("\n", " ").split())
    line = re.sub(r"^(hubie( says)?:)\s*", "", line, flags=re.I).strip(' "')
    check = re.sub(r"\b(no|zero|not|without|nothing|never)\s+(any\s+)?real money", "", line, flags=re.I)
    if BLOCK.search(check):
        return None
    if len(line) > 220:
        line = line[:220].rsplit(" ", 1)[0] + "."
    if sum(c.isupper() for c in line) > 0.6 * max(1, sum(c.isalpha() for c in line)):
        line = line.capitalize()
    return line


_last = []


def fallback():
    choices = [f for f in FALLBACKS if f not in _last] or FALLBACKS
    pick = random.choice(choices)
    _last[:] = (_last + [pick])[-3:]
    return pick


def rg_line():
    return random.choice(RG_LINES)


PRIZE_BLOCK = re.compile(
    r"(crypto|bitcoin|\bbtc\b|\beth\b|usdt|\bsol\b|token|coin|nft|cash|money|gift ?card|voucher|credit|"
    r"balance|chips?\b|wager|free ?spin|rakeback|tip\b|paypal|venmo|cash ?app|\d+\s?%)", re.I)


def safe_prize(text):
    """Giveaway prizes must be non-monetary (shoutout, pick the next game, a gifted Kick sub...).
    Returns the cleaned prize, or None if it looks like money, crypto, bonuses or codes."""
    text = " ".join((text or "").split())[:60]
    if not text or BLOCK.search(text) or PRIZE_BLOCK.search(text):
        return None
    return text
