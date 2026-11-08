"""Make new full-body Hubie moves with Grok (xAI Imagine image editing).

  python tools/grok_pose.py new moonwalk "doing a smooth moonwalk, one foot sliding back, arms loose, cool smile"
      -> asks Grok for 3 versions using Hubie's existing poses as the character reference,
         cuts each out of the green screen, sizes it like the other moves, and writes a preview sheet:
         /opt/hubie/poses/moonwalk_preview.png  (versions numbered 1, 2, 3)

  python tools/grok_pose.py install moonwalk 2 "{u} asked for a moonwalk" [--mood happy] [--confetti]
      -> copies version 2 into art/hubie/body/moonwalk.png and registers the chat command !moonwalk
         (art/hubie/body/commands.json). Restart Hubie to load it.

  python tools/grok_pose.py animate dance "bouncy happy dance, stepping side to side, arms waving" [--secs 3]
      -> turns the existing dance pose into a short Grok video, cuts every frame out of the green screen and
         writes art/hubie/body/dance/frame_000.png ...; the pop-out then plays the frames instead of the still.
         A preview strip is written to /opt/hubie/poses/dance_anim_preview.png. Delete the folder to go back.

  python tools/grok_pose.py cam talk B      (or: cam idle A, ...)
      -> a short Grok video of Hubie's head-and-shoulders cam pose (A = smug, B = wide-eyed) talking or idling,
         keyed off the green screen into full 512x512 frames: art/hubie/cam/<A|B>_<talk|idle>/frame_000.png ...
         plus mouth.json (how open the mouth is in every frame) so his cam can lip-sync to the real voice.

Needs XAI_API_KEY in /opt/hubie/.env. Each version costs a few cents. Nothing here touches the live stream.
"""
import argparse
import subprocess
import tempfile
import time
import base64
import io
import json
import os
import sys
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "tools"))
from hubie import config  # noqa: E402,F401  (loads /opt/hubie/.env into the environment)
from cutout_body import cutout  # noqa: E402

BODY = APP / "art" / "hubie" / "body"
OUT = Path(os.environ.get("HUBIE_POSES_DIR", "/opt/hubie/poses"))
API = "https://api.x.ai/v1/images/edits"
MODEL = os.environ.get("XAI_IMAGE_MODEL", "grok-imagine-image-2.0")
REFS = ["dapper", "wave"]                 # existing poses sent as the character reference
TARGET_STANDING_H = 330                   # on-screen height of a standing Hubie (matches the other moves)

PROMPT = """<IMAGE_0> and <IMAGE_1> show the same character: a cartoon sloth gentleman. Draw this exact character
again in a new pose. Keep his face, fur colours and markings, black top hat with the gold H badge, navy tailcoat with
gold buttons, gold vest, white cuffs and black bow tie exactly the same, in the same polished 3D cartoon style.

Pose: {pose}

Framing: full body, the whole character visible including hat and feet, centred, the same size relative to the
canvas as in the references, standing on an invisible ground line near the bottom. Plain solid pure green
background (#00FF00) everywhere, no floor, no shadow, no text, no logos, no frame. Do not add money, coins, chips,
cards or casino items. Only add a prop if the pose above asks for one."""


