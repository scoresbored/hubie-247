"""Hubie's sound effects, all synthesized here with numpy (original sounds, nothing sampled).

Every pop-out move gets a "bloop" as he springs up, its own sound, and a little "whoop" as he ducks
back down. Confetti gets a party-popper crackle, the rain cloud gets soft rain.
Volume: HUBIE_SFX_VOLUME (default 0.5). Effects never move Hubie's mouth (that follows his voice only).
"""
import os

import numpy as np

from .audio import RATE

VOL = float(os.environ.get("HUBIE_SFX_VOLUME", "0.5"))


def _t(secs):
    return np.arange(int(secs * RATE)) / RATE


def _env(n, attack=0.005, release=0.08):
    e = np.ones(n)
    a, r = min(n, int(attack * RATE)), min(n, int(release * RATE))
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def _sweep(f0, f1, secs, shape="sine", curve=1.0):
    t = _t(secs)
    f = f0 + (f1 - f0) * (t / secs) ** curve
    ph = 2 * np.pi * np.cumsum(f) / RATE
    if shape == "square":
        return np.sign(np.sin(ph)) * 0.6
    if shape == "saw":
        return 2 * ((ph / (2 * np.pi)) % 1) - 1
    return np.sin(ph)


def _tone(freq, secs, decay=6.0, harmonics=(1, 0.5, 0.25)):
    t = _t(secs)
    s = sum(a * np.sin(2 * np.pi * freq * (i + 1) * t) for i, a in enumerate(harmonics))
    return s * np.exp(-t * decay)


def _noise(secs, seed=0):
    return np.random.default_rng(seed).uniform(-1, 1, int(secs * RATE))


def _lp(x, k):
    return np.convolve(x, np.ones(k) / k, mode="same")


def _bp(x, lo, hi):
    return _lp(x, lo) - _lp(x, hi)


def _at(parts, total=None):
    """Mix [(start_secs, samples), ...] into one array."""
    end = max(int(s * RATE) + len(x) for s, x in parts)
    out = np.zeros(max(end, int((total or 0) * RATE)))
    for s, x in parts:
        i = int(s * RATE)
        out[i:i + len(x)] += x
    return out


def _seq(notes, step, fn):
    return _at([(i * step, fn(f)) for i, f in enumerate(notes) if f])


# ---------- the sounds ----------
def pop_in():
    s = _sweep(260, 950, 0.13) * _env(int(0.13 * RATE), 0.003, 0.05)
    w = _bp(_noise(0.18, 1), 3, 30) * np.linspace(0.6, 0, int(0.18 * RATE))
    return _at([(0, s * 0.8), (0, w * 0.5)])


def duck_out():
    return _sweep(700, 220, 0.16) * _env(int(0.16 * RATE), 0.003, 0.08) * 0.5


def dance():          # bouncy little bass-and-bleep groove
    bass = _seq([110, 0, 147, 0, 110, 0, 165, 147], 0.15, lambda f: _tone(f, 0.14, 12, (1, 0.6, 0.3)))
    lead = _seq([880, 1047, 1175, 1319, 1175, 1047, 1319, 1568], 0.15,
                lambda f: _sweep(f, f, 0.1, "square") * _env(int(0.1 * RATE), 0.002, 0.05) * 0.35)
    return _at([(0, bass), (0, lead)])


def wave():           # friendly "hel-lo" chime
    return _seq([1047, 1568], 0.14, lambda f: _tone(f, 0.6, 5, (1, 0.3, 0.1)))


def hat():            # classy ta-da
    arp = _seq([523, 659, 784], 0.08, lambda f: _tone(f, 0.25, 8))
    chord = sum(_tone(f, 0.9, 3, (1, 0.4)) for f in (523, 659, 784, 1047)) * 0.4
    return _at([(0, arp), (0.26, chord)])


def flex():           # power-up riser + boing
    rise = _sweep(150, 900, 0.7, "saw", 1.6) * _env(int(0.7 * RATE), 0.02, 0.05) * 0.35
    t = _t(0.35)
    boing = np.sin(2 * np.pi * np.cumsum(330 + 120 * np.sin(2 * np.pi * 14 * t) * np.exp(-t * 6)) / RATE)
    return _at([(0, rise), (0.7, boing * np.exp(-t * 7) * 0.8)])


def nap():            # two soft snores with a whistle on the way out
    def snore(seed):
        n = _lp(_noise(0.9, seed), 40)
        mod = 0.5 + 0.5 * np.sin(2 * np.pi * 28 * _t(0.9))
        body = n * mod * np.sin(np.linspace(0, np.pi, len(n))) * 3
        whistle = _sweep(900, 1500, 0.5) * np.sin(np.linspace(0, np.pi, int(0.5 * RATE))) * 0.25
        return _at([(0, body), (0.95, whistle)])
    return _at([(0, snore(2)), (1.7, snore(3))])


