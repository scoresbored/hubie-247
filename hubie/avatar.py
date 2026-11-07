"""Animated Hubie: expression frames + procedural motion, drawn fresh every video frame.

Frames live in art/hubie/*.png (cut out of green-screen renders of the official-style art):
  idle, talk_half, talk_open   pose A (smug, paw on cheek): mouth closed / half / open, for lip sync
  idle2, talk2_1..3            pose B (wide-eyed, paws on bowtie): four mouth steps, closed to wide
                               each new line of speech picks a pose, so he doesn't look the same every time
  shocked, cheering, facepalm, thinking, laughing, sleepy, happy   reaction faces
  blink.png, blink2.png         eyelids for pose A and pose B

The director calls react("cheering", 3) etc. Everything else (breathing, sway, blinking,
the mouth following the voice, little idle glances and yawns) happens here by itself.
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ART = Path(__file__).resolve().parent.parent / "art" / "hubie"
MOODS = ["shocked", "cheering", "facepalm", "thinking", "laughing", "sleepy", "happy"]
# talking poses: frames from mouth closed to widest, the blink overlay, and when to step the mouth
# up (louder than) / down (quieter than) for each step
POSES = {
    "A": dict(frames=["idle", "talk_half", "talk_open"], blink="blink.png",
              up=[0.12, 0.45], down=[0.06, 0.30]),
    "B": dict(frames=["idle2", "talk2_1", "talk2_2", "talk2_3"], blink="blink2.png",
              up=[0.10, 0.28, 0.50], down=[0.05, 0.20, 0.38]),
}
TALK = POSES["A"]["frames"]
FADE = 0.14            # seconds of crossfade between faces
BODY = 0.86           # Hubie's size inside his card (1.0 = fills it)
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


def _poses():
    """Poses whose frames are all present (pose A is required, the others are optional)."""
    return {k: v for k, v in POSES.items() if all((ART / f"{n}.png").exists() for n in v["frames"])}


class Avatar:
    def __init__(self, size=300, bg=(20, 45, 60)):
        self.size = size
        load = lambda n: Image.open(ART / n).convert("RGBA").resize((size, size), Image.LANCZOS)
        self.poses = _poses()
        self.frames = {n: load(f"{n}.png") for n in MOODS}
        self.blinks = {}
        self.pose_of = {}                   # frame name -> pose key
        from . import eyes
        self.gaze_frames = {}               # (frame, "game"/"cam", step 1..3) -> image with moved pupils
        for k, p in self.poses.items():
            for n in p["frames"]:
                self.frames[n] = load(f"{n}.png")
                self.pose_of[n] = k
                if k in eyes.LOOK:
                    full = Image.open(ART / f"{n}.png").convert("RGBA")
                    for g, (dx, dy) in eyes.LOOK[k].items():
                        for st in (1, 2, 3):
                            im = eyes.look(full, k, round(dx * st / 3), round(dy * st / 3))
                            self.gaze_frames[(n, g, st)] = im.resize((size, size), Image.LANCZOS)
            if (ART / p["blink"]).exists():
                self.blinks[k] = load(p["blink"])
        self.pose = "A"
        self.talk_step = 0
        self.next_pose_swap = 0.0
        from .theme import Theme
        self.theme = Theme(size)            # seasonal / day-night background behind him
        self.mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, size - 1, size - 1), 28, fill=255)
        self.mood, self.mood_t0, self.mood_until = "idle", 0.0, 0.0
        self.queue = []                     # chained reactions: [(mood, secs)]
        self.shown, self.prev, self.fade_t0 = "idle", None, 0.0
        self.talk_level = 0.0
        self.next_blink, self.blink_until, self.double = 0.0, 0.0, False
        self.next_glance = 0.0
        self.next_yawn = 0.0
        self.last_voice = 0.0
        self.was_quiet_since = 1.0          # when the current pause started (0 while talking)
        self._last = None
        self._last_key = None
        self.k = BODY
        # where he's looking: -1 = at the game, 0 = straight, +1 = at viewers (eased each frame)
        self.gaze, self.gaze_target, self.gaze_hold = 0.0, -1.0, 0.0
        self.peek_until = 0.0               # quick glance at the game while talking
        # lip sync: loudness auto-gain, when the mouth last changed, previous mouth image for blending
        self.peak = 0.3
        self.step_t = 0.0
        self.mouth_prev = None
        # rain cloud over his head on losing streaks
        self.rain_t0, self.rain_until = 0.0, 0.0

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

    def on_round(self, now):
        """A round just landed: if he's mid-sentence, he flicks his eyes to the game for a moment."""
        if now - self.last_voice < 0.35:
            self.peek_until = now + 0.7

    def rain(self, secs, now):
        self.rain_t0, self.rain_until = now, now + secs

    def _update_gaze(self, now, speaking, reacting):
        if reacting:
            target = 0.0                                   # reaction faces keep their own eyes
        elif speaking:
            target = -1.0 if now < self.peek_until else 1.0
        else:                                              # watching the game, now and then checks on chat
            if now > self.gaze_hold:
                self.gaze_target = 1.0 if self.gaze_target < 0 and random.random() < 0.6 else -1.0
                self.gaze_hold = now + (random.uniform(2, 4) if self.gaze_target > 0 else random.uniform(6, 16))
            target = self.gaze_target
        step = 1 / 30 / 0.16                               # ~0.16s to sweep across
        self.gaze += max(-step, min(step, target - self.gaze))

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
        if self.mood == "idle" and not speaking and getattr(self, "idle_moods", True):
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
        # a new line of speech after a pause: maybe switch pose, so he doesn't look identical every time
        if speaking and self.was_quiet_since and now - self.was_quiet_since > 1.5:
            if len(self.poses) > 1 and random.random() < 0.5:
                self.pose = random.choice([k for k in self.poses if k != self.pose])
            self.was_quiet_since = 0.0
        if not speaking and not self.was_quiet_since:
            self.was_quiet_since = now
        if not speaking and now > self.next_pose_swap and len(self.poses) > 1:   # rare idle shuffle
            if self.next_pose_swap:
                self.pose = random.choice([k for k in self.poses if k != self.pose])
            self.next_pose_swap = now + random.uniform(60, 150)
        # talking (or resting): the pose's frames, mouth following the voice
        if level > 0.02:                                  # auto-gain: quiet lines still open the mouth fully
            self.peak = max(level, self.peak * 0.997)
        lv_in = min(1.0, level / max(0.22, self.peak) * 0.85)
        self.talk_level += (lv_in - self.talk_level) * (0.65 if lv_in > self.talk_level else 0.4)
        lv, P = self.talk_level, self.poses[self.pose]
        top = len(P["frames"]) - 1
        if len(P["frames"]) >= 4 and not getattr(self, "happy", False):
            top -= 1                                      # the big open grin is saved for happy lines
        cur = min(self.talk_step, top)
        want = cur
        while want < min(len(P["up"]), top) and lv > P["up"][want]:
            want += 1
        while want > 0 and lv < P["down"][want - 1]:
            want -= 1
        if want != cur and now - self.step_t >= 0.055:    # hold each mouth shape ~2 frames (no flicker)
            self.talk_step = want if want > cur else max(want, cur - 1)   # open fast, close a step at a time
            self.step_t = now
        else:
            self.talk_step = cur
        return P["frames"][self.talk_step], "idle"

    def _draw_rain(self, img, now):
        """A little grey cloud over his head with rain falling, fading in and out."""
        s = self.size
        fade = min(1.0, (now - self.rain_t0) / 0.4, max(0.0, (self.rain_until + 0.5 - now) / 0.5))
        if fade <= 0:
            return
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        cx = s * 0.42 + math.sin(now * 0.8) * 6
        cy = s * 0.14 + math.sin(now * 1.3) * 2
        for i in range(24):                                 # rain drops below the cloud
            r = random.Random(i)
            x = cx - s * 0.2 + r.random() * s * 0.4
            y = cy + 10 + ((now * (140 + 60 * r.random()) + r.random() * 200) % (s * 0.55))
            d.line((x, y, x - 2, y + 9), fill=(150, 190, 235, int(210 * fade)), width=2)
        grey = (120, 128, 142, int(245 * fade))
        for dx, dy, rr in [(-38, 8, 22), (-12, -6, 28), (18, 0, 25), (42, 10, 19), (0, 14, 24)]:
            d.ellipse((cx + dx - rr, cy + dy - rr, cx + dx + rr, cy + dy + rr), fill=grey)
        d.ellipse((cx - 30, cy - 22, cx + 4, cy - 2), fill=(170, 176, 188, int(200 * fade)))
        img.alpha_composite(layer)

    def render(self, now, level):
        """Returns the finished RGBA card (size x size) for this video frame."""
        key, motion = self._pick(now, level)
        speaking = now - self.last_voice < 0.35
        self._update_gaze(now, speaking, key not in self.pose_of)
        img = self.frames[key]
        g = self.gaze
        if key in self.pose_of and abs(g) > 0.05:
            st = max(1, min(3, round(abs(g) * 3)))
            img = self.gaze_frames.get((key, "game" if g < 0 else "cam", st), img)
        if key in self.pose_of:                            # blend each mouth change over one frame
            prev_key = self._last_key
            if (self.mouth_prev is not None and prev_key != key and prev_key in self.pose_of
                    and self.pose_of[prev_key] == self.pose_of[key]):
                cur = img
                img = Image.blend(self.mouth_prev, cur, 0.55)
                self.mouth_prev = cur
            else:
                self.mouth_prev = img
        self._last_key = key
        # blink on the neutral faces
        blink = self.blinks.get(self.pose_of.get(key))
        if blink is not None:
            if now > self.next_blink:
                self.blink_until = now + 0.13
                self.double = random.random() < 0.15
                self.next_blink = now + (0.32 if self.double else random.uniform(2.5, 6.0))
            if now < self.blink_until:
                img = Image.alpha_composite(img, blink)
        # crossfade when the face changes (mouth flaps cut instantly, faces blend)
        group = ("talk" + self.pose_of[key]) if key in self.pose_of else key
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
        # leaning toward what he's looking at (the game is to his left); stronger for the half-lidded pose
        if key in self.pose_of:
            ang += self.gaze * (2.8 if self.pose_of[key] == "A" else 1.2)
        # speaking adds a small nod with the words
        if key in self.pose_of:
            dy += self.talk_level * 4
        # pop when a new reaction lands
        if motion != "idle" and age < 0.25:
            sc *= 1.0 + 0.05 * (1 - age / 0.25)

        s = self.size
        # body size: a bit smaller than the card so the seasonal sky shows, full size when cheering
        target_k = 1.0 if motion == "cheering" else BODY
        self.k += (target_k - self.k) * 0.18
        k = self.k
        ax, ay = s * 0.995, s * 0.995       # shrink toward the bottom-right (where the art meets the edge)
        cx, cy = s * 0.55, s * 0.98         # sway/breathe pivot: bottom of his body
        a = math.radians(ang)
        cos, sin = math.cos(a) * sc, math.sin(a) * sc
        # forward map: p1 = A + k(p - A);  p2 = C + sc*R(a)(p1 - C) + (0, dy)
        m00, m01, m10, m11 = cos * k, -sin * k, sin * k, cos * k
        tx = cx + cos * (ax - k * ax - cx) - sin * (ay - k * ay - cy)
        ty = cy + dy + sin * (ax - k * ax - cx) + cos * (ay - k * ay - cy)
        det = m00 * m11 - m01 * m10         # PIL wants the inverse (output -> input)
        i00, i01, i10, i11 = m11 / det, -m01 / det, -m10 / det, m00 / det
        coeffs = (i00, i01, -(i00 * tx + i01 * ty), i10, i11, -(i10 * tx + i11 * ty))
        moved = img.transform((s, s), Image.AFFINE, coeffs, resample=Image.BILINEAR)
        out = self.theme.background(now)
        out.alpha_composite(moved)
        if now < self.rain_until + 0.5:
            self._draw_rain(out, now)
        out.putalpha(self.mask)
        return out
