"""Hubie's voice: a free local Piper voice, tuned in .env:
  HUBIE_VOICE_MODEL  path to the .onnx voice (default: Northern English male, medium)
  HUBIE_VOICE_SPEED  Piper length scale, >1 = slower (sloth pace)
  HUBIE_VOICE_PITCH  1.0 = natural, <1 = deeper (applied with ffmpeg, tempo kept)
"""
import array
import asyncio
import math
import os
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


SPEED = float(os.environ.get("HUBIE_VOICE_SPEED", "1.3"))
PITCH = float(os.environ.get("HUBIE_VOICE_PITCH", "1.0"))


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
            raw = out if PITCH == 1.0 else out.with_suffix(".raw.wav")
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "piper", "-m", VOICE_MODEL, "--length-scale", str(SPEED), "-f", str(raw),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(p.communicate(text.encode()), 30)
            if p.returncode != 0 or not raw.exists():
                log("tts_error", err=err.decode()[-300:])
                return None
            if PITCH != 1.0:                       # deeper voice, same speed
                f = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-af",
                    f"asetrate=22050*{PITCH},aresample=22050,atempo={1 / PITCH:.4f}", str(out),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.wait_for(f.wait(), 15)
                if not out.exists():
                    out = raw
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