def hmm():            # tick... tock... tick... tock
    tick = lambda f: _bp(_noise(0.05, int(f)), 2, 12) * np.exp(-_t(0.05) * 120) * 1.5 + _tone(f, 0.05, 60) * 0.3
    return _seq([2400, 1800, 2400, 1800], 0.42, tick)


def cheer():          # party horn + crackle
    t = _t(0.7)
    horn = _sweep(380, 520, 0.7, "saw", 0.4) * _env(len(t), 0.01, 0.2) * 0.4
    horn = _lp(horn, 3)
    return _at([(0, horn), (0.1, confetti() * 0.6)])


def facepalm():       # sad trombone: wah wah wah wahhh
    def note(f, secs, vib=False):
        t = _t(secs)
        ff = f * (1 + (0.02 * np.sin(2 * np.pi * 6 * t) if vib else np.zeros_like(t)))
        s = np.sin(2 * np.pi * np.cumsum(ff) / RATE)
        s = np.tanh(2.5 * (s + 0.3 * np.sin(2 * np.pi * np.cumsum(ff * 2) / RATE)))
        return _lp(s, 6) * _env(len(t), 0.03, 0.12) * 0.35
    return _at([(0, note(311, 0.3)), (0.33, note(294, 0.3)), (0.66, note(277, 0.3)), (0.99, note(262, 0.9, True))])


def snack():          # munch munch munch
    crunch = lambda seed: _bp(_noise(0.12, seed), 2, 8) * np.exp(-_t(0.12) * 30) * 1.2
    return _at([(0, crunch(4)), (0.28, crunch(5)), (0.56, crunch(6)), (0.84, crunch(7))])


def lol():            # boing boing boing
    def boing(f):
        t = _t(0.28)
        ff = f * (1 + 0.5 * np.exp(-t * 18) * np.sin(2 * np.pi * 16 * t))
        return np.sin(2 * np.pi * np.cumsum(ff) / RATE) * np.exp(-t * 9) * 0.7
    return _seq([392, 440, 523], 0.22, boing)


def dapper():         # sparkly twinkle
    rng = np.random.default_rng(8)
    return _at([(i * 0.07, _tone(float(f), 0.4, 9, (1, 0.2))) for i, f in
                enumerate(rng.choice([1568, 1760, 2093, 2349, 2637], 8))]) * 0.6


def confetti():       # party popper: bang + crackle tail
    bang = _bp(_noise(0.08, 9), 1, 6) * np.exp(-_t(0.08) * 40) * 1.4
    rng = np.random.default_rng(10)
    crackle = _at([(rng.uniform(0, 1.2), _bp(_noise(0.012, i), 1, 4) * rng.uniform(0.2, 0.6)) for i in range(60)])
    return _at([(0, bang), (0.05, crackle)])


def rain(secs=5.0):
    n = _lp(_noise(secs, 11), 3) - _lp(_noise(secs, 11), 25)
    drops = _at([(np.random.default_rng(12).uniform(0, secs - 0.05), _tone(2200 + 600 * (i % 3), 0.04, 80) * 0.15)
                 for i in range(int(secs * 8))], secs)
    fade = np.minimum(1, np.minimum(np.arange(len(n)) / (0.5 * RATE), (len(n) - np.arange(len(n))) / (0.8 * RATE)))
    return (n * 0.35 + drops[:len(n)]) * fade


MOVES = {"dance": dance, "wave": wave, "hat": hat, "flex": flex, "nap": nap, "thinking": hmm, "cheer": cheer,
         "facepalm": facepalm, "snack": snack, "laughing": lol, "dapper": dapper}


def _pcm(x, gain=1.0):
    peak = np.max(np.abs(x)) or 1
    return (np.clip(x / peak * 0.8 * gain * VOL, -1, 1) * 32767).astype(np.int16)


class SFX:
    """Builds every effect once, then hands them to the mixer on demand."""

    def __init__(self, enter=0.45, hold=4.2):
        self.clips = {}
        try:
            for pose, fn in MOVES.items():     # bloop up, the move's sound, whoop back down
                self.clips["pop:" + pose] = _pcm(_at([(0, pop_in()), (enter * 0.7, fn()), (enter + hold, duck_out())]))
            self.clips["confetti"] = _pcm(confetti(), 0.8)
            self.clips["rain"] = _pcm(rain(5.0), 0.6)
        except Exception:                      # sound effects are nice-to-have: never break the stream
            self.clips = {}

    def play(self, name):
        from .audio import MIXER
        clip = self.clips.get(name)
        if clip is not None:
            MIXER.add_sfx(clip)


SOUNDS = None


def play(name):
    global SOUNDS
    if SOUNDS is None:
        SOUNDS = SFX()
    SOUNDS.play(name)
