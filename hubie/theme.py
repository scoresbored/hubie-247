"""Hubie's background card: the sky follows the UTC clock and every month has its own theme.

Sky:     dawn -> day -> dusk -> night, blended smoothly by the UTC hour.
Themes:  Jan new year (confetti, fireworks at night), Feb love (hearts), Mar lucky (clovers),
         Apr spring (blossom petals), May bloom (flower petals), Jun-Aug summer (sun rays,
         fireflies at night), Sep harvest (golden leaves), Oct spooky (bats, orange moon),
         Nov fall (orange and red leaves), Dec holidays (snow + twinkling string lights).
Every theme also gets stars and a moon at night and a sun glow by day.

Particles are a pure function of time (no state), so they never drift or pile up over a long run.
Set HUBIE_THEME=dec (or jan..nov) and/or HUBIE_HOUR=21 to force a look for testing.
"""
import math
import os
import random
import time

from PIL import Image, ImageDraw, ImageFilter

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

# sky colours (top, bottom) at UTC hour keyframes; blended in between
SKY = [
    (0,  (8, 12, 34),    (22, 30, 64)),      # night
    (5,  (10, 14, 40),   (30, 34, 72)),
    (6,  (70, 60, 120),  (240, 150, 110)),   # dawn
    (8,  (90, 160, 220), (190, 225, 240)),   # morning
    (13, (70, 150, 225), (175, 220, 245)),   # day
    (17, (80, 120, 200), (230, 190, 150)),
    (19, (70, 50, 110),  (245, 130, 90)),    # dusk
    (21, (16, 18, 48),   (40, 40, 84)),
    (24, (8, 12, 34),    (22, 30, 64)),
]

THEMES = {
    "jan": dict(name="New Year", tint=(40, 30, 10), glow=(255, 215, 120), parts="confetti", n=26, night_extra="fireworks"),
    "feb": dict(name="Love", tint=(60, 0, 30), glow=(255, 120, 170), parts="hearts", n=14),
    "mar": dict(name="Lucky", tint=(0, 35, 10), glow=(120, 230, 140), parts="clovers", n=12),
    "apr": dict(name="Spring", tint=(20, 10, 25), glow=(255, 190, 220), parts="petals", n=22),
    "may": dict(name="Bloom", tint=(15, 25, 5), glow=(255, 230, 140), parts="flowers", n=14),
    "jun": dict(name="Summer", tint=(25, 15, 0), glow=(255, 220, 120), parts="rays", n=0, night_extra="fireflies"),
    "jul": dict(name="Summer", tint=(25, 15, 0), glow=(255, 220, 120), parts="rays", n=0, night_extra="fireflies"),
    "aug": dict(name="Summer", tint=(30, 15, 0), glow=(255, 210, 110), parts="rays", n=0, night_extra="fireflies"),
    "sep": dict(name="Harvest", tint=(30, 20, 0), glow=(255, 200, 100), parts="leaves_gold", n=12),
    "oct": dict(name="Spooky", tint=(30, 5, 35), glow=(255, 140, 40), parts="bats", n=7, moon=(255, 150, 60)),
    "nov": dict(name="Fall", tint=(35, 12, 0), glow=(255, 160, 80), parts="leaves", n=18),
    "dec": dict(name="Holidays", tint=(5, 15, 25), glow=(200, 230, 255), parts="snow", n=40, lights=True),
}

LEAF_COLS = [(214, 92, 34), (186, 52, 32), (232, 150, 40), (150, 70, 30), (240, 180, 60)]
GOLD_COLS = [(240, 190, 60), (220, 160, 40), (200, 140, 50), (245, 210, 110)]
CONFETTI = [(255, 215, 90), (230, 230, 240), (255, 120, 150), (120, 200, 255), (180, 140, 255)]
LIGHTS = [(255, 70, 70), (80, 220, 110), (255, 210, 70), (90, 160, 255)]


