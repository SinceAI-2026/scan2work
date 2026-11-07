"""AR overlay: the real walkthrough video with the digital twin drawn on top.

Every frame gets the camera pose interpolated from the aligned walk (pipeline/walk.py), the extracted 3D
objects are projected into it, hidden ones are culled with a z-buffer against the scan, and the visible
ones are framed in their status colour with non-overlapping labels. This is Valmet's tablet use case:
the virtual twin registered over the real camera view.

Run (after python -m pipeline.run):
  python -m scripts.render_overlay --video "../Valmet_SinceAI2026 _1_/Industrial Boiler Room (1).mp4"
Writes web/data/overlay_walk.mp4 (10 fps, 1280x720, H.264).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import config as C, ingest, walk as WK  # noqa: E402
from pipeline.detect_lift import ViewGeometry, project  # noqa: E402
from pipeline.walk_photos import EDGES, _corners, _members  # noqa: E402

W, H, FPS = 1280, 720, 10
COL = {"target": (255, 45, 45), "degraded": (255, 120, 30), "attention_required": (255, 120, 30),
       "watch": (255, 200, 0), "healthy_device": (60, 220, 90), "healthy": (90, 200, 255),
       "movable": (190, 110, 255), "not_instrumented": (170, 170, 170)}
MAX_BOXES, MAX_LABELS, MAX_DEPTH = 28, 9, 4.2


def font(size, bold=False):
    for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), "Arial Bold.ttf" if bold else "Arial.ttf",
                 "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def colour(a):
    if a.get("is_target"):
        return COL["target"]
    if a.get("movable"):
        return COL["movable"]
    st = a.get("condition_state", "healthy")
    if st == "healthy":
        return COL["healthy_device"] if a.get("mapped") else COL["healthy"]
    return COL.get(st, COL["healthy"])


def priority(a, depth):
    p = 0.0
    if a.get("is_target"):
        p += 100
    if a.get("mapped"):
        p += 20
    if a.get("condition_state") not in ("healthy", "movable", "not_instrumented"):
        p += 30
    if a.get("label_source", "geometric_rules") != "geometric_rules":
        p += 5
    return p + 3 * min(1.0, float(np.prod(a["size"])) ** (1 / 3)) - depth


def pose_at(walk, t):
    """Camera pose at time t (s), linear between the 3 fps aligned poses."""
    tt = walk["t"]
    i = int(np.clip(np.searchsorted(tt, t) - 1, 0, len(tt) - 2))
    u = float(np.clip((t - tt[i]) / max(tt[i + 1] - tt[i], 1e-6), 0, 1))
    lerp = lambda k: (1 - u) * walk[k][i] + u * walk[k][i + 1]  # noqa: E731
    f, up = lerp("fwd"), lerp("up")
    one = {"t": np.array([t]), "pos": lerp("pos")[None], "fwd": (f / np.linalg.norm(f))[None],
           "up": (up / np.linalg.norm(up))[None], "files": [None]}
    cam = WK.camera(one, 0, W, H)
    cam["V"], cam["P"] = cam["_V"], cam["_P"]
    return cam


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(ROOT.parent / "Valmet_SinceAI2026 _1_" / "Industrial Boiler Room (1).mp4"))
    ap.add_argument("--out", default=str(C.OUT_DIR / "overlay_walk.mp4"))
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = whole video")
    args = ap.parse_args()

    twin = json.loads((C.OUT_DIR / "digital_twin.json").read_text())
    assets = [a for a in twin["assets"] if a.get("condition_state") != "clutter"]
    walk = WK.load()
    splat = ingest.level_scene(ingest.load_splat())
    sel = np.where(splat["roi"] & (splat["opacity"] > 0.2) & (splat["scale"] < 0.2))[0]
    pts = splat["xyz"][sel]
    members = _members(splat, assets, sel, max_pts=250)
    corners = {a["id"]: _corners(a) for a in assets}
    f_small, f_bold, f_big = font(15), font(15, True), font(24, True)
    n_objects = len(assets)

    dur = args.seconds or (walk["t"][-1] - args.start)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(args.start), "-i", args.video, "-t", str(dur),
                            "-vf", f"fps={FPS},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", args.out], stdin=subprocess.PIPE)
    shown_prev, k = set(), 0
    seen_ids, attention_seen = set(), set()
    while True:
        buf = dec.stdout.read(W * H * 3)
        if len(buf) < W * H * 3:
            break
        t = args.start + k / FPS
        k += 1
        img = Image.frombuffer("RGB", (W, H), buf).copy()
        cam = pose_at(walk, min(t, walk["t"][-1]))
        vg = ViewGeometry(pts, cam)
        vis = np.zeros(len(pts), bool); vis[vg.idx[vg.visible]] = True
        infr = np.zeros(len(pts), bool); infr[vg.idx] = True
        cand = []
        for a in assets:
            mem = members[a["id"]]
            if len(mem) < 12:
                continue
            px, py, d = project(corners[a["id"]], cam)
            if (d <= 0.25).any() or np.median(d) > MAX_DEPTH:
                continue
            x0, x1, y0, y1 = px.min(), px.max(), py.min(), py.max()
            if x1 < 0 or x0 > W or y1 < 0 or y0 > H or (x1 - x0) * (y1 - y0) > 0.6 * W * H:
                continue
            in_f = infr[mem].sum()
            if in_f < 0.4 * len(mem):
                continue
            vfrac = vis[mem].sum() / max(1, in_f)
            if vfrac < (0.2 if a["id"] in shown_prev else 0.35):    # hysteresis: no flicker
                continue
            cand.append((priority(a, float(np.median(d))), a, px, py))
        cand.sort(key=lambda c: -c[0])
        cand = cand[:MAX_BOXES]
        shown_prev = {a["id"] for _, a, _, _ in cand}
        seen_ids |= shown_prev
        attention_seen |= {a["id"] for _, a, _, _ in cand
                           if a.get("condition_state") not in ("healthy", "movable", "not_instrumented")}

        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        for pr, a, px, py in reversed(cand):                           # most important drawn last (on top)
            c = colour(a)
            wdt = 4 if a.get("is_target") else (2 if pr > 10 else 1)
            for i, j in EDGES:
                d.line([(px[i], py[i]), (px[j], py[j])], fill=c + (255 if pr > 10 else 170,), width=wdt)
        placed = []                                                     # greedy, non-overlapping labels
        for pr, a, px, py in cand[:MAX_LABELS * 2]:
            if len(placed) >= MAX_LABELS:
                break
            if not a.get("mapped") and a.get("label_source", "geometric_rules") == "geometric_rules":
                continue                                                # unverified colour-rule label: box only
            name = a.get("label", a["class"])
            name = name[:1].upper() + name[1:]
            line1 = f"{a['tag']}  {name}" if a.get("mapped") else name
            st = a.get("condition_state", "")
            line2 = (st.replace("_", " ") if a.get("mapped") or a.get("is_target") or st not in ("healthy",) else "")
            if a.get("movable"):
                line2 = "movable"
            tw = max(d.textlength(line1, font=f_bold), d.textlength(line2, font=f_small)) + 10
            th = 38 if line2 else 22
            ax = float(np.clip((px.min() + px.max()) / 2 - tw / 2, 4, W - tw - 4))
            ay = float(np.clip(py.min() - th - 4, 44, H - th - 4))
            box = (ax, ay, ax + tw, ay + th)
            if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed):
                continue
            placed.append(box)
            c = colour(a)
            d.rounded_rectangle(box, 5, fill=(15, 18, 24, 205), outline=c + (255,), width=2)
            d.text((ax + 5, ay + 3), line1, font=f_bold, fill=(255, 255, 255, 255))
            if line2:
                d.text((ax + 5, ay + 20), line2, font=f_small, fill=c + (255,))
        # HUD
        d.rectangle((0, 0, W, 38), fill=(10, 12, 16, 200))
        d.text((12, 9), f"Valmet digital twin  |  live overlay on walkthrough video  t={t:5.1f}s", font=f_bold,
               fill=(255, 255, 255, 255))
        hud = f"{len(cand)} in view  |  {len(seen_ids)} of {n_objects} objects passed  |  {len(attention_seen)} need attention"
        d.text((W - 12 - d.textlength(hud, font=f_small), 11), hud, font=f_small, fill=(200, 220, 255, 255))
        tgt = next((a for _, a, _, _ in cand if a.get("is_target")), None)
        if tgt:
            msg = f"FAULTY DEVICE IN VIEW: {tgt['tag']}  -  {tgt.get('issue_summary', '')}"
            mw = d.textlength(msg, font=f_big) + 24
            d.rounded_rectangle(((W - mw) / 2, H - 58, (W + mw) / 2, H - 16), 8, fill=(200, 30, 30, 225))
            d.text(((W - mw) / 2 + 12, H - 52), msg, font=f_big, fill=(255, 255, 255, 255))
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        enc.stdin.write(img.tobytes())
        if k % 100 == 0:
            print(f"  {k} frames ({t:.0f} s)")
    enc.stdin.close(); enc.wait(); dec.wait()
    print(f"wrote {args.out}: {k} frames, {len(seen_ids)} objects shown, {len(attention_seen)} need attention")


if __name__ == "__main__":
    main()
