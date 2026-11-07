"""Draws every stream frame: game, Hubie, chat, stats and the safety banner.

Hubie himself is animated by avatar.py (expression frames + motion). If those frames are
missing it falls back to the single head image, then to a simple drawn placeholder.
"""
import glob
import io
import math
import os
import random
import threading
import time

from PIL import Image, ImageDraw, ImageFont

import json
from pathlib import Path

from .config import H, W

ART_DIR = Path(__file__).resolve().parent.parent / "art"

BG = (8, 18, 26)
PANEL = (14, 30, 42)
TEAL = (110, 225, 225)
WHITE = (240, 246, 248)
DIM = (140, 165, 175)
GREEN = (70, 220, 140)
RED = (240, 90, 100)

GAME_BOX = (0, 0, 960, 540)        # game video area
SIDE_X = 960                        # right column
REACTIONS = os.environ.get("HUBIE_REACTIONS", "minimal")   # "minimal" = big moments only, "full" = all
CAPTION_BOX = (0, 540, 960, 690)
BANNER_Y = 690


def _font(size, bold=False):
    pats = (["/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
            if bold else
            ["/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for p in pats + glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)[:1]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


class Scene:
    def __init__(self):
        self.lock = threading.Lock()
        self.f_caption = _font(30, True)
        self.f_small = _font(18)
        self.f_small_b = _font(18, True)
        self.f_banner = _font(20, True)
        self.f_title = _font(22, True)
        self.f_tag = _font(14, True)
        self.game_jpeg = None
        self.game_img = Image.new("RGB", (GAME_BOX[2], GAME_BOX[3]), PANEL)
        self.caption = "Hubie is waking up..."
        self.status = ""
        self.chat = [("Hubie", "Say hi in chat, I answer everyone by name.")]
        self.mood = "idle"            # idle | hit | miss
        self.mood_until = 0
        self.speech_env, self.speech_t0 = [], 0
        self.next_blink = time.time() + 3
        self.blink_until = 0
        self.stats = {"rounds": 0, "hits": 0}
        self.history = []          # recent (outcome, win) for the bottom strip
        self.poll = None           # (title, [(label, votes)], seconds_left) while a poll runs
        self._base = None
        self._dirty = True
        from . import theme
        self._theme = theme
        self.pal = theme.palette(time.time())
        self.confetti = None
        from .popout import Popout
        self.popout = Popout((0, 0, 960, 540))       # full-body Hubie for chat commands, over the game
        self.avatar = None
        try:
            from . import avatar
            if avatar.available():
                self.avatar = avatar.Avatar(300)
                self.avatar.idle_moods = REACTIONS != "minimal"     # random glances/yawns only in "full"
        except Exception:
            self.avatar = None
        self.art = None if self.avatar else self._load_art()

    def _load_art(self):
        """Official Hubie head art + where his mouth and eyes are. None = use placeholder."""
        try:
            img = Image.open(ART_DIR / "hubie_head.png").convert("RGBA")
            cfg = json.loads((ART_DIR / "hubie_head.json").read_text())
            scale = 300 / img.height
            img = img.resize((int(img.width * scale), 300), Image.LANCZOS)
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), 28, fill=255)
            img.putalpha(mask)
            return {"img": img, "cfg": cfg, "s": scale}
        except Exception:
            return None

    # ---------- state updates (any thread) ----------
    def set_game_jpeg(self, data):
        with self.lock:
            self.game_jpeg = data

    def set_caption(self, text):
        with self.lock:
            self.caption, self._dirty = text, True

    def set_status(self, text):
        with self.lock:
            self.status, self._dirty = text, True

    def add_chat(self, user, text):
        with self.lock:
            self.chat = (self.chat + [(user, text)])[-9:]
            self._dirty = True

    def on_event(self, ev):
        with self.lock:
            if self.avatar:
                self.avatar.on_round(time.time())
            self.mood = "hit" if ev["win"] else "miss"
            self.mood_until = time.time() + 2.5
            self.stats["rounds"] += 1
            self.stats["hits"] += int(ev["win"])
            shown = ev.get("outcome") if isinstance(ev.get("outcome"), (int, float)) else None
            if shown is None:
                shown = f"{ev.get('multiplier') or 0}x"
            self.history = (self.history + [(shown, ev["win"])])[-12:]
            self._dirty = True

    def set_poll(self, poll):
        with self.lock:
            self.poll, self._dirty = poll, True

    def reset_session(self):
        with self.lock:
            self.stats = {"rounds": 0, "hits": 0}
            self.history = []
            self._dirty = True

    def pop(self, cmd, user):
        """Chat command pop-out. Returns (True, mood, confetti) or (False, reason)."""
        with self.lock:
            res = self.popout.trigger(cmd, user, time.time())
        if res[0]:
            from . import popout, sfx
            sfx.play("pop:" + res[3])                         # bloop up, the move's sound, whoop down
            self.react(res[1], 3.0)
            if res[2]:
                self.effect("confetti", 3.0, sound=False)
        return res

    def effect(self, kind, secs=3.5, sound=True):
        """Screen effects: "confetti" (big hits, streaks, giveaway winners) or "rain" (cloud over Hubie)."""
        now = time.time()
        if sound:
            from . import sfx
            sfx.play(kind)
        with self.lock:
            if kind == "confetti":
                self.confetti = (now, now + secs, random.randrange(10 ** 6))
            elif kind == "rain" and self.avatar:
                self.avatar.rain(secs, now)

    def _draw_confetti(self, img, now):
        t0, until, seed = self.confetti
        t = now - t0
        if t > (until - t0) + 2.5:
            self.confetti = None
            return
        d = ImageDraw.Draw(img)
        cols = [(255, 215, 90), (240, 240, 250), (255, 110, 150), (110, 200, 255), (170, 130, 255), (120, 230, 140)]
        r = random.Random(seed)
        for i in range(110):
            x0, delay = r.random() * W, r.random() * (until - t0) * 0.6
            vx, vy = (r.random() - 0.5) * 120, 120 + r.random() * 160
            spin, size = 4 + r.random() * 8, 5 + r.random() * 5
            tt = t - delay
            if tt < 0:
                continue
            y = -20 + vy * tt + 40 * tt * tt
            if y > BANNER_Y + 10:
                continue
            x = x0 + vx * tt + math.sin(tt * 3 + i) * 18
            a = tt * spin
            w = size * abs(math.cos(a)) + 1
            ca, sa = math.cos(a * 0.7), math.sin(a * 0.7)
            d.polygon([(x + px * ca - py * sa, y + px * sa + py * ca)
                       for px, py in ((-w, -size / 2), (w, -size / 2), (w, size / 2), (-w, size / 2))],
                      fill=cols[i % len(cols)])

    def react(self, mood, secs=2.5, then=None, soft=False, big=False):
        """Show a reaction face on Hubie (shocked, cheering, facepalm, thinking, laughing, sleepy, happy).
        With HUBIE_REACTIONS=minimal (the default) only big moments change his face; otherwise he just
        chats in his box and the full-body pop-out moves carry the expressions."""
        if REACTIONS == "minimal" and not big:
            return
        if self.avatar:
            with self.lock:
                self.avatar.react(mood, secs, then=then, soft=soft, now=time.time())

    def speak(self, env):
        with self.lock:
            self.speech_env, self.speech_t0 = env, time.time()

    # ---------- drawing ----------
    def _build_base(self):
        P = self.pal
        BGc, PAN, ACC, ACC2, DIMc, LINE = P["bg"], P["panel"], P["accent"], P["accent2"], P["dim"], P["line"]
        img = Image.new("RGB", (W, H), BGc)
        img.paste(self.game_img, (0, 0))
        # bottom strip: what's playing + recent results (no captions; Hubie's words are spoken only)
        ImageDraw.Draw(img).rectangle(CAPTION_BOX, fill=PAN)
        self._theme.decorate(img, CAPTION_BOX, P, seed=7, n=14)
        d = ImageDraw.Draw(img)
        d.line((0, CAPTION_BOX[1], CAPTION_BOX[2], CAPTION_BOX[1]), fill=ACC, width=3)
        y0 = CAPTION_BOX[1]
        r, h = self.stats["rounds"], self.stats["hits"]
        d.text((20, y0 + 16), self.status or "Warming up", font=self.f_title, fill=ACC)
        d.text((20, y0 + 48), f"Rounds {r}    Hits {h}" + (f"    ({100 * h // r}%)" if r else ""),
               font=self.f_small, fill=DIMc)
        if not self.poll and self.popout.available():         # tell viewers they can make him move
            hint = "Chat: type !commands to make Hubie move"
            d.text((CAPTION_BOX[2] - 16 - d.textlength(hint, font=self.f_small), y0 + 22), hint,
                   font=self.f_small, fill=P["accent2"])
        d.text((20, y0 + 92), "Recent rolls", font=self.f_small, fill=DIMc)
        x = 150
        for outcome, win in reversed(self.history):
            label = f"{outcome:.2f}" if isinstance(outcome, (int, float)) else str(outcome)
            w = d.textlength(label, font=self.f_small_b) + 22
            if x + w > (548 if self.poll else CAPTION_BOX[2] - 16):
                break
            d.rounded_rectangle((x, y0 + 86, x + w, y0 + 118), 14, fill=GREEN if win else RED)
            d.text((x + 11, y0 + 91), label, font=self.f_small_b, fill=(10, 20, 25))
            x += w + 8
        # poll box over the right side of the bottom strip
        if self.poll:
            title, opts, left = self.poll
            px = 560
            box = tuple(min(255, int(v * 1.35) + 6) for v in PAN)
            d.rounded_rectangle((px, y0 + 10, CAPTION_BOX[2] - 12, y0 + 140), 12, fill=box, outline=ACC, width=2)
            d.text((px + 14, y0 + 16), f"{title}  ·  {left}s", font=self.f_small_b, fill=ACC)
            total = max(1, sum(v for _, v in opts))
            for i, (label, v) in enumerate(opts):
                yy = y0 + 44 + i * 30
                d.text((px + 14, yy), f"{i + 1}", font=self.f_small_b, fill=WHITE)
                bw = int((CAPTION_BOX[2] - px - 200) * v / total)
                d.rounded_rectangle((px + 36, yy + 2, px + 36 + max(6, bw), yy + 22), 8, fill=ACC if v else LINE)
                d.text((px + 44 + max(6, bw), yy), f"{label}  {v}", font=self.f_small, fill=WHITE)
        # right column: title + chat
        d.rectangle((SIDE_X, 0, W, BANNER_Y), fill=PAN)
        self._theme.decorate(img, (SIDE_X, 330, W, BANNER_Y), P, seed=3, n=12)
        d = ImageDraw.Draw(img)
        d.line((SIDE_X, 0, SIDE_X, BANNER_Y), fill=ACC, width=3)
        d.text((SIDE_X + 16, 332), "HUBIE  LIVE 24/7", font=self.f_title, fill=ACC)
        tag = P["name"].upper()
        d.text((W - 14 - d.textlength(tag, font=self.f_tag), 339), tag, font=self.f_tag, fill=ACC2)
        if P["key"] == "dec":
            self._theme.string_lights(ImageDraw.Draw(img, "RGBA"), SIDE_X + 8, W - 8, 360)
            d = ImageDraw.Draw(img)
        else:
            d.line((SIDE_X + 16, 366, W - 16, 366), fill=LINE, width=2)
        y = 384 if P["key"] == "dec" else 376
        for user, text in self.chat[-8:]:
            d.text((SIDE_X + 16, y), user, font=self.f_small_b,
                   fill=ACC2 if user == "HubieBot" else ACC)
            for ln in _wrap(d, text, self.f_small, 285)[:2]:
                y += 21
                d.text((SIDE_X + 16, y), ln, font=self.f_small, fill=WHITE)
            y += 26
            if y > BANNER_Y - 30:
                break
        # safety banner: always on screen and always red, so it stands out in every theme
        d.rectangle((0, BANNER_Y, W, H), fill=(170, 30, 45))
        d.line((0, BANNER_Y, W, BANNER_Y), fill=ACC, width=2)
        msg = "18+ ONLY   |   DEMO PLAY, NO REAL MONEY   |   GAMBLE RESPONSIBLY   |   AI STREAM"
        tw = d.textlength(msg, font=self.f_banner)
        d.text(((W - tw) / 2, BANNER_Y + 4), msg, font=self.f_banner, fill=WHITE)
        return img

    def _mouth_level(self, now):
        from .audio import MIXER
        if MIXER.level > 0.02 or not self.speech_env:
            return MIXER.level            # exact loudness of the audio going out with this frame
        if not self.speech_env:
            return 0.0
        i = int((now - self.speech_t0) * 30)
        if i >= len(self.speech_env):
            self.speech_env = []
            return 0.0
        return self.speech_env[i]

    def draw_hubie(self, img, now):
        if self.avatar:
            return self._draw_hubie_avatar(img, now)
        if self.art:
            return self._draw_hubie_art(img, now)
        return self._draw_hubie_placeholder(img, now)

    def _draw_hubie_avatar(self, img, now):
        card = self.avatar.render(now, self._mouth_level(now))
        mood = self.mood if now < self.mood_until else "idle"
        glow = {"hit": GREEN, "miss": RED}.get(mood, self.pal["accent"])
        x0, y0 = SIDE_X + (W - SIDE_X - card.width) // 2, 14
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((x0 - 5, y0 - 5, x0 + card.width + 4, y0 + card.height + 4), 32, outline=glow, width=5)
        img.paste(card, (x0, y0), card)

    def _draw_hubie_art(self, img, now):
        """Official Hubie art, with a moving mouth and blinking eyes drawn on top."""
        a, c, s = self.art["img"], self.art["cfg"], self.art["s"]
        mood = self.mood if now < self.mood_until else "idle"
        glow = {"hit": GREEN, "miss": RED}.get(mood, TEAL)
        bob = math.sin(now * 1.6) * 3 + (-6 * max(0, self.mood_until - now) / 2.5 if mood == "hit" else 0)
        x0 = SIDE_X + (W - SIDE_X - a.width) // 2
        y0 = int(12 + bob)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((x0 - 5, y0 - 5, x0 + a.width + 4, y0 + a.height + 4), 32, outline=glow, width=5)
        img.paste(a, (x0, y0), a)
        d = ImageDraw.Draw(img)
        # blink
        if now > self.next_blink:
            self.blink_until, self.next_blink = now + 0.14, now + random.uniform(2.5, 6)
        if now < self.blink_until:
            ew, eh = c["eye_size"][0] * s / 2, c["eye_size"][1] * s / 2
            for ex, ey in c["eyes"]:
                cx, cy = x0 + ex * s, y0 + ey * s
                d.ellipse((cx - ew, cy - eh, cx + ew, cy + eh), fill=tuple(c["lid_color"]))
        # mouth opens with the voice
        m = self._mouth_level(now)
        if m > 0.05:
            mx, my, mw = c["mouth"]
            cx, cy, w2 = x0 + mx * s, y0 + my * s, mw * s / 2 * (0.75 + 0.25 * m)
            h = (5 + 26 * m) * s
            d.ellipse((cx - w2, cy - h * 0.35, cx + w2, cy + h), fill=tuple(c["mouth_color"]))
            if m > 0.35:
                d.ellipse((cx - w2 * 0.5, cy + h * 0.45, cx + w2 * 0.5, cy + h * 0.95), fill=tuple(c["tongue_color"]))

    def _draw_hubie_placeholder(self, img, now):
        """PLACEHOLDER Hubie: a sloth face in a top hat (used only if the art files are missing)."""
        d = ImageDraw.Draw(img)
        cx, cy = SIDE_X + 160, 192
        bob = math.sin(now * 1.6) * 4
        cy += bob
        mood = self.mood if now < self.mood_until else "idle"
        glow = {"hit": GREEN, "miss": RED}.get(mood, TEAL)
        d.ellipse((cx - 118, cy - 108, cx + 118, cy + 128), outline=glow, width=5)
        d.ellipse((cx - 105, cy - 95, cx + 105, cy + 115), fill=(150, 115, 80))      # fur
        d.ellipse((cx - 80, cy - 60, cx + 80, cy + 100), fill=(225, 205, 170))       # face
        for ex in (-40, 40):                                                          # eye patches
            d.ellipse((cx + ex - 30, cy - 20, cx + ex + 30, cy + 25), fill=(95, 70, 50))
        if now > self.next_blink:
            self.blink_until, self.next_blink = now + 0.15, now + random.uniform(2.5, 6)
        for ex in (-40, 40):
            if now < self.blink_until:
                d.line((cx + ex - 10, cy + 2, cx + ex + 10, cy + 2), fill=(20, 15, 10), width=4)
            else:
                d.ellipse((cx + ex - 10, cy - 8, cx + ex + 10, cy + 12), fill=(20, 15, 10))
                d.ellipse((cx + ex - 3, cy - 5, cx + ex + 3, cy + 1), fill=WHITE)
        d.ellipse((cx - 12, cy + 25, cx + 12, cy + 40), fill=(60, 40, 30))           # nose
        m = self._mouth_level(now)
        mh = 4 + m * 30
        if m > 0.05:
            d.ellipse((cx - 22, cy + 52, cx + 22, cy + 52 + mh), fill=(70, 25, 30))
        elif mood == "hit":
            d.arc((cx - 28, cy + 40, cx + 28, cy + 75), 20, 160, fill=(70, 25, 30), width=5)
        elif mood == "miss":
            d.arc((cx - 22, cy + 58, cx + 22, cy + 85), 200, 340, fill=(70, 25, 30), width=5)
        else:
            d.arc((cx - 24, cy + 45, cx + 24, cy + 70), 30, 150, fill=(70, 25, 30), width=4)
        # top hat
        d.rectangle((cx - 95, cy - 98, cx + 95, cy - 84), fill=(20, 20, 24))
        d.rectangle((cx - 60, cy - 178, cx + 60, cy - 92), fill=(20, 20, 24))
        d.rectangle((cx - 60, cy - 112, cx + 60, cy - 98), fill=glow)
        d.text((cx - 14, cy - 165), "H", font=self.f_caption, fill=TEAL)

    def render(self):
        """Returns one RGB frame as raw bytes."""
        now = time.time()
        with self.lock:
            if self.game_jpeg is not None:
                try:
                    g = Image.open(io.BytesIO(self.game_jpeg)).convert("RGB")
                    self.game_img = g.resize((GAME_BOX[2], GAME_BOX[3]), Image.BILINEAR)
                except Exception:
                    pass
                self.game_jpeg, self._dirty = None, True
            pal = self._theme.palette(now)
            if pal["cache"] != self.pal["cache"]:
                self.pal, self._dirty = pal, True
            if self._dirty or self._base is None:
                self._base, self._dirty = self._build_base(), False
            frame = self._base.copy()
            self.draw_hubie(frame, now)
            self.popout.draw(frame, now)
            if self.confetti:
                self._draw_confetti(frame, now)
        return frame.tobytes()
