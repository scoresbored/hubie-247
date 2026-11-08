"""Full-body Hubie pop-outs: viewers type a command (!dance, !wave...) and Hubie pops up over the
game, does the move for a few seconds with a little caption naming who asked, then ducks back down.

Poses are art/hubie/body/<pose>.png (cut out of green-screen renders). Only commands whose pose file
exists are active, so new poses become new commands just by adding the image.
Cooldowns keep chat from spamming it: one pop-out at a time, a short gap between them, and a
per-viewer cooldown.
"""
import math
import random
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ART = Path(__file__).resolve().parent.parent / "art" / "hubie" / "body"

# command -> (pose file, caption, card mood for the small Hubie, confetti?)
COMMANDS = {
    "dance":    ("dance", "{u} asked for a dance!", "cheering", True),
    "wave":     ("wave", "Hi {u}!", "happy", False),
    "hat":      ("hat", "Tips hat to {u}", "happy", False),
    "flex":     ("flex", "Flexing for {u}", "laughing", False),
    "nap":      ("nap", "{u} made Hubie sleepy...", "sleepy", False),
    "hmm":      ("thinking", "Hmm... good question, {u}", "thinking", False),
    "cheer":    ("cheer", "Cheering with {u}!", "cheering", True),
    "facepalm": ("facepalm", "Oh no, {u}...", "facepalm", False),
    "snack":    ("snack", "Leaf break, thanks {u}", "happy", False),
    "lol":      ("laughing", "{u} cracked Hubie up", "laughing", False),
    "dapper":   ("dapper", "Looking sharp for {u}", "happy", False),
}
ALIASES = {"tip": "hat", "tiphat": "hat", "sleep": "nap", "think": "hmm", "jump": "cheer", "eat": "snack",
           "laugh": "lol", "haha": "lol", "bowtie": "dapper", "sharp": "dapper", "hi": "wave", "hello": "wave"}
CMD = re.compile(r"^\s*!([a-z]+)\b", re.I)

HEIGHT = 330           # on-screen height of the full-body Hubie (game area is 540 tall)
ENTER, EXIT = 0.45, 0.35
HOLD = 4.2             # seconds he stays up
GAP = 6.0              # quiet seconds between pop-outs
ANIM_FPS = 12          # playback speed of animated moves
USER_COOLDOWN = 60.0   # per viewer


def _font(size, bold=True):
    for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _ease_out_back(x):
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


class Popout:
    def __init__(self, area=(0, 0, 960, 540)):
        self.area = area
        self.sprites = {}
        extra = ART / "commands.json"                 # moves added later with tools/grok_pose.py
        if extra.exists():
            try:
                import json
                for name, c in json.loads(extra.read_text()).items():
                    if name.isalpha() and name.lower() not in COMMANDS:
                        COMMANDS[name.lower()] = (c["pose"], c.get("caption", "{u} asked for this one"),
                                                  c.get("mood", "happy"), bool(c.get("confetti")))
            except Exception:
                pass
        for f in sorted(ART.glob("*.png")):           # saved at stream size by tools/cutout_body.py
            im = Image.open(f).convert("RGBA")
            if im.height > HEIGHT * 1.3:              # a raw, unscaled render: fit it to the usual height
                im = im.resize((round(im.width * HEIGHT / im.height), HEIGHT), Image.LANCZOS)
            self.sprites[f.stem] = im
        self.anims = {}                               # pose -> animation frames (tools/grok_pose.py animate)
        for d in sorted(p for p in ART.iterdir() if p.is_dir()) if ART.exists() else []:
            frames = [Image.open(f).convert("RGBA") for f in sorted(d.glob("frame_*.png"))]
            if len(frames) >= 4:
                self.anims[d.name] = frames
                self.sprites.setdefault(d.name, frames[0])
        self.font = _font(20)
        self.active = None           # dict(pose, caption, t0, x, side)
        self.last_end = 0.0
        self.user_last = {}

    # ---------- commands ----------
    def available(self):
        """Commands whose pose image exists."""
        return sorted(c for c, (pose, *_) in COMMANDS.items() if pose in self.sprites)

    def parse(self, text):
        m = CMD.match(text or "")
        if not m:
            return None
        c = m.group(1).lower()
        c = ALIASES.get(c, c)
        return c if c in COMMANDS else ("help" if c in ("commands", "help", "moves") else None)

    def trigger(self, cmd, user, now=None):
        """Start a pop-out. Returns (True, card_mood, confetti) or (False, reason)."""
        now = now or time.time()
        pose, caption, mood, confetti = COMMANDS[cmd]
        if pose not in self.sprites:
            return False, "not yet"
        if self.active or now - self.last_end < GAP:
            return False, "busy"
        if now - self.user_last.get(user.lower(), 0) < USER_COOLDOWN:
            return False, "cooldown"
        self.user_last[user.lower()] = now
        x0, y0, x1, y1 = self.area
        sp = self.sprites[pose]
        side = random.choice(["left", "right"])
        x = x0 + 40 if side == "left" else x1 - sp.width - 40
        self.active = dict(pose=pose, caption=caption.format(u=user[:18]), t0=now, x=x)
        return True, mood, confetti, pose

    # ---------- drawing ----------
    def draw(self, img, now):
        a = self.active
        if not a:
            return
        t = now - a["t0"]
        total = ENTER + HOLD + EXIT
        if t > total:
            self.active, self.last_end = None, now
            return
        x0, y0, x1, y1 = self.area
        sp = self.sprites[a["pose"]]
        if t < ENTER:                                   # springs up from below the game's bottom edge
            k = _ease_out_back(t / ENTER)
        elif t < ENTER + HOLD:
            k = 1.0
        else:                                           # ducks back down
            e = (t - ENTER - HOLD) / EXIT
            k = 1 - e * e
        anim = self.anims.get(a["pose"])
        if anim and ENTER <= t < ENTER + HOLD:        # animated move: play its frames (12 fps, looping)
            sp = anim[int((t - ENTER) * ANIM_FPS) % len(anim)]
        bob = 0 if anim else (math.sin(t * 5.5) * 4 if ENTER <= t < ENTER + HOLD else 0)
        ang = 0 if anim else (math.sin(t * 2.6) * 3 if ENTER <= t < ENTER + HOLD else 0)
        frame = sp.rotate(ang, resample=Image.BILINEAR, expand=True) if abs(ang) > 0.2 else sp
        y = y1 - round(frame.height * k) + bob
        x = a["x"] - (frame.width - sp.width) // 2
        # clip at the game's bottom edge so he really pops up out of it
        visible_h = max(0, min(frame.height, y1 - y))
        if visible_h <= 0:
            return
        crop = frame.crop((0, 0, frame.width, visible_h))
        img.paste(crop, (int(x), int(y)), crop)
        # caption bubble while he's up
        if t > ENTER * 0.6 and t < ENTER + HOLD:
            d = ImageDraw.Draw(img)
            tw = d.textlength(a["caption"], font=self.font)
            bx = int(min(max(x0 + 10, a["x"] + sp.width / 2 - tw / 2 - 14), x1 - tw - 38))
            by = int(max(y0 + 10, y - 50))
            d.rounded_rectangle((bx, by, bx + tw + 28, by + 38), 14, fill=(255, 255, 255), outline=(30, 30, 40), width=2)
            d.text((bx + 14, by + 8), a["caption"], font=self.font, fill=(25, 25, 35))
