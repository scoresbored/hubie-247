"""Settings. Secrets come from /opt/hubie/.env (never from the repo)."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("HUBIE_ROOT", "/opt/hubie"))


def load_env(path=ROOT / ".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

GAME_URL = os.environ.get("HUBIE_GAME_URL", "https://www.housebets.com/games/housegames-dice")
PROFILE = Path(os.environ.get("HUBIE_PROFILE", str(ROOT / "profile")))
VOICE_MODEL = os.environ.get("HUBIE_VOICE_MODEL", str(ROOT / "voices" / "en_US-ryan-high.onnx"))
AUDIO_SINK = os.environ.get("HUBIE_SINK", "hubie_out")
MODEL = os.environ.get("HUBIE_MODEL", "claude-haiku-5-5")
W, H, FPS = 1280, 720, 30
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def rtmp_target():
    url = os.environ.get("KICK_RTMP_URL", "")
    key = os.environ.get("KICK_STREAM_KEY", "")
    return (url + key) if url and key else None
