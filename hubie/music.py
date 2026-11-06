"""Hubie's background music: chill lo-fi loops generated from scratch in code.
No samples, no third-party tracks, so there's nothing to license and nothing to get a
copyright strike for. Tracks are generated once and cached in /opt/hubie/music/."""
import wave
from pathlib import Path

import numpy as np

from .audio import RATE
from .config import ROOT

MUSIC_DIR = ROOT / "music"
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
QUAL = {"maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "7": [0, 4, 7, 10], "m9": [0, 3, 7, 10, 14], "maj9": [0, 4, 7, 11, 14]}
TRACKS = [
    ("sloth_lounge", 70, [("F", "maj7"), ("E", "m7"), ("D", "m7"), ("C", "maj7")]),
    ("branch_nap", 66, [("A", "m7"), ("D", "m9"), ("G", "maj7"), ("C", "maj7")]),
    ("velvet_hat", 72, [("D#", "maj7"), ("C", "m7"), ("F", "m7"), ("A#", "7")]),
    ("three_am", 68, [("D", "maj9"), ("B", "m7"), ("E", "m7"), ("A", "7")]),
]


def _freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(x, k=9):
    return np.convolve(x, np.ones(k) / k, mode="same")


def make_track(bpm, prog, bars=48, seed=0):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    chord_len = beat * 8                         # two bars per chord
    total = chord_len * (bars // 2)
    n = int(total * RATE)
    t = np.arange(n) / RATE
    out = np.zeros(n)
    for ci in range(bars // 2):
        root, q = prog[ci % len(prog)]
        base = 48 + NOTE[root]                   # around C3
        s, e = int(ci * chord_len * RATE), int((ci + 1) * chord_len * RATE)
        seg = t[s:e] - t[s]
        # soft electric-piano chord, re-struck every 2 beats with a gentle decay
        env = np.exp(-(seg % (beat * 2)) * 1.1) * (1 - np.exp(-seg * 30))
        trem = 1 + 0.08 * np.sin(2 * np.pi * 4.5 * seg)
        for iv in QUAL[q]:
            f = _freq(base + 12 + iv)
            for det in (-0.6, 0.6):
                out[s:e] += 0.05 * env * trem * np.sin(2 * np.pi * (f + det) * seg)
            out[s:e] += 0.012 * env * np.sin(2 * np.pi * 2 * f * seg)
        # round bass on beats 1 and 3
        benv = np.exp(-(seg % (beat * 2)) * 2.2) * (1 - np.exp(-seg * 60))
        out[s:e] += 0.16 * benv * np.sin(2 * np.pi * _freq(base - 12) * seg)
    # brushed hat on the off-beats + vinyl crackle, both very quiet
    hat = np.zeros(n)
    for b in np.arange(beat / 2, total, beat):
        i = int(b * RATE)
        L = min(int(0.05 * RATE), n - i)
        if L > 0:
            hat[i:i + L] += rng.normal(0, 1, L) * np.exp(-np.arange(L) / (0.012 * RATE))
    hat = hat - _lowpass(hat, 5)                 # crude high-pass
    out += 0.02 * hat
    crackle = (rng.random(n) < 0.0004) * rng.normal(0, 1, n)
    out += 0.03 * _lowpass(crackle, 3)
    out = _lowpass(out, 5)                       # warm it up
    fade = int(1.5 * RATE)                       # fade the edges so loops blend
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    out /= max(1e-9, np.max(np.abs(out)))
    return (out * 0.7 * 32767).astype(np.int16)


def ensure_tracks():
    """Generate any missing tracks; return the list of wav paths."""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (name, bpm, prog) in enumerate(TRACKS):
        p = MUSIC_DIR / f"{name}.wav"
        if not p.exists():
            data = make_track(bpm, prog, seed=i)
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(data.tobytes())
        paths.append(p)
    return paths
