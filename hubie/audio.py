"""Hubie's audio mixer. The stream pulls exactly one video-frame's worth of audio for every
video frame it sends, so the voice can never drift away from the picture (or the mouth)."""
import array
import asyncio
import math
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
        self.level = min(1.0, rms * 6)
        return chunk.tobytes(), self.level


MIXER = Mixer()