def ref_data_uri(name):
    """An existing pose on a green background, as a PNG data URI."""
    im = Image.open(BODY / f"{name}.png").convert("RGBA")
    canvas = Image.new("RGBA", (768, 1024), (0, 255, 0, 255))
    s = 600 / im.height
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    canvas.alpha_composite(im, ((768 - im.width) // 2, 1024 - im.height - 140))
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def generate(name, pose, n):
    key = os.environ.get("XAI_API_KEY")
    if not key:
        sys.exit("XAI_API_KEY is missing: add it to /opt/hubie/.env")
    body = {"model": MODEL, "prompt": PROMPT.format(pose=pose), "n": n, "aspect_ratio": "3:4",
            "resolution": "1k", "response_format": "b64_json",
            "images": [{"url": ref_data_uri(r), "type": "image_url"} for r in REFS]}
    print(f"asking Grok for {n} version(s) of '{name}'...")
    r = httpx.post(API, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   json=body, timeout=180)
    if r.status_code != 200:
        sys.exit(f"xAI error {r.status_code}: {r.text[:400]}")
    j = r.json()
    cost = (j.get("usage") or {}).get("cost_in_usd_ticks")
    OUT.mkdir(parents=True, exist_ok=True)
    sprites = []
    for i, item in enumerate(j.get("data", []), 1):
        raw = Image.open(io.BytesIO(base64.b64decode(item["b64_json"]))).convert("RGB")
        raw.save(OUT / f"{name}_{i}_raw.png")
        cut = cutout(raw)
        # same framing as the references, so scale by canvas height (a standing pose ends up ~330px tall)
        s = TARGET_STANDING_H / (raw.height * 600 / 1024)
        cut = cut.resize((max(1, round(cut.width * s)), max(1, round(cut.height * s))), Image.LANCZOS)
        cut.save(OUT / f"{name}_{i}.png")
        sprites.append(cut)
    preview(name, sprites)
    if cost:
        print(f"cost: ${cost / 1e10:.3f}")
    print(f"preview: {OUT / (name + '_preview.png')}")


def preview(name, sprites):
    """Existing 'dapper' pose first (for size comparison), then the numbered new versions, on a dark backdrop."""
    ref = Image.open(BODY / "dapper.png").convert("RGBA")
    items = [("dapper (current)", ref)] + [(f"{name} #{i}", s) for i, s in enumerate(sprites, 1)]
    w = sum(im.width for _, im in items) + 40 * (len(items) + 1)
    h = max(im.height for _, im in items) + 70
    sheet = Image.new("RGBA", (w, h), (22, 26, 38, 255))
    d = ImageDraw.Draw(sheet)
    x = 40
    for label, im in items:
        sheet.alpha_composite(im, (x, h - 40 - im.height))
        d.text((x, h - 30), label, fill=(230, 230, 240))
        x += im.width + 40
    d.line((0, h - 40, w, h - 40), fill=(200, 80, 80))
    sheet.convert("RGB").save(OUT / f"{name}_preview.png")


VIDEO_API = "https://api.x.ai/v1/videos"
VIDEO_MODEL = os.environ.get("XAI_VIDEO_MODEL", "grok-imagine-video")
VIDEO_PROMPT = """Animate this cartoon sloth gentleman: {motion}. Keep him exactly the same character (face, top hat with
the gold H, navy tailcoat, gold vest, bow tie), same size, fully in frame, feet staying near the same spot.
The camera does not move. The background stays plain solid pure green (#00FF00) the whole time, no shadow,
no floor, no text, no new objects. Smooth, loopable cartoon motion that ends close to how it started."""
ANIM_FPS = 12


def animate(name, motion, secs, reuse=False):
    key = os.environ.get("XAI_API_KEY")
    if not key:
        sys.exit("XAI_API_KEY is missing: add it to /opt/hubie/.env")
    sprite = BODY / f"{name}.png"
    if not sprite.exists():
        sys.exit(f"{sprite} not found: animate an existing move (or make it with 'new' + 'install' first)")
    # start frame: the pose on green, framed like the references
    im = Image.open(sprite).convert("RGBA")
    canvas = Image.new("RGBA", (720, 960), (0, 255, 0, 255))
    s = 560 / im.height
    big = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    canvas.alpha_composite(big, ((720 - big.width) // 2, 960 - big.height - 150))
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    mp4 = OUT / f"{name}_anim.mp4"
    if reuse and mp4.exists():                       # re-cut the video we already have (no new API call)
        return cut_frames(name, im, mp4)
    r = httpx.post(VIDEO_API + "/generations", headers=H, timeout=60,
                   json={"model": VIDEO_MODEL, "prompt": VIDEO_PROMPT.format(motion=motion), "image": {"url": uri},
                         "duration": secs, "resolution": "480p", "aspect_ratio": "3:4"})
    if r.status_code != 200:
        sys.exit(f"xAI error {r.status_code}: {r.text[:400]}")
    rid = r.json()["request_id"]
    print(f"video requested ({rid}), waiting...")
    for _ in range(120):
        time.sleep(5)
        j = httpx.get(f"{VIDEO_API}/{rid}", headers=H, timeout=30).json()
        if j.get("status") == "done":
            break
        if j.get("status") == "failed":
            sys.exit(f"video failed: {j}")
    else:
        sys.exit("timed out waiting for the video")
    if not j.get("video", {}).get("respect_moderation", True):
        sys.exit("xAI moderation flagged the video, not using it")
    OUT.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(httpx.get(j["video"]["url"], timeout=120).content)
    cut_frames(name, im, mp4)


def cut_frames(name, im, mp4):
    """Video -> cut-out frames in art/hubie/body/<name>/ plus a preview strip."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp4), "-vf", f"fps={ANIM_FPS}",
                        f"{tmp}/f_%03d.png"], check=True)
        raws = sorted(Path(tmp).glob("f_*.png"))
        cuts = [cutout(Image.open(f).convert("RGB")) for f in raws]
    # one shared scale (so he doesn't pulse in size): match the still sprite's height on the first frame
    k = im.height / max(1, cuts[0].height)
    frames = [c.resize((max(1, round(c.width * k)), max(1, round(c.height * k))), Image.LANCZOS) for c in cuts]
    # pad every frame to one canvas, feet on the bottom edge, centred, so nothing jitters
    W = max(f.width for f in frames)
    Hh = max(f.height for f in frames)
    folder = BODY / name
    if folder.exists():
        for old in folder.glob("frame_*.png"):
            old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        c = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
        c.alpha_composite(f, ((W - f.width) // 2, Hh - f.height))
        c.save(folder / f"frame_{i:03d}.png", optimize=True)
    strip = Image.new("RGBA", (min(len(frames), 8) * (W + 10) + 10, Hh + 20), (22, 26, 38, 255))
    for i, f in enumerate(frames[:: max(1, len(frames) // 8)][:8]):
        strip.alpha_composite(f, (10 + i * (W + 10), 10 + Hh - f.height))
    strip.convert("RGB").save(OUT / f"{name}_anim_preview.png")
    print(f"{len(frames)} frames -> {folder}  (preview: {OUT / (name + '_anim_preview.png')}). Restart Hubie to load.")


CAM_DIR = APP / "art" / "hubie" / "cam"
CAM_SRC = {"A": "idle", "B": "idle2"}
CAM_PROMPTS = {
    "talk": ("He is talking to the camera like a relaxed, friendly streamer: his mouth opens and closes naturally "
             "as if speaking in full sentences, small head nods and tilts, an occasional blink, slight shoulder "
             "movement. He keeps his paws where they are."),
    "idle": ("He is idle and relaxed, listening: mouth closed the whole time, slow breathing, a blink or two, a slow "
             "small head tilt, a glance to the side and back to the camera. He keeps his paws where they are."),
}
CAM_TAIL = (" His black top hat keeps exactly one crown and ONE brim, exactly as in the image, with the gold H badge; "
            "it never doubles, stretches or changes shape. "
            "Same character and art style, same head-and-shoulders framing and size, he stays in place. The camera "
            "does not move. The background stays plain solid pure green (#00FF00) the whole time. No text, no new "
            "objects. Smooth motion that ends close to how it started so it loops.")


def key_full(img):
    """Green-screen key WITHOUT cropping (cam frames must keep the exact framing)."""
    import numpy as np
    from scipy import ndimage
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    spill = g - np.maximum(r, b)
    screen = (spill > 45) & ((b >= 0.7 * r) | ((g > 180) & (r < 70)))
    lab, _ = ndimage.label(screen)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(edge))
    band = ndimage.binary_dilation(bg, iterations=3) & ~bg
    alpha = np.ones(bg.shape, np.float32)
    alpha[bg] = 0
    alpha[band] = np.clip(1 - (spill[band] - 25) / 70, 0, 1)
    g2 = np.where(band | bg, np.minimum(g, np.maximum(r, b) + 12), g)
    return Image.fromarray(np.dstack([r, g2, b, alpha * 255]).clip(0, 255).astype("uint8"), "RGBA")


def mouth_open(frame, box):
    """0..1-ish score of how open the mouth is: dark/red mouth-interior pixels inside the mouth box."""
    import numpy as np
    a = np.asarray(frame.crop(box).convert("RGBA")).astype(np.float32)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    lum = (r + g + b) / 3
    inside = (al > 128) & (((lum < 75) & (r >= b)) | ((r > 150) & (g < 120) & (b < 130)))   # dark mouth or tongue
    return float(inside.mean())


def cam(mode, pose, secs):
    key = os.environ.get("XAI_API_KEY")
    if not key:
        sys.exit("XAI_API_KEY is missing: add it to /opt/hubie/.env")
    src = Image.open(APP / "art" / "hubie" / f"{CAM_SRC[pose]}.png").convert("RGBA").resize((720, 720), Image.LANCZOS)
    canvas = Image.new("RGBA", (720, 720), (0, 255, 0, 255))
    canvas.alpha_composite(src)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = httpx.post(VIDEO_API + "/generations", headers=H, timeout=60,
                   json={"model": VIDEO_MODEL, "prompt": CAM_PROMPTS[mode] + CAM_TAIL, "image": {"url": uri},
                         "duration": secs, "resolution": "720p", "aspect_ratio": "1:1"})
    if r.status_code != 200:
        sys.exit(f"xAI error {r.status_code}: {r.text[:400]}")
    rid = r.json()["request_id"]
    print(f"cam {pose}_{mode}: video requested ({rid}), waiting...", flush=True)
    for _ in range(150):
        time.sleep(5)
        j = httpx.get(f"{VIDEO_API}/{rid}", headers=H, timeout=30).json()
        if j.get("status") in ("done", "failed"):
            break
    if j.get("status") != "done" or not j.get("video", {}).get("respect_moderation", True):
        sys.exit(f"video not usable: {j.get('status')}")
    OUT.mkdir(parents=True, exist_ok=True)
    mp4 = OUT / f"cam_{pose}_{mode}.mp4"
    mp4.write_bytes(httpx.get(j["video"]["url"], timeout=120).content)
    cut_cam(pose, mode, mp4)


def cut_cam(pose, mode, mp4):
    folder = CAM_DIR / f"{pose}_{mode}"
    if folder.exists():
        for old in folder.glob("frame_*.png"):
            old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    mbox = {"A": (150, 260, 380, 380), "B": (150, 250, 400, 400)}[pose]      # generous mouth area (512px frame)
    scores = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp4), "-vf", f"fps={ANIM_FPS},scale=512:512",
                        f"{tmp}/f_%03d.png"], check=True)
        for i, f in enumerate(sorted(Path(tmp).glob("f_*.png"))):
            fr = key_full(Image.open(f))
            scores.append(mouth_open(fr, mbox))
            fr.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(
                folder / f"frame_{i:03d}.png", optimize=True)
    lo, hi = min(scores), max(scores)
    norm = [round((s - lo) / (hi - lo), 3) if hi > lo else 0.0 for s in scores]
    (folder / "mouth.json").write_text(json.dumps({"fps": ANIM_FPS, "open": norm}))
    print(f"{len(norm)} frames -> {folder}; mouth openness range {lo:.3f}..{hi:.3f}", flush=True)


def install(name, version, caption, mood, confetti):
    src = OUT / f"{name}_{version}.png"
    if not src.exists():
        sys.exit(f"{src} not found: run 'new' first")
    BODY.mkdir(parents=True, exist_ok=True)
    Image.open(src).save(BODY / f"{name}.png", optimize=True)
    reg = BODY / "commands.json"
    cmds = json.loads(reg.read_text()) if reg.exists() else {}
    cmds[name] = {"pose": name, "caption": caption, "mood": mood, "confetti": confetti}
    reg.write_text(json.dumps(cmds, indent=2))
    print(f"installed !{name} -> {BODY / (name + '.png')}. Restart Hubie to load it.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("new")
    a.add_argument("name")
    a.add_argument("pose")
    a.add_argument("--n", type=int, default=3)
    b = sub.add_parser("install")
    b.add_argument("name")
    b.add_argument("version", type=int)
    b.add_argument("caption", help='e.g. "{u} asked for a moonwalk" ({u} = viewer name)')
    b.add_argument("--mood", default="happy")
    b.add_argument("--confetti", action="store_true")
    c = sub.add_parser("animate")
    c.add_argument("name")
    c.add_argument("motion")
    c.add_argument("--secs", type=int, default=3)
    c.add_argument("--reuse", action="store_true", help="re-cut the last video instead of making a new one")
    d = sub.add_parser("cam")
    d.add_argument("mode", choices=["talk", "idle"])
    d.add_argument("pose", choices=["A", "B"])
    d.add_argument("--secs", type=int, default=6)
    d.add_argument("--reuse", action="store_true")
    args = ap.parse_args()
    if args.cmd == "cam":
        mp4 = OUT / f"cam_{args.pose}_{args.mode}.mp4"
        cut_cam(args.pose, args.mode, mp4) if args.reuse and mp4.exists() else cam(args.mode, args.pose, args.secs)
        sys.exit(0)
    if args.cmd == "animate":
        animate(args.name, args.motion, args.secs, args.reuse)
    elif args.cmd == "new":
        generate(args.name, args.pose, args.n)
    else:
        install(args.name, args.version, args.caption, args.mood, args.confetti)
