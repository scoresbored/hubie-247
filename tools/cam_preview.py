"""Render a test clip of Hubie's cam with his real voice: TTS a line, drive the avatar with the same
per-frame loudness the live mixer uses, put it on the seasonal card, and mux the audio.
Usage (from the app folder): PYTHONPATH=. python tools/cam_preview.py out.mp4 [A|B] "line one" "line two" ...
"""
import array
import asyncio
import math
import subprocess
import sys
import wave

from PIL import Image

from hubie.avatar import Avatar
from hubie.voice import Voice

RATE, FPS = 22050, 30
PER = RATE // FPS


async def main(out, pose, lines):
    v = Voice()
    clips = []
    for ln in lines:
        res = await v.synth(ln)
        clips.append(res[0])
    pcm = array.array("h")
    gap = array.array("h", [0] * int(RATE * 1.2))
    pcm.extend(gap)
    for c in clips:
        with wave.open(str(c)) as w:
            pcm.frombytes(w.readframes(w.getnframes()))
        pcm.extend(gap)
    a = Avatar(300)
    a.pose = pose
    a.next_pose_swap = 1e12                 # keep the chosen pose for the whole clip
    a.was_quiet_since = 0
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "300x300",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "/tmp/cam_v.mp4"],
                          stdin=subprocess.PIPE)
    n = len(pcm) // PER
    for i in range(n):
        chunk = pcm[i * PER:(i + 1) * PER]
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk)) / 32768
        level = min(1.0, rms * 6)
        card = a.render(1000 + i / FPS, level)
        ff.stdin.write(card.convert("RGB").tobytes())
    ff.stdin.close()
    ff.wait()
    with wave.open("/tmp/cam_a.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", "/tmp/cam_v.mp4", "-i", "/tmp/cam_a.wav", "-c:v", "copy",
                    "-c:a", "aac", "-shortest", out], check=True)
    print("wrote", out, f"({n / FPS:.1f}s, pose {pose}, cam loops: {sorted(a.cam)})")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
