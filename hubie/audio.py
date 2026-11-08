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


def _soft(x):
    """Soft limiter: untouched below ~75% of full scale, then rounds off instead of clipping (no crackle)."""
    k = 24576.0
    if -k <= x <= k:
        return int(x)
    sign = 1 if x > 0 else -1
    over = (abs(x) - k) / (32767 - k)
    return int(sign * (k + (32767 - k) * math.tanh(over)))


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
        self.duck_to = 0.35       # how far the music dips under his voice
        self.sfx = []             # playing sound effects: [samples (array 'h'), position]

    def set_music(self, paths, songs=()):
        """Background music. With songs, the playlist alternates: a song, then a lo-fi loop, then the next
        song (shuffled, no repeats until all have played)... Every track is level-matched so nothing jumps."""
        import numpy as np

        def level(a, target=0.12):
            if not len(a):
                return a
            x = np.frombuffer(a, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(x[:: max(1, len(x) // 400000)] ** 2))) or 1.0
            y = np.clip(x * (target * 32768 / rms), -32768, 32767).astype(np.int16)
            return array.array("h", y.tobytes())

        loops = [level(_load_mono16(p)) for p in paths]
        tunes = [level(_load_mono16(p)) for p in songs]
        with self.lock:
            self.tracks = [t for t in loops if len(t)]
            self.songs = [t for t in tunes if len(t)]
            self.song_order, self.last_was_song = [], False
            self.track, self.tpos = None, 0

    def _next_track(self):
        songs = getattr(self, "songs", [])
        if songs and (not self.last_was_song or not self.tracks):
            if not self.song_order:
                self.song_order = random.sample(range(len(songs)), len(songs))
            self.last_was_song = True
            return songs[self.song_order.pop()]
        self.last_was_song = False
        choices = [t for t in self.tracks if t is not self.track] or self.tracks
        return random.choice(choices)

    def _music_chunk(self):
        if not (self.tracks or getattr(self, "songs", [])) or self.music_vol <= 0:
            return None
        if self.track is None or self.tpos + PER_FRAME > len(self.track):
            self.track, self.tpos = self._next_track(), 0
        c = self.track[self.tpos:self.tpos + PER_FRAME]
        self.tpos += PER_FRAME
        return c

    def add_sfx(self, samples):
        """Play a sound effect on top of everything (doesn't delay speech, doesn't move the mouth)."""
        with self.lock:
            if len(self.sfx) < 6:
                self.sfx.append([array.array("h", samples.tobytes()), 0])

    def _sfx_chunk(self):
        if not self.sfx:
            return None
        mix = [0] * PER_FRAME
        for item in self.sfx:
            clip, pos = item
            part = clip[pos:pos + PER_FRAME]
            for i, v in enumerate(part):
                mix[i] += v
            item[1] = pos + PER_FRAME
        self.sfx = [it for it in self.sfx if it[1] < len(it[0])]
        return mix

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
            fx = self._sfx_chunk()
        if m is not None:
            target = self.duck_to if self.level > 0.02 else 1.0
            self.duck += (target - self.duck) * (0.35 if target < self.duck else 0.04)   # fast duck, slow return
            g = self.music_vol * self.duck
            chunk = array.array("h", (max(-32768, min(32767, v + int(mv * g))) for v, mv in zip(chunk, m)))
        if fx is not None:
            g = 0.5 if self.level > 0.02 else 1.0          # tuck effects under his voice
            chunk = array.array("h", (_soft(v + f * g) for v, f in zip(chunk, fx)))
        return chunk.tobytes(), self.level


MIXER = Mixer()
