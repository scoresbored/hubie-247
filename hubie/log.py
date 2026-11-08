import gzip
import json
import os
import shutil
import sys
import time

from .config import LOG_DIR

PATH = LOG_DIR / "hubie.jsonl"
MAX_BYTES = 40 * 1024 * 1024       # rotate at 40 MB, keep 3 compressed old logs
KEEP = 3
_f = open(PATH, "a", buffering=1)
_n = 0


def _rotate():
    """hubie.jsonl -> hubie.jsonl.1.gz (older ones shift up, the oldest is deleted)."""
    global _f
    _f.close()
    for i in range(KEEP, 0, -1):
        src = LOG_DIR / f"hubie.jsonl.{i}.gz"
        if src.exists():
            if i == KEEP:
                src.unlink()
            else:
                src.rename(LOG_DIR / f"hubie.jsonl.{i + 1}.gz")
    with open(PATH, "rb") as fi, gzip.open(LOG_DIR / "hubie.jsonl.1.gz", "wb") as fo:
        shutil.copyfileobj(fi, fo)
    open(PATH, "w").close()
    _f = open(PATH, "a", buffering=1)


def log(kind, **kv):
    global _n
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kind": kind, **kv}
    line = json.dumps(rec, default=str)
    _f.write(line + "\n")
    print(line, file=sys.stderr, flush=True)
    _n += 1
    if _n % 500 == 0:
        try:
            if os.path.getsize(PATH) > MAX_BYTES:
                _rotate()
        except Exception:
            pass
