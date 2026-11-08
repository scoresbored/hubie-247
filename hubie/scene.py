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

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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

# Layout v2 (1280x720): game in a framed "monitor" on the left, Hubie's cam + live clock + compact chat
# on the right, a dashboard bar under the game, the red safety banner along the bottom.
GAME_BOX = (16, 16, 928, 529)       # game video area (912x513, 16:9)
GAME_W, GAME_H = GAME_BOX[2] - GAME_BOX[0], GAME_BOX[3] - GAME_BOX[1]
SIDE_X = 944                        # right column
CAM = (954, 16)                     # Hubie's 300x300 cam
CLOCK_BOX = (944, 330, 1264, 386)
CHAT_BOX = (944, 396, 1264, 646)
HINT_BOX = (944, 654, 1264, 682)
HUD_BOX = (16, 541, 928, 682)
REACTIONS = os.environ.get("HUBIE_REACTIONS", "minimal")   # "minimal" = big moments only, "full" = all
CAPTION_BOX = HUD_BOX
BANNER_Y = 692


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
        self.f_hud = _font(26, True)
        self.f_chip_l = _font(12, True)
        self.f_chip_v = _font(19, True)
        self.f_chat_n = _font(15, True)
        self.f_chat = _font(15)
        self.f_clock = _font(22, True)
        self.f_mini = _font(12, True)
        self.started = time.time()
        self._backdrop, self._backdrop_key = None, None
        self._game_mask = Image.new("L", (GAME_W, GAME_H), 0)
        ImageDraw.Draw(self._game_mask).rounded_rectangle((0, 0, GAME_W - 1, GAME_H - 1), 14, fill=255)
        self.game_jpeg = None
        self.game_img = Image.new("RGB", (GAME_W, GAME_H), PANEL)
        self.caption = "Hubie is waking up..."
        self.status = ""
        self.chat = [("Hubie", "Say hi in chat, I answer everyone by name.")]
        self.mood = "idle"            # idle | hit | miss
        self.mood_until = 0
        self.speech_env, self.speech_t0 = [], 0
        self.next_blink = time.time() + 3
        self.blink_until = 0
        self.stats = {"rounds": 0, "hits": 0, "best": 0.0, "streak": 0}   # streak: +wins / -misses
        self.history = []          # recent (outcome, win) for the bottom strip
        self.poll = None           # (title, [(label, votes)], seconds_left) while a poll runs
        self._base = None
        self._dirty = True
        from . import theme
        self._theme = theme
        self.pal = theme.palette(time.time())
        self.confetti = None
        from .popout import Popout
        self.popout = Popout(GAME_BOX)               # full-body Hubie for chat commands, over the game
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
            if ev["win"]:
                try:
                    self.stats["best"] = max(self.stats["best"], float(ev.get("multiplier") or 0))
                except (TypeError, ValueError):
                    pass
            st = self.stats["streak"]
            self.stats["streak"] = (st + 1 if st > 0 else 1) if ev["win"] else (st - 1 if st < 0 else -1)
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
            self.stats = {"rounds": 0, "hits": 0, "best": 0.0, "streak": 0}   # streak: +wins / -misses
            self.history = []
            self._dirty = True

    def happy_talk(self, on):
        """While Hubie says a happy line (big hit, follow, winner) his mouth may open into the big grin."""
        if self.avatar:
            self.avatar.happy = bool(on)

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
    # ---------- layout v2 ----------
    def _run_start(self):
        """When the current contest run started (Telegram /run timer), else when Hubie started."""
        try:
            from .config import ROOT
            st = json.loads((ROOT / "run_state.json").read_text())
            if st.get("run_started"):
                return float(st["run_started"])
        except Exception:
            pass
        return self.started

    def _make_backdrop(self):
        """Everything that only changes with the season / time of day: background, glows, glass panels,
        the monitor bezel around the game and the safety banner. Built once per palette change."""
        P = self.pal
        BGc, PAN, ACC, ACC2, LINE = P["bg"], P["panel"], P["accent"], P["accent2"], P["line"]
        img = Image.new("RGBA", (W, H), BGc + (255,))
        d = ImageDraw.Draw(img)
        top, bot = tuple(min(255, int(v * 1.6) + 6) for v in BGc), tuple(int(v * 0.55) for v in BGc)
        for y in range(H):                                       # deep vertical gradient
            k = y / H
            d.line((0, y, W, y), fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bot)) + (255,))
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))           # soft coloured light behind the monitor and cam
        g = ImageDraw.Draw(glow)
        g.ellipse((80, -60, 860, 600), fill=ACC + (60,))
        g.ellipse((900, -40, 1320, 420), fill=ACC2 + (55,))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))
        self._theme.decorate(img, (0, 0, W, BANNER_Y), dict(P), seed=11, n=34)   # faint seasonal pattern
        vig = Image.new("L", (W, H), 0)                          # vignette
        ImageDraw.Draw(vig).rounded_rectangle((-40, -40, W + 40, H + 40), 220, fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(120))
        img = Image.composite(img, Image.new("RGBA", (W, H), (0, 0, 0, 255)), vig.point(lambda v: 90 + v * 165 // 255))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        L = ImageDraw.Draw(layer)
        glass = tuple(int(v * 0.8) for v in PAN) + (205,)
        x0, y0, x1, y1 = GAME_BOX                                 # monitor bezel
        L.rounded_rectangle((x0 - 10, y0 - 10, x1 + 9, y1 + 9), 22, fill=(18, 20, 28, 255))
        L.rounded_rectangle((x0 - 10, y0 - 10, x1 + 9, y1 + 9), 22, outline=ACC + (200,), width=2)
        L.rounded_rectangle((x0 - 4, y0 - 4, x1 + 3, y1 + 3), 17, outline=(255, 255, 255, 28), width=2)
        L.line((x0 + 40, y0 - 10, x0 + 160, y0 - 10), fill=ACC + (255,), width=4)        # little highlight accents
        L.line((x1 - 160, y1 + 9, x1 - 40, y1 + 9), fill=ACC2 + (255,), width=4)
        for box in (HUD_BOX, CLOCK_BOX, CHAT_BOX, HINT_BOX):      # glass panels
            L.rounded_rectangle(box, 16, fill=glass, outline=(255, 255, 255, 30), width=1)
        L.rounded_rectangle((HUD_BOX[0], HUD_BOX[1], HUD_BOX[0] + 6, HUD_BOX[3]), 3, fill=ACC + (255,))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(1)) if False else layer)
        d = ImageDraw.Draw(img)
        # chat header + hint
        d.text((CHAT_BOX[0] + 16, CHAT_BOX[1] + 10), "CHAT", font=self.f_chip_l, fill=ACC)
        tag = P["name"].upper()
        d.text((CHAT_BOX[2] - 16 - d.textlength(tag, font=self.f_chip_l), CHAT_BOX[1] + 10), tag, font=self.f_chip_l, fill=ACC2)
        d.line((CHAT_BOX[0] + 16, CHAT_BOX[1] + 30, CHAT_BOX[2] - 16, CHAT_BOX[1] + 30), fill=LINE, width=1)
        if P["key"] == "dec":
            self._theme.string_lights(ImageDraw.Draw(img, "RGBA"), CHAT_BOX[0] + 6, CHAT_BOX[2] - 6, CHAT_BOX[1] - 6, n=10)
            d = ImageDraw.Draw(img)
        hint = "Type !commands to make Hubie move"
        d.text(((HINT_BOX[0] + HINT_BOX[2] - d.textlength(hint, font=self.f_chip_l)) / 2, HINT_BOX[1] + 7), hint,
               font=self.f_chip_l, fill=ACC2)
        # clock card labels (the time itself is drawn every frame)
        d.ellipse((CLOCK_BOX[0] + 16, CLOCK_BOX[1] + 13, CLOCK_BOX[0] + 26, CLOCK_BOX[1] + 23), fill=(240, 60, 70))
        d.text((CLOCK_BOX[0] + 32, CLOCK_BOX[1] + 9), "ON AIR", font=self.f_chip_l, fill=(240, 90, 100))
        lab = "AI-RUN · NO HUMAN INPUT"
        d.text((CLOCK_BOX[2] - 16 - d.textlength(lab, font=self.f_chip_l), CLOCK_BOX[1] + 9), lab, font=self.f_chip_l, fill=ACC)
        # safety banner, always red so it stands out in every theme
        d.rectangle((0, BANNER_Y, W, H), fill=(150, 24, 38))
        d.line((0, BANNER_Y, W, BANNER_Y), fill=(255, 120, 130), width=1)
        msg = "18+ ONLY   •   DEMO PLAY, NO REAL MONEY   •   GAMBLE RESPONSIBLY   •   AI STREAM"
        d.text(((W - d.textlength(msg, font=self.f_tag)) / 2, BANNER_Y + 6), msg, font=self.f_tag, fill=WHITE)
        return img.convert("RGB")

    def _build_base(self):
        P = self.pal
        if self._backdrop is None or self._backdrop_key != P["cache"]:
            self._backdrop, self._backdrop_key = self._make_backdrop(), P["cache"]
        ACC, ACC2, DIMc, LINE = P["accent"], P["accent2"], P["dim"], P["line"]
        img = self._backdrop.copy()
        img.paste(self.game_img, (GAME_BOX[0], GAME_BOX[1]), self._game_mask)
        d = ImageDraw.Draw(img)
        # ---- dashboard bar under the game ----
        x0, y0, x1, y1 = HUD_BOX
        status = self.status or "Warming up"
        game, _, strat = status.replace("Now playing: ", "").partition(" - ")
        d.text((x0 + 22, y0 + 12), game.upper(), font=self.f_hud, fill=ACC)
        if strat:
            d.text((x0 + 26 + d.textlength(game.upper(), font=self.f_hud), y0 + 20), strat, font=self.f_chip_l, fill=DIMc)
        r, h = self.stats["rounds"], self.stats["hits"]
        stk = self.stats["streak"]
        chips = [("ROUNDS", f"{r:,}", WHITE), ("HIT RATE", f"{100 * h // r}%" if r else "-", WHITE),
                 ("BEST", f"{self.stats['best']:.2f}x" if self.stats["best"] else "-", GREEN),
                 ("STREAK", (f"W{stk}" if stk > 0 else f"L{-stk}") if stk else "-", GREEN if stk > 0 else RED)]
        if not self.poll:
            cx = x1 - 16
            for label, val, col in reversed(chips):
                w = max(d.textlength(val, font=self.f_chip_v), d.textlength(label, font=self.f_chip_l)) + 24
                cx -= w
                d.rounded_rectangle((cx, y0 + 10, cx + w - 8, y0 + 56), 10, fill=(0, 0, 0), outline=LINE)
                d.text((cx + 8, y0 + 13), label, font=self.f_chip_l, fill=DIMc)
                d.text((cx + 8, y0 + 29), val, font=self.f_chip_v, fill=col)
                cx -= 2
        d.text((x0 + 22, y0 + 82), "RECENT", font=self.f_chip_l, fill=DIMc)
        x = x0 + 86
        for outcome, win in reversed(self.history):
            label = f"{outcome:.2f}" if isinstance(outcome, (int, float)) else str(outcome)
            w = d.textlength(label, font=self.f_chip_l) + 20
            if x + w > (x0 + 500 if self.poll else x1 - 16):
                break
            d.rounded_rectangle((x, y0 + 76, x + w, y0 + 102), 13, fill=GREEN if win else RED)
            d.text((x + 10, y0 + 82), label, font=self.f_chip_l, fill=(10, 20, 25))
            x += w + 6
        if self.poll:                                            # poll takes the right half of the bar
            title, opts, left = self.poll
            px = x0 + 520
            d.rounded_rectangle((px, y0 + 8, x1 - 10, y1 - 8), 12, fill=(0, 0, 0), outline=ACC, width=2)
            d.text((px + 12, y0 + 14), f"{title}  ·  {left}s", font=self.f_chip_l, fill=ACC)
            total = max(1, sum(v for _, v in opts))
            for i, (label, v) in enumerate(opts[:3]):
                yy = y0 + 36 + i * 30
                d.text((px + 12, yy), f"{i + 1}", font=self.f_chat_n, fill=WHITE)
                bw = int((x1 - px - 190) * v / total)
                d.rounded_rectangle((px + 32, yy + 2, px + 32 + max(6, bw), yy + 20), 8, fill=ACC if v else LINE)
                d.text((px + 40 + max(6, bw), yy), f"{label}  {v}", font=self.f_chat, fill=WHITE)
        # ---- compact chat ----
        cx0, cy0, cx1, cy1 = CHAT_BOX
        rows = []
        for user, text in reversed(self.chat[-6:]):
            lines = _wrap(d, text, self.f_chat, cx1 - cx0 - 32)[:2]
            rows.insert(0, (user, lines))
        y = cy0 + 40
        need = sum(19 * (1 + len(l)) + 6 for _, l in rows)
        while rows and need > cy1 - cy0 - 48:                    # keep only what fits, newest at the bottom
            u, l = rows.pop(0)
            need -= 19 * (1 + len(l)) + 6
        for user, lines in rows:
            d.text((cx0 + 16, y), user, font=self.f_chat_n, fill=ACC2 if user == "HubieBot" else ACC)
            for ln in lines:
                y += 19
                d.text((cx0 + 16, y), ln, font=self.f_chat, fill=WHITE)
            y += 25
        return img

    def _draw_clock(self, img, now):
        secs = int(now - self._run_start())
        hh, mm, ss = secs // 3600, secs % 3600 // 60, secs % 60
        txt = f"{hh:02d}h {mm:02d}m {ss:02d}s"
        d = ImageDraw.Draw(img)
        d.text((CLOCK_BOX[0] + 16, CLOCK_BOX[1] + 25), txt, font=self.f_clock, fill=WHITE)

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
        x0, y0 = CAM
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((x0 - 4, y0 - 4, x0 + card.width + 3, y0 + card.height + 3), 30, outline=glow, width=4)
        img.paste(card, (x0, y0), card)
        # name plate, webcam style
        d.rounded_rectangle((x0 + 10, y0 + card.height - 40, x0 + 118, y0 + card.height - 12), 12, fill=(12, 14, 20))
        d.ellipse((x0 + 20, y0 + card.height - 31, x0 + 30, y0 + card.height - 21), fill=(240, 60, 70))
        d.text((x0 + 38, y0 + card.height - 37), "HUBIE", font=self.f_chip_v, fill=WHITE)

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
                    self.game_img = g.resize((GAME_W, GAME_H), Image.BILINEAR)
                except Exception:
                    pass
                self.game_jpeg, self._dirty = None, True
            pal = self._theme.palette(now)
            if pal["cache"] != self.pal["cache"]:
                self.pal, self._dirty = pal, True
            if self._dirty or self._base is None:
                self._base, self._dirty = self._build_base(), False
            frame = self._base.copy()
            self._draw_clock(frame, now)
            self.draw_hubie(frame, now)
            self.popout.draw(frame, now)
            if self.confetti:
                self._draw_confetti(frame, now)
        return frame.tobytes()
