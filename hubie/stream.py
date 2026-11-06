"""Sends Hubie to Kick. Every video frame is sent together with exactly one frame of audio from
the mixer, so voice and picture stay locked together forever. Restarts ffmpeg if it dies."""
import asyncio
import os
import time

from .audio import MIXER, RATE
from .config import FPS, H, W, rtmp_target
from .log import log


def ffmpeg_cmd(target, audio_fd):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats",
        # raw inputs need no probing; without this ffmpeg waits for minutes of audio before starting
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-thread_queue_size", "64", "-i", "pipe:0",
        "-probesize", "32", "-analyzeduration", "0",
        "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-thread_queue_size", "256", "-i", f"pipe:{audio_fd}",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-b:v", "3000k", "-maxrate", "3000k", "-bufsize", "6000k",
        "-pix_fmt", "yuv420p", "-g", str(FPS * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-f", "flv", target,
    ]


class Streamer:
    def __init__(self, scene, offline=False):
        self.scene = scene
        self.offline = offline

    async def run(self):
        target = rtmp_target()
        if not target and not self.offline:
            log("stream_error", err="KICK_RTMP_URL / KICK_STREAM_KEY missing in .env")
            self.offline = True
        loop = asyncio.get_running_loop()
        backoff = 2
        while True:
            proc, afd_w = None, None
            if not self.offline:
                afd_r, afd_w = os.pipe()
                proc = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd(target, afd_r), stdin=asyncio.subprocess.PIPE, pass_fds=(afd_r,),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                os.close(afd_r)
                log("stream_start")
                asyncio.ensure_future(self._drain_errors(proc))
            started = time.time()
            sent = 0
            try:
                while True:
                    # how many frames *should* have gone out by now (keeps real-time pace)
                    due = int((time.time() - started) * FPS) + 1
                    if due - sent > FPS * 3:          # badly stalled: skip ahead instead of bursting
                        log("stream_skip", frames=due - sent)
                        sent = due - 1
                    raw = None
                    while sent < due:
                        chunk, _ = MIXER.next_chunk()
                        if raw is None:
                            raw = await loop.run_in_executor(None, self.scene.render)
                        if proc:
                            if proc.returncode is not None:
                                raise RuntimeError(f"ffmpeg exited {proc.returncode}")
                            await loop.run_in_executor(None, os.write, afd_w, chunk)
                            proc.stdin.write(raw)
                            await proc.stdin.drain()
                        sent += 1
                    next_t = started + sent / FPS
                    await asyncio.sleep(max(0, next_t - time.time()))
            except Exception as e:
                log("stream_down", err=repr(e)[:200], up_secs=int(time.time() - started))
                if proc and proc.returncode is None:
                    proc.kill()
                if afd_w is not None:
                    try:
                        os.close(afd_w)
                    except OSError:
                        pass
                if time.time() - started > 60:
                    backoff = 2
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _drain_errors(self, proc):
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            log("ffmpeg", msg=line.decode(errors="replace").strip()[:300])