def _lerp(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


def utc_hour(now):
    forced = os.environ.get("HUBIE_HOUR")
    if forced:
        return float(forced)
    t = time.gmtime(now)
    return t.tm_hour + t.tm_min / 60


def theme_key(now):
    forced = os.environ.get("HUBIE_THEME", "").lower()[:3]
    if forced in THEMES:
        return forced
    return MONTHS[time.gmtime(now).tm_mon - 1]


def sky(hour):
    for (h0, t0, b0), (h1, t1, b1) in zip(SKY, SKY[1:]):
        if h0 <= hour <= h1:
            k = (hour - h0) / max(1e-6, h1 - h0)
            return _lerp(t0, t1, k), _lerp(b0, b1, k)
    return SKY[0][1], SKY[0][2]


def darkness(hour):
    """0 = full day, 1 = full night."""
    if 8 <= hour <= 17:
        return 0.0
    if hour >= 21 or hour <= 5:
        return 1.0
    if 5 < hour < 8:
        return 1 - (hour - 5) / 3
    return (hour - 17) / 4


def _rot(points, cx, cy, a):
    c, s = math.cos(a), math.sin(a)
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in points]


HEART = [(math.sin(t) ** 3 * 16, -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)))
         for t in [i * 2 * math.pi / 24 for i in range(24)]]
LEAF = [(math.cos(t) * 10, math.sin(t) * 5 * (1 - 0.35 * math.cos(t))) for t in [i * 2 * math.pi / 14 for i in range(14)]]
BAT = [(-14, 0), (-9, -5), (-6, -1), (-3, -4), (0, 0), (3, -4), (6, -1), (9, -5), (14, 0), (7, 3), (3, 1), (0, 4),
       (-3, 1), (-7, 3)]


