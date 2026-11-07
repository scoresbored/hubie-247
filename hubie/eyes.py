"""Moves Hubie's pupils so he can glance at the game or look at the camera.

Each pose has its eye openings described as rotated ellipses (in 512px art coordinates) plus where
the iris sits. look() repaints the inside of each eye opening with eye-white, then puts the iris
back shifted by (dx, dy), clipped to the opening, so the eyelids and outlines never move.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# (eye opening centre, (rx, ry), angle in degrees, iris centre, iris radius)
EYES = {
    "A": [((194, 294), (22, 9), -22, (195, 295), 11), ((330, 238), (22, 8), -12, (327, 238), 10)],
    "B": [((191, 283), (24, 19), -8, (190, 281), 19), ((332, 232), (24, 19), -6, (331, 231), 19)],
}
# where to look, per pose, in 512px art pixels: "game" = down-left toward the game, "cam" = at viewers
# Pose A's half-lidded eyes are mostly lash and iris, so it shows where it looks with a head lean instead.
LOOK = {"B": {"game": (-5, 3), "cam": (4, 2)}}


def _ellipse_mask(size, c, r, ang, scale=4):
    W, H = size
    m = Image.new("L", (W * scale, H * scale), 0)
    d = ImageDraw.Draw(m)
    d.ellipse(((c[0] - r[0]) * scale, (c[1] - r[1]) * scale, (c[0] + r[0]) * scale, (c[1] + r[1]) * scale), fill=255)
    m = m.rotate(-ang, center=(c[0] * scale, c[1] * scale), resample=Image.BICUBIC)
    return m.resize(size, Image.LANCZOS)


def look(img, pose, dx, dy):
    """img: 512x512 RGBA frame of that pose. Returns a copy with the pupils moved by (dx, dy)."""
    if not (dx or dy):
        return img
    out = img.copy()
    a = np.asarray(img).astype(np.float32)
    for c, r, ang, ic, ir in EYES[pose]:
        opening = _ellipse_mask(img.size, c, r, ang)
        op = np.asarray(opening).astype(np.float32) / 255
        # eye-white colour: the brightest pixels inside the opening
        lum = a[..., :3].mean(2)
        inside = (op > 0.6) & (lum > 150)
        white = a[inside][:, :3].mean(0) if inside.sum() > 5 else np.array([235, 228, 215])
        layer = np.zeros_like(a)
        layer[..., :3] = white
        layer[..., 3] = 255
        # iris: a soft disc around the iris centre, only the part inside the opening, then shifted
        disc = Image.new("L", img.size, 0)
        ImageDraw.Draw(disc).ellipse((ic[0] - ir, ic[1] - ir, ic[0] + ir, ic[1] + ir), fill=255)
        disc = np.asarray(disc.filter(ImageFilter.GaussianBlur(1))).astype(np.float32) / 255 * op
        iris = np.roll(np.roll(a, dy, 0), dx, 1)
        dmask = np.roll(np.roll(disc, dy, 0), dx, 1)[..., None]
        eye = layer * (1 - dmask) + iris * dmask
        o = np.asarray(out).astype(np.float32)
        o = o * (1 - op[..., None]) + eye * op[..., None]
        out = Image.fromarray(o.clip(0, 255).astype(np.uint8), "RGBA")
    return out
