"""Cuts full-body Hubie poses out of green-screen renders for the pop-out moves.

Only green that is connected to the picture's edge is removed (a flood fill), so green things he's
holding, like the snack leaf, survive. Edge pixels get a soft alpha and the green spill is pulled out.
Usage: python tools/cutout_body.py <image> <pose> [crop x0 y0 x1 y1] [--scale S]
"""
import sys

import numpy as np
from PIL import Image
from scipy import ndimage


def cutout(img):
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    spill = g - np.maximum(r, b)
    # screen green: strongly green and "neutral" (blue about as strong as red). Leaves and other props he
    # holds are yellow-green (much more red than blue), so they never count as screen.
    # (video renders come out as a very bright, almost blue-free green like (12, 250, 4): that counts too)
    screen = (spill > 45) & ((b >= 0.7 * r) | ((g > 180) & (r < 70)))
    lab, _ = ndimage.label(screen)
    edge_labels = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(edge_labels))
    # pockets of screen trapped between arms, hat brims etc.: same colour, not touching the edge
    sizes = ndimage.sum(np.ones_like(spill), lab, range(1, lab.max() + 1))
    for i, n in enumerate(sizes, 1):
        if i not in edge_labels and n > 30 and np.median(spill[lab == i]) > 70:
            bg |= lab == i
    band = ndimage.binary_dilation(bg, iterations=3) & ~bg     # soft edge around the background
    alpha = np.ones(bg.shape, np.float32)
    alpha[bg] = 0
    alpha[band] = np.clip(1 - (spill[band] - 25) / 70, 0, 1)
    g2 = np.where(band | bg, np.minimum(g, np.maximum(r, b) + 12), g)   # de-spill the edges only
    out = np.dstack([r, g2, b, alpha * 255]).clip(0, 255).astype(np.uint8)
    im = Image.fromarray(out, "RGBA")
    bb = im.split()[3].point(lambda v: 255 if v > 60 else 0).getbbox()
    return im.crop((max(0, bb[0] - 4), max(0, bb[1] - 4), min(im.width, bb[2] + 4), min(im.height, bb[3] + 4)))
