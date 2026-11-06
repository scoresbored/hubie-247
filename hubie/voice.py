"""Hubie's voice. v1 uses a free local voice (Piper); swap for Hubie's real voice later."""
import array
import asyncio
import math
import sys
import tempfile
import wave
from pathlib import Path

from .config import AUDIO_SINK, FPS, VOICE_MODEL
from .log import log


def envelope(wav_path, fps=FPS):
    """Loudness per video frame (0..1), used to move Hubie's mouth."""
    with wave.open(str(wav_path)) as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        return [], n / sr
    samples = array.array("h", raw)[::ch]
    step = max(1, sr // fps)
    env = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        rms = math.sqrt(sum(s * s for s in chunk) / max(1, len(chunk))) / 32768
        env.append(min(1.0, rms * 6))
    return env, n / sr


class Voice:
    def __init__(self):
        self.ok = Path(VOICE_MODEL).exists()
        self.tmp = Path(tempfile.gettempdir()) / "hubie_tts"
        self.tmp.mkdir(exist_ok=True)
        self.n = 0
        if not self.ok:
            log("voice_missing", model=VOICE_MODEL)

    async def synth(self, text):
        """Returns (wav_path, envelope, seconds) or None."""
        if not self.ok:
            return None
        self.n += 1
        out = self.tmp / f"line_{self.n % 20}.wav"
        try:
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "piper", "-m", VOICE_MODEL, "-f", str(out),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(p.communicate(text.encode()), 30)
            if p.returncode != 0 or not out.exists():
                log("tts_error", err=err.decode()[-300:])
                return None
            env, secs = envelope(out)
            log("tts", secs=round(secs, 1))
            return out, env, secs
        except Exception as e:
            log("tts_error", err=repr(e)[:200])
            return None

    async def play(self, wav):
        """Hand the clip to the stream's mixer and wait until it has been fully sent."""
        from .audio import MIXER
        try:
            fut = MIXER.enqueue(wav)
            await asyncio.wait_for(fut, 60)
        except Exception as e:
            log("play_error", err=repr(e)[:200])