class Theme:
    def __init__(self, size):
        self.size = size
        self._key = None
        self._static = None
        self.rng_seeds = [random.Random(i) for i in range(64)]
        self.p = [(r.random(), r.random(), r.random(), r.random(), r.random()) for r in self.rng_seeds]

    # ---------- static part: sky, tint, sun/moon, spotlight (rebuilt once a minute) ----------
    def _build_static(self, key, hour):
        s, th = self.size, THEMES[key]
        top, bot = sky(hour)
        dk = darkness(hour)
        img = Image.new("RGBA", (s, s))
        d = ImageDraw.Draw(img)
        for y in range(s):
            c = _lerp(top, bot, y / s)
            c = tuple(min(255, v + int(t * (0.6 + 0.4 * dk))) for v, t in zip(c, th["tint"]))
            d.line((0, y, s, y), fill=c + (255,))
        glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        mc = th.get("moon", (245, 240, 220))
        if dk > 0.5:                                    # moon halo (the moon itself is drawn sharp below)
            g.ellipse((s * 0.68, s * 0.02, s * 0.98, s * 0.32), fill=mc + (int(70 * dk),))
        elif dk < 0.5:                                  # sun glow, top-right
            g.ellipse((s * 0.62, s * -0.12, s * 1.05, s * 0.30), fill=(255, 240, 190, int(170 * (1 - dk))))
        # themed spotlight behind his head
        g.ellipse((s * 0.12, s * 0.10, s * 0.88, s * 0.86), fill=th["glow"] + (45,))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(s * 0.06)))
        if dk > 0.5:
            d = ImageDraw.Draw(img, "RGBA")
            al = int(255 * min(1, (dk - 0.5) * 2))
            d.ellipse((s * 0.76, s * 0.08, s * 0.90, s * 0.22), fill=mc + (al,))
        return img

    def background(self, now):
        hour = utc_hour(now)
        key = theme_key(now)
        cache = (key, int(hour * 12))                  # every 5 minutes
        if cache != self._key:
            self._static, self._key = self._build_static(key, hour), cache
        img = self._static.copy()
        self._particles(img, key, hour, now)
        return img

    # ---------- animated part ----------
    def _particles(self, img, key, hour, t):
        s, th = self.size, THEMES[key]
        d = ImageDraw.Draw(img, "RGBA")
        dk = darkness(hour)
        if dk > 0.3:                                    # twinkling stars
            for i, (a, b, c, e, f) in enumerate(self.p[:22]):
                tw = 0.5 + 0.5 * math.sin(t * (1 + 2 * c) + 6 * e)
                al = int(220 * dk * tw)
                x, y = a * s, b * s * 0.6
                r = 1 + (f > 0.8)
                d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 230, al))
        kind = th["parts"]
        n = th["n"]
        for i in range(n):
            a, b, c, e, f = self.p[i]
            if kind in ("leaves", "leaves_gold", "petals", "snow", "confetti", "clovers", "flowers"):
                speed = {"snow": 18, "confetti": 30, "petals": 22}.get(kind, 26) * (0.6 + 0.8 * c)
                y = (b * (s + 40) + t * speed) % (s + 40) - 20
                x = a * s + math.sin(t * (0.6 + c) + e * 6) * (10 if kind != "snow" else 6)
                ang = t * (1 + 2 * f) + e * 6
                draw_motif(d, kind, x, y, ang, c, f)
            elif kind == "hearts":
                speed = 14 * (0.6 + 0.8 * c)
                y = s + 20 - (b * (s + 40) + t * speed) % (s + 40)
                x = a * s + math.sin(t * (0.8 + c) + e * 6) * 8
                draw_motif(d, "hearts", x, y, 0, c, f, scale=1 + 0.08 * math.sin(t * 4 + e * 6))
            elif kind == "bats":
                x = (a * (s + 60) + t * (12 + 14 * c)) % (s + 60) - 30
                y = s * (0.08 + 0.45 * b) + math.sin(t * 2 + e * 6) * 10
                flap = 0.5 + 0.5 * abs(math.sin(t * (9 + 4 * f) + e * 6))
                d.polygon([(x + px * 0.9, y + py * flap * 1.2) for px, py in BAT], fill=(20, 10, 25, 230))
        if kind == "rays" and dk < 0.7:                 # slow sun rays (own layer so they stay faint)
            rays = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            rd = ImageDraw.Draw(rays)
            cx, cy = s * 0.83, s * 0.06
            for k in range(9):
                ang = t * 0.08 + k * math.pi * 2 / 9
                rd.polygon([(cx, cy), (cx + math.cos(ang - 0.05) * s, cy + math.sin(ang - 0.05) * s),
                            (cx + math.cos(ang + 0.05) * s, cy + math.sin(ang + 0.05) * s)],
                           fill=(255, 245, 200, int(40 * (1 - dk))))
            img.alpha_composite(rays)
            d = ImageDraw.Draw(img, "RGBA")
        extra = th.get("night_extra")
        if extra == "fireflies" and dk > 0.4:
            for i in range(14):
                a, b, c, e, f = self.p[30 + i]
                x = a * s + math.sin(t * 0.5 + e * 6) * 18
                y = s * (0.15 + 0.8 * b) + math.cos(t * 0.4 + f * 6) * 14
                al = int(230 * dk * max(0, math.sin(t * (0.8 + c) + e * 6)))
                d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(220, 255, 120, al // 4))
                d.ellipse((x - 1.6, y - 1.6, x + 1.6, y + 1.6), fill=(240, 255, 160, al))
        if extra == "fireworks" and dk > 0.4:
            for slot in range(2):                       # a burst every ~4s in two spots
                period = 4.0 + slot * 1.3
                k = ((t + slot * 1.9) % period) / 1.6    # burst lasts 1.6s
                if k > 1:
                    continue
                seed = int((t + slot * 1.9) // period) * 7 + slot
                r = random.Random(seed)
                cx, cy = s * (0.15 + 0.7 * r.random()), s * (0.08 + 0.25 * r.random())
                col = CONFETTI[r.randrange(5)]
                rad = 8 + 40 * (1 - (1 - k) ** 2)
                al = int(255 * (1 - k) * dk)
                for j in range(14):
                    ang = j * math.pi * 2 / 14
                    px, py = cx + math.cos(ang) * rad, cy + math.sin(ang) * rad + 10 * k * k
                    d.ellipse((px - 1.8, py - 1.8, px + 1.8, py + 1.8), fill=col + (al,))
        if th.get("lights"):                             # string lights along the top edge
            string_lights(d, 0, s, 8, t, n=11)


# ---------- whole-overlay palettes ----------
# bg, panel, accent (titles, chat names, Hubie's frame), accent2 (HubieBot, highlights),
# dim (secondary text), line (dividers, empty bars), motif (faint pattern in the panels)
PALETTES = {
    "jan": dict(bg=(10, 10, 20), panel=(22, 22, 36), accent=(255, 212, 100), accent2=(205, 212, 235), dim=(165, 165, 185), line=(60, 58, 80), motif="confetti"),
    "feb": dict(bg=(24, 8, 18), panel=(44, 16, 34), accent=(255, 125, 170), accent2=(255, 190, 210), dim=(200, 155, 175), line=(95, 40, 70), motif="hearts"),
    "mar": dict(bg=(6, 22, 12), panel=(12, 40, 24), accent=(115, 225, 135), accent2=(245, 210, 95), dim=(150, 190, 160), line=(35, 85, 50), motif="clovers"),
    "apr": dict(bg=(22, 14, 26), panel=(38, 26, 44), accent=(255, 175, 212), accent2=(175, 225, 165), dim=(190, 165, 190), line=(80, 55, 90), motif="petals"),
    "may": dict(bg=(12, 22, 14), panel=(24, 40, 28), accent=(255, 220, 120), accent2=(200, 175, 255), dim=(165, 190, 165), line=(50, 85, 55), motif="flowers"),
    "jun": dict(bg=(6, 22, 32), panel=(12, 40, 56), accent=(255, 205, 85), accent2=(95, 215, 235), dim=(150, 185, 200), line=(30, 80, 100), motif="suns"),
    "jul": dict(bg=(6, 22, 32), panel=(12, 40, 56), accent=(255, 205, 85), accent2=(95, 215, 235), dim=(150, 185, 200), line=(30, 80, 100), motif="suns"),
    "aug": dict(bg=(8, 22, 30), panel=(14, 40, 52), accent=(255, 195, 80), accent2=(110, 210, 225), dim=(150, 185, 195), line=(30, 80, 95), motif="suns"),
    "sep": dict(bg=(24, 18, 8), panel=(42, 32, 14), accent=(242, 192, 72), accent2=(215, 125, 55), dim=(195, 175, 135), line=(90, 70, 35), motif="leaves_gold"),
    "oct": dict(bg=(16, 8, 22), panel=(30, 14, 40), accent=(255, 140, 40), accent2=(175, 115, 235), dim=(180, 155, 190), line=(70, 40, 85), motif="bats"),
    "nov": dict(bg=(24, 14, 10), panel=(42, 25, 17), accent=(255, 160, 72), accent2=(235, 112, 52), dim=(200, 168, 140), line=(95, 58, 36), motif="leaves"),
    "dec": dict(bg=(8, 20, 22), panel=(14, 38, 34), accent=(240, 80, 80), accent2=(110, 210, 130), dim=(165, 195, 185), line=(40, 85, 70), motif="snow"),
}


def palette(now):
    """Colours for the whole overlay right now (month theme, a little darker at night)."""
    key = theme_key(now)
    p = dict(PALETTES[key])
    dk = darkness(utc_hour(now))
    for k in ("bg", "panel", "line"):
        p[k] = tuple(int(v * (1.12 - 0.22 * dk)) for v in p[k])
    p["key"], p["name"], p["night"] = key, THEMES[key]["name"], dk > 0.5
    p["cache"] = (key, round(dk * 4))           # overlay is redrawn when this changes
    return p


def decorate(img, rect, pal, seed=1, n=10):
    """Faint seasonal pattern inside a panel rectangle (static, so it costs nothing per frame)."""
    x0, y0, x1, y1 = rect
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    r = random.Random(seed)
    kind = pal["motif"]
    for _ in range(n):
        x, y = r.random() * (x1 - x0), r.random() * (y1 - y0)
        ang, c, f = r.random() * 6.28, r.random(), r.random()
        draw_motif(d, kind, x, y, ang, c, f, alpha=1.0, scale=1.6 + c)
    a = layer.split()[3].point(lambda v: v * (0.24 if kind == "snow" else 0.16))    # keep it very faint behind text
    layer.putalpha(a)
    img.paste(layer, (x0, y0), layer)


def draw_motif(d, kind, x, y, ang, c, f, alpha=1.0, scale=1.0):
    A = lambda v: int(v * alpha)
    if kind == "snow":
        if scale <= 1.2:                             # falling snow: soft round flakes
            r = (1.5 + 2.5 * f) * scale
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, A(220)))
        else:                                        # panel decoration: a lacy six-arm snowflake
            R = (5 + 4 * f) * scale
            for k in range(6):
                a = ang + k * math.pi / 3
                ex, ey = x + math.cos(a) * R, y + math.sin(a) * R
                d.line((x, y, ex, ey), fill=(255, 255, 255, A(230)), width=2)
                for side in (-0.6, 0.6):             # little V branches on each arm
                    mx, my = x + math.cos(a) * R * 0.6, y + math.sin(a) * R * 0.6
                    d.line((mx, my, mx + math.cos(a + side) * R * 0.3, my + math.sin(a + side) * R * 0.3),
                           fill=(255, 255, 255, A(230)), width=1)
    elif kind in ("leaves", "leaves_gold"):
        cols = LEAF_COLS if kind == "leaves" else GOLD_COLS
        col = cols[int(f * len(cols)) % len(cols)]
        sc = (0.7 + 0.6 * c) * scale
        d.polygon(_rot([(px * sc, py * sc * max(0.3, abs(math.cos(ang * 0.7)))) for px, py in LEAF], x, y, ang),
                  fill=col + (A(235),))
    elif kind == "petals":
        col = (255, 190, 215) if f > 0.4 else (255, 240, 245)
        d.polygon(_rot([(px * 0.6 * scale, py * 0.9 * scale * max(0.3, abs(math.cos(ang)))) for px, py in LEAF], x, y, ang),
                  fill=col + (A(225),))
    elif kind == "confetti":
        col = CONFETTI[int(f * 4.99)]
        w = (3 * abs(math.cos(ang * 2)) + 0.5) * scale
        h = 2.5 * scale
        d.polygon(_rot([(-w, -h), (w, -h), (w, h), (-w, h)], x, y, ang), fill=col + (A(235),))
    elif kind == "clovers":
        for k in range(3):
            cx, cy = x + 4.5 * scale * math.cos(ang + k * 2.09), y + 4.5 * scale * math.sin(ang + k * 2.09)
            rr = 4 * scale
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=(70, 190, 90, A(215)))
    elif kind == "flowers":
        col = [(255, 170, 200), (255, 230, 120), (190, 160, 255), (255, 255, 255)][int(f * 3.99)]
        for k in range(5):
            cx, cy = x + 4 * scale * math.cos(ang + k * 1.257), y + 4 * scale * math.sin(ang + k * 1.257)
            rr = 3 * scale
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col + (A(225),))
        d.ellipse((x - 2 * scale, y - 2 * scale, x + 2 * scale, y + 2 * scale), fill=(255, 210, 80, A(240)))
    elif kind == "hearts":
        sc = (0.35 + 0.4 * f) * scale
        col = [(255, 90, 130), (255, 150, 180), (230, 60, 100)][int(f * 2.99)]
        d.polygon([(x + px * sc, y + py * sc) for px, py in HEART], fill=col + (A(215),))
    elif kind == "bats":
        d.polygon([(x + px * 0.9 * scale, y + py * 1.2 * scale) for px, py in BAT], fill=(140, 90, 170, A(230)))
    elif kind == "suns":
        rr = 5 * scale
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 215, 90, A(230)))
        for k in range(8):
            a = ang + k * math.pi / 4
            d.line((x + math.cos(a) * rr * 1.4, y + math.sin(a) * rr * 1.4, x + math.cos(a) * rr * 2.1,
                    y + math.sin(a) * rr * 2.1), fill=(255, 215, 90, A(230)), width=2)


def string_lights(d, x0, x1, y, t=0.0, n=None):
    """Holiday bulbs on a wire between x0 and x1."""
    n = n or max(4, int((x1 - x0) / 28))
    pts = [(x0 + (x1 - x0) * (k + 0.5) / n, y + 6 * math.sin(k * 0.9) ** 2) for k in range(n)]
    for a, b in zip(pts, pts[1:]):
        d.line((a, b), fill=(30, 60, 35), width=2)
    for k, (x, yy) in enumerate(pts):
        col = LIGHTS[k % 4]
        on = math.sin(t * 2.2 + k * 1.7) > -0.3
        d.ellipse((x - 7, yy - 3, x + 7, yy + 11), fill=col + (90 if on else 0,))
        d.ellipse((x - 3.5, yy, x + 3.5, yy + 8), fill=col + (255 if on else 120,))
