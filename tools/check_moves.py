"""Quality check for animated moves: Claude vision compares 5 frames of each move with the reference
pose (same character? leftover green? broken limbs? does it read as the move?) and writes a preview GIF.
Run from the app folder: MOVES=dance,wave PYTHONPATH=. python tools/check_moves.py
"""
import os, io, base64, glob, httpx
from PIL import Image
from hubie import config
ref = Image.open("art/hubie/body/dapper.png").convert("RGBA")
rs = Image.new("RGB", (320, 380), (40, 40, 48)); rs.paste(ref, (50, 370 - ref.height), ref)
def b64(im):
    b = io.BytesIO(); im.save(b, "PNG"); return base64.b64encode(b.getvalue()).decode()
for move in os.environ.get("MOVES", "wave,hat,flex,nap,thinking,cheer,facepalm,snack,laughing,dapper").split(","):
    fr = sorted(glob.glob(f"art/hubie/body/{move}/frame_*.png"))
    frames = [Image.open(f).convert("RGBA") for f in fr]
    W = max(f.width for f in frames); H = max(f.height for f in frames)
    gif = []
    for f in frames:
        bg = Image.new("RGBA", (W + 40, H + 40), (22, 26, 38, 255)); bg.alpha_composite(f, (20, 20)); gif.append(bg.convert("P", palette=Image.ADAPTIVE))
    gif[0].save(f"/opt/hubie/poses/{move}_anim.gif", save_all=True, append_images=gif[1:], duration=83, loop=0)
    pick = [frames[i] for i in (0, len(frames)//4, len(frames)//2, 3*len(frames)//4, len(frames)-1)]
    sh = Image.new("RGB", (sum(p.width for p in pick) + 60, max(p.height for p in pick) + 20), (40, 40, 48)); x = 10
    for p in pick: sh.paste(p, (x, sh.height - 10 - p.height), p); x += p.width + 10
    msg = [{"type": "text", "text": f"Image 1 is the reference character. Image 2 shows 5 frames (left to right) of a short cartoon animation of the same character doing: {move}. Cut out from green screen onto dark grey. Check: same character every frame (face, top hat with gold H, navy tailcoat, gold vest, bow tie)? Any leftover green, missing/broken/extra limbs, melted faces, glitches? Does the motion read as {move}? Reply in ONE line of max 25 words, then VERDICT: GOOD, OK, or REDO."},
           {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(rs)}},
           {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(sh)}}]
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       json={"model": config.MODEL, "max_tokens": 300, "thinking": {"type": "disabled"}, "messages": [{"role": "user", "content": msg}]}, timeout=60).json()
        txt = " ".join("".join(x.get("text", "") for x in r.get("content", [])).split())
    except Exception as e:
        txt = f"check failed: {e!r}"
    print(f"{move}: {len(frames)} frames, {W}x{H} | {txt}", flush=True)
print("QCDONE", flush=True)
