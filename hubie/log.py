import json
import sys
import time

from .config import LOG_DIR

_f = open(LOG_DIR / "hubie.jsonl", "a", buffering=1)


def log(kind, **kv):
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kind": kind, **kv}
    line = json.dumps(rec, default=str)
    _f.write(line + "\n")
    print(line, file=sys.stderr, flush=True)
