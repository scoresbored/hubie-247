"""Hubie's audio mixer. The stream pulls exactly one video-frame's worth of audio for every
video frame it sends, so the voice can never drift away from the picture (or the mouth)."""
import array
import asyncio
import math
import os
import random
import threading
import wave

from .config import FPS

RATE = 22050                      # Piper voices speak at 22.05 kHz
PER_FRAME = RATE // FPS           # 735 samples per video frame
SILENCE = bytes(PER_FRAME * 2)


def _load_mono16(path):
    with wave.open(str(path)) as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        return array.array("h")
    s = array.array("h", raw)
    if ch > 1:
        s = array.array("h", s[::ch])
    if sr != RATE and len(s):                       # simple resample to 22.05 kHz
        step = sr / RATE
        s = array.array("h", (s[min(len(s) - 1, int(i * step))] for i in range(int(len(s) / step))))
    return s


class Mixer:
    def __init__(self):
        self.lock = threading.Lock()
        self.buf = array.array("h")
        self.emitted = 0          # total samples handed to the stream
        self.waiters = []         # (end_sample, future, loop)
        self.level = 0.0
        self.tracks = []          # list of array('h') music loops
        self.track = None
        self.tpos = 0
        self.music_vol = float(os.environ.get("HUBIE_MUSIC_VOLUME", "0.12"))
        self.duck = 1.0           # drops while Hubie talks so his voice stays clear

    def set_music(self, paths):
        loaded = [_load_mono16(p) for p in paths]
        with self.lock:
            self.tracks = [t for t in loaded if len(t)]
            self.track, self.tpos = None, 0

    def _music_chunk(self):
        if not self.tracks or self.music_vol <= 0:
            return None
        if self.track is None or self.tpos + PER_FRAME > len(self.track):
            choices = [t for t in self.tracks if t is not self.track] or self.tracks
            self.track, self.tpos = random.choice(choices), 0
        c = self.track[self.tpos:self.tpos + PER_FRAME]
        self.tpos += PER_FRAME
        return c

    def enqueue(self, wav_path):
        """Queue a clip; returns a future that finishes when the clip has fully gone out."""
        samples = _load_mono16(wav_path)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        with self.lock:
            end = self.emitted + len(self.buf) + len(samples)   # sample count when this clip finishes
            self.buf.extend(samples)
            self.waiters.append((end, fut, loop))
        return fut

    def skip(self, frames):
        """Throw away `frames` worth of audio (used when video frames had to be skipped)."""
        for _ in range(max(0, int(frames))):
            self.next_chunk()

    def backlog(self):
        with self.lock:
            return len(self.buf) / RATE

    def next_chunk(self):
        """One video frame of audio (bytes) + its loudness 0..1 for the mouth."""
        with self.lock:
            if len(self.buf) >= PER_FRAME:
                chunk = self.buf[:PER_FRAME]
                del self.buf[:PER_FRAME]
            else:
                chunk = self.buf[:] + array.array("h", bytes((PER_FRAME - len(self.buf)) * 2))
                del self.buf[:]
            self.emitted += PER_FRAME
            done = [w for w in self.waiters if w[0] <= self.emitted]
            self.waiters = [w for w in self.waiters if w[0] > self.emitted]
        for _, fut, loop in done:
            loop.call_soon_threadsafe(lambda f=fut: f.done() or f.set_result(True))
        rms = math.sqrt(sum(x * x for x in chunk) / PER_FRAME) / 32768
        self.level = min(1.0, rms * 6)          # mouth follows the voice only
        with self.lock:
            m = self._music_chunk()
        if m is not None:
            target = 0.35 if self.level > 0.02 else 1.0
            self.duck += (target - self.duck) * (0.35 if target < self.duck else 0.04)   # fast duck, slow return
            g = self.music_vol * self.duck
            chunk = array.array("h", (max(-32768, min(32767, v + int(mv * g))) for v, mv in zip(chunk, m)))
        return chunk.tobytes(), self.level


MIXER = Mixer()
