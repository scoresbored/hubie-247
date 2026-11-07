"""Animated Hubie: expression frames + procedural motion, drawn fresh every video frame.

Frames live in art/hubie/*.png (cut out of green-screen renders of the official-style art):
  idle, talk_half, talk_open   same pose, only the mouth differs (used for lip sync)
  shocked, cheering, facepalm, thinking, laughing, sleepy, happy   reaction faces
  blink.png                    eyelids that sit on top of the idle/talk frames

The director calls react("cheering", 3) etc. Everything else (breathing, sway, blinking,
the mouth following the voice, little idle glances and yawns) happens here by itself.
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ART = Path(__file__).resolve().parent.parent / "art" / "hubie"
MOODS = ["shocked", "cheering", "facepalm", "thinking", "laughing", "sleepy", "happy"]
TALK = ["idle", "talk_half", "talk_open"]
FADE = 0.14            # seconds of crossfade between faces
MIN_HOLD = 1.2         # a fresh reaction stays at least this long even if Hubie starts talking

# per-mood motion: (bob amplitude px, bob speed, sway degrees, sway speed, style)
MOTION = {
    "idle":     (3, 1.4, 1.6, 0.45, "breathe"),
    "happy":    (4, 1.8, 2.2, 0.7, "breathe"),
    "thinking": (2, 1.0, 3.0, 0.35, "tilt"),
    "sleepy":   (2, 0.7, 3.5, 0.3, "droop"),
    "facepalm": (2, 0.9, 1.0, 0.4, "droop"),
    "laughing": (5, 9.0, 2.5, 4.5, "jiggle"),
    "cheering": (10, 5.5, 2.0, 2.0, "bounce"),
    "shocked":  (6, 0.0, 0.0, 0.0, "jolt"),
}


def available():
    return all((ART / f"{n}.png").exists() for n in TALK + MOODS)


class Avatar:
    def __init__(self, size=300, bg=(20, 45, 60)):
        self.size = size
        self.frames = {n: Image.open(ART / f"{n}.png").convert("RGBA").resize((size, size), Image.LANCZOS)
                       for n in TALK + MOODS}
        blink = ART / "blink.png"
        self.blink = Image.open(blink).convert("RGBA").resize((size, size), Image.LANCZOS) if blink.exists() else None
        self.card = self._make_card(bg)
        self.mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, size - 1, size - 1), 28, fill=255)
        self.mood, self.mood_t0, self.mood_until = "idle", 0.0, 0.0
        self.queue = []                     # chained reactions: [(mood, secs)]
        self.shown, self.prev, self.fade_t0 = "idle", None, 0.0
        self.talk_level = 0.0
        self.talk_frame = "idle"
        self.next_blink, self.blink_until, self.double = 0.0, 0.0, False
        self.next_glance = 0.0
        self.next_yawn = 0.0
        self.last_voice = 0.0
        self._last = None

    def _make_card(self, bg):
        s = self.size
        card = Image.new("RGBA", (s, s), bg + (255,))
        d = ImageDraw.Draw(card)
        for i in range(s):                  # soft vertical gradient behind him
            k = i / s
            d.line((0, i, s, i), fill=(int(bg[0] * (1.25 - 0.5 * k)), int(bg[1] * (1.25 - 0.5 * k)),
                                       int(bg[2] * (1.25 - 0.5 * k)), 255))
        glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))     # soft spotlight behind his head
        ImageDraw.Draw(glow).ellipse((s * 0.12, s * 0.08, s * 0.88, s * 0.84), fill=(110, 225, 225, 38))
        card.alpha_composite(glow.filter(ImageFilter.GaussianBlur(s * 0.08)))
        return card

    # ---------- control ----------
    def react(self, mood, secs=2.5, then=None, soft=False, now=0.0):
        """Show a reaction face. soft=True only applies if no other reaction is running."""
        if mood not in self.frames:
            return
        if soft and now < self.mood_until:
            return
        self.mood, self.mood_t0, self.mood_until = mood, now, now + secs
        self.queue = list(then or [])

    # ---------- per frame ----------
    def _pick(self, now, level):
        if level > 0.06:
            self.last_voice = now
        speaking = now - self.last_voice < 0.35

        if now >= self.mood_until:
            if self.queue:
                m, secs = self.queue.pop(0)
                self.mood, self.mood_t0, self.mood_until = m, now, now + secs
            else:
                self.mood = "idle"
        # little life when nothing is going on
        if self.mood == "idle" and not speaking:
            if not self.next_glance:
                self.next_glance = now + random.uniform(25, 70)
                self.next_yawn = now + random.uniform(600, 1200)
            if now > self.next_yawn:
                self.react("sleepy", 2.6, now=now)
                self.next_yawn = now + random.uniform(600, 1200)
            elif now > self.next_glance:
                self.react(random.choice(["happy", "happy", "thinking"]), random.uniform(1.8, 3.0), now=now)
                self.next_glance = now + random.uniform(30, 90)

        if self.mood != "idle" and not (speaking and now - self.mood_t0 > MIN_HOLD):
            return self.mood, self.mood
        # talking (or resting): idle body with the mouth following the voice
        self.talk_level += (level - self.talk_level) * (0.6 if level > self.talk_level else 0.35)
        lv = self.talk_level
        cur = self.talk_frame                 # hysteresis so the mouth doesn't flicker
        if cur == "talk_open":
            nxt = "talk_open" if lv > 0.30 else ("talk_half" if lv > 0.08 else "idle")
        elif cur == "talk_half":
            nxt = "talk_open" if lv > 0.40 else ("talk_half" if lv > 0.06 else "idle")
        else:
            nxt = "talk_open" if lv > 0.45 else ("talk_half" if lv > 0.12 else "idle")
        self.talk_frame = nxt
        return nxt, "idle"

    def render(self, now, level):
        """Returns the finished RGBA card (size x size) for this video frame."""
        key, motion = self._pick(now, level)
        img = self.frames[key]
        # blink on the neutral faces
        if self.blink is not None and key in TALK:
            if now > self.next_blink:
                self.blink_until = now + 0.13
                self.double = random.random() < 0.15
                self.next_blink = now + (0.32 if self.double else random.uniform(2.5, 6.0))
            if now < self.blink_until:
                img = Image.alpha_composite(img, self.blink)
        # crossfade when the face changes (mouth flaps cut instantly, faces blend)
        group = "talk" if key in TALK else key
        if group != self.shown:
            self.prev, self.fade_t0, self.shown = self._last, now, group
        if self.prev is not None and now - self.fade_t0 < FADE:
            img = Image.blend(self.prev, img, (now - self.fade_t0) / FADE)
        self._last = img

        # body motion
        amp, spd, sway, sspd, style = MOTION.get(motion, MOTION["idle"])
        t, age = now, now - self.mood_t0
        dy, ang, sc = 0.0, 0.0, 1.0
        if style == "breathe":
            dy = math.sin(t * spd) * amp
            sc = 1.0 + 0.012 * math.sin(t * spd)
            ang = math.sin(t * sspd) * sway
        elif style == "tilt":
            ang = sway * min(1, age / 0.5) + math.sin(t * sspd) * 0.6
            dy = math.sin(t * spd) * amp
        elif style == "droop":
            ang = -sway * min(1, age / 0.8) + math.sin(t * sspd) * 0.5
            dy = 4 * min(1, age / 0.8) + math.sin(t * spd) * amp
        elif style == "jiggle":
            dy = -abs(math.sin(t * spd)) * amp
            ang = math.sin(t * sspd) * sway
        elif style == "bounce":
            dy = -abs(math.sin(t * spd)) * amp
            ang = math.sin(t * sspd) * sway
            sc = 1.0 + 0.03 * abs(math.sin(t * spd))
        elif style == "jolt":
            k = math.exp(-age * 5)
            dy = -amp * k * 2
            sc = 1.0 + 0.08 * k
            ang = math.sin(age * 40) * 2.5 * k
        # speaking adds a small nod with the words
        if key in TALK:
            dy += self.talk_level * 4
        # pop when a new reaction lands
        if motion != "idle" and age < 0.25:
            sc *= 1.0 + 0.05 * (1 - age / 0.25)

        s = self.size
        px, py = s / 2, s * 0.98            # pivot near the bottom: he sways from the body
        a = math.radians(ang)
        ca, sa = math.cos(a) / sc, math.sin(a) / sc
        # inverse affine (output -> input) for rotate+scale about pivot, then shift by dy
        ox, oy = px, py + dy
        coeffs = (ca, sa, px - ca * ox - sa * oy,
                  -sa, ca, py + sa * ox - ca * oy)
        moved = img.transform((s, s), Image.AFFINE, coeffs, resample=Image.BILINEAR)
        out = self.card.copy()
        out.alpha_composite(moved)
        out.putalpha(self.mask)
        return out
