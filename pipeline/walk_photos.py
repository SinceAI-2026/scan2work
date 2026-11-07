"""Best real photo of every object, from the challenge walkthrough video.

The video's 334 camera poses are aligned to the scan (pipeline/walk.py), so any 3D object can be
projected into every frame. For each object we pick the frame where it is
  * actually visible: share of its splats that pass a z-buffer test against the whole room,
  * a useful size in the image and near the centre, fully in front of the camera,
  * sharp: variance-of-Laplacian of the frame (hand-held video has motion blur; idea from
    scan2service pipeline/sharp_frames.py),
crop it with the object's oriented 3D box drawn in yellow (same convention as the splat renders),
and save web/data/photos/<id>.jpg. Real photos are far sharper than splat renders, so they are
used for AI labelling when present, and shown in the viewer as "Real walkthrough frame".
"""
from __future__ import annotations

import itertools
import json
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import config as C
from . import walk as WK
from .detect_lift import ViewGeometry, project

PHOTO_DIR = C.OUT_DIR / "photos"
VERSION = "photo-v1"
MIN_SCORE = 0.08
OUT_PX = 448


def _corners(a: dict) -> np.ndarray:
    cx, cy, cz = a["center"]; sx, sy, sz = a["size"]
    c, s = math.cos(a.get("yaw", 0.0)), math.sin(a.get("yaw", 0.0))
    out = []
    for lx, ly, lz in itertools.product((-sx / 2, sx / 2), (-sy / 2, sy / 2), (-sz / 2, sz / 2)):
        out.append([cx + lx * c - lz * s, cy + ly, cz + lx * s + lz * c])
    return np.array(out)


EDGES = [(i, j) for i in range(8) for j in range(i + 1, 8) if bin(i ^ j).count("1") == 1]


def _sharpness(path) -> float:
    g = Image.open(path).convert("L").resize((320, 180))
    lap = np.asarray(g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], 1, 128)), float) - 128
    return float(lap.var())


def _members(splat: dict, assets: list, sel: np.ndarray, max_pts: int = 400) -> dict:
    """Up to max_pts splat indices (into sel) inside each object's oriented box."""
    xyz = splat["xyz"][sel]
    out = {}
    for a in assets:
        cx, cy, cz = a["center"]; sx, sy, sz = a["size"]
        c, s = math.cos(a.get("yaw", 0.0)), math.sin(a.get("yaw", 0.0))
        dx, dz = xyz[:, 0] - cx, xyz[:, 2] - cz
        m = ((np.abs(dx * c + dz * s) <= sx / 2) & (np.abs(-dx * s + dz * c) <= sz / 2)
             & (np.abs(xyz[:, 1] - cy) <= sy / 2))
        idx = np.where(m)[0]
        if len(idx) > max_pts:
            idx = idx[np.linspace(0, len(idx) - 1, max_pts).astype(int)]
        out[a["id"]] = idx
    return out


def select(splat: dict, assets: list, k: int = 1, min_angle_deg: float = 20.0) -> dict:
    """{asset id: (frame index, score)} of the best real frame (k=1), or {id: [(fi, score), ...]} of up to k
    good frames whose viewing directions differ by >= min_angle_deg (k>1). Empty if the video is absent."""
    walk = WK.load()
    if walk is None:
        return {}
    sel = np.where(splat["roi"] & (splat["opacity"] > 0.2) & (splat["scale"] < 0.2))[0]
    pts = splat["xyz"][sel]
    members = _members(splat, assets, sel)
    sharp = np.array([_sharpness(f) for f in walk["files"]])
    sharp_w = np.clip(sharp / np.median(sharp), 0.3, 1.5)
    corners = {a["id"]: _corners(a) for a in assets}
    cands = {}
    for fi in range(len(walk["files"])):
        cam = WK.camera(walk, fi)
        cam["V"], cam["P"] = cam["_V"], cam["_P"]
        cam["f"] = cam["P"][1, 1] * cam["H"] / 2
        vg = ViewGeometry(pts, cam)
        vis = np.zeros(len(pts), bool); vis[vg.idx[vg.visible]] = True
        infr = np.zeros(len(pts), bool); infr[vg.idx] = True
        W, H = cam["W"], cam["H"]
        for a in assets:
            mem = members[a["id"]]
            if len(mem) < 15:
                continue
            px, py, d = project(corners[a["id"]], cam)
            if (d <= 0.3).any() or np.median(d) > 4.5:
                continue
            x0, x1, y0, y1 = px.min(), px.max(), py.min(), py.max()
            area = (x1 - x0) * (y1 - y0)
            cx0, cx1, cy0, cy1 = max(x0, 0), min(x1, W), max(y0, 0), min(y1, H)
            if cx1 <= cx0 or cy1 <= cy0 or area <= 0:
                continue
            inside = (cx1 - cx0) * (cy1 - cy0) / area
            if inside < 0.75 or max(x1 - x0, y1 - y0) < 36 or area > 0.5 * W * H:
                continue
            in_f = infr[mem].sum()
            if in_f < 0.5 * len(mem):
                continue
            vfrac = vis[mem].sum() / max(1, in_f)
            size = min(1.0, math.sqrt(area / (0.05 * W * H)))
            centre = 1 - 0.5 * math.hypot((x0 + x1) / W - 1, (y0 + y1) / H - 1)
            score = vfrac ** 2 * size * centre * inside * sharp_w[fi]
            if score >= MIN_SCORE:
                cands.setdefault(a["id"], []).append((float(score), fi))
    if k == 1:
        return {aid: (max(c)[1], max(c)[0]) for aid, c in cands.items()}
    ctr = {a["id"]: np.array(a["center"]) for a in assets}
    out = {}
    for aid, c in cands.items():
        picked, dirs = [], []
        for score, fi in sorted(c, reverse=True):
            v = ctr[aid] - walk["pos"][fi]; v /= np.linalg.norm(v) + 1e-9
            if all(math.degrees(math.acos(np.clip(v @ d, -1, 1))) >= min_angle_deg for d in dirs):
                picked.append((fi, score)); dirs.append(v)
            if len(picked) == k:
                break
        out[aid] = picked
    return out


def crop(asset: dict, walk: dict, fi: int, scale: float = 0.8, pad: float = 40) -> Image.Image:
    cam = WK.camera(walk, fi); cam["V"], cam["P"] = cam["_V"], cam["_P"]
    img = Image.open(walk["files"][fi]).convert("RGB")
    sx_, sy_ = img.width / cam["W"], img.height / cam["H"]
    px, py, _ = project(_corners(asset), cam)
    px, py = px * sx_, py * sy_
    d = ImageDraw.Draw(img)
    for i, j in EDGES:
        d.line([(px[i], py[i]), (px[j], py[j])], fill=(255, 220, 0), width=3)
    x0, x1, y0, y1 = px.min(), px.max(), py.min(), py.max()
    half = max(x1 - x0, y1 - y0) * scale + pad
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    box = [int(max(0, cx - half)), int(max(0, cy - half)), int(min(img.width, cx + half)), int(min(img.height, cy + half))]
    out = img.crop(box)
    out.thumbnail((OUT_PX, OUT_PX))
    return out


def build(splat: dict, assets: list, key_fn, out_dir=PHOTO_DIR, set_field: bool = True) -> int:
    """Writes photos for `assets` (skips ones already current) and, if set_field, sets asset['photo']. Returns #photos."""
    walk = WK.load()
    if walk is None:
        print("   [photos] walkthrough video frames not found, skipping real photos")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    idx_path = out_dir / "index.json"
    try:
        index = json.loads(idx_path.read_text())
    except (OSError, ValueError):
        index = {}
    todo = [a for a in assets if index.get(a["id"], {}).get("key") != f"{VERSION}:{key_fn(a)}"
            or not (out_dir / f"{a['id']}.jpg").exists()]
    if todo:
        picks = select(splat, todo)
        for a in todo:
            index.pop(a["id"], None)
            if a["id"] in picks:
                fi, score = picks[a["id"]]
                crop(a, walk, fi).save(out_dir / f"{a['id']}.jpg", quality=88)
                index[a["id"]] = {"key": f"{VERSION}:{key_fn(a)}", "frame": walk["files"][fi].name,
                                  "t": round(float(walk["t"][fi]), 2), "score": round(score, 3)}
        idx_path.write_text(json.dumps(index, indent=0))
        _idx_cache.pop(out_dir, None)
    n = 0
    for a in assets:
        e = index.get(a["id"])
        if not set_field:
            n += bool(e and e["key"] == f"{VERSION}:{key_fn(a)}")
        elif e and e["key"] == f"{VERSION}:{key_fn(a)}":
            a["photo"] = f"data/photos/{a['id']}.jpg"
            a["photo_source"] = {"video_t_s": e["t"], "frame": e["frame"], "score": e["score"]}
            n += 1
        else:
            a.pop("photo", None); a.pop("photo_source", None)
    print(f"   [photos] {n}/{len(assets)} objects have a real walkthrough photo ({len(todo)} recomputed)")
    return n


LABEL_DIR = C.OUT_DIR / "photos_label"   # photos of the pre-merge clusters, used only for AI labelling
_idx_cache: dict = {}


def data_url(asset: dict, key_fn, out_dir=LABEL_DIR) -> str | None:
    """JPEG data URL of the real photo, if current (for AI labelling)."""
    import base64
    try:
        if out_dir not in _idx_cache:
            _idx_cache[out_dir] = json.loads((out_dir / "index.json").read_text())
        e = _idx_cache[out_dir].get(asset["id"])
    except (OSError, ValueError):
        return None
    if not e or e["key"] != f"{VERSION}:{key_fn(asset)}":
        return None
    return "data:image/jpeg;base64," + base64.b64encode((out_dir / f"{asset['id']}.jpg").read_bytes()).decode()


# ----------------------------------------------------------------------------- multi-view collage for labelling
COLLAGE_DIR = C.OUT_DIR / "collage_label"
COLLAGE_VERSION = "collage-v1"
TILE = 320


def _tile(img: Image.Image, caption: str) -> Image.Image:
    t = Image.new("RGB", (TILE, TILE), (25, 25, 25))
    im = img.copy(); im.thumbnail((TILE, TILE - 18))
    t.paste(im, ((TILE - im.width) // 2, 18 + (TILE - 18 - im.height) // 2))
    ImageDraw.Draw(t).text((4, 3), caption, fill=(255, 255, 0))
    return t


def build_collages(splat: dict, assets: list, key_fn, render_url) -> dict:
    """2x2 per object: [scan render | best photo] / [2nd photo from another angle | wide context photo].
    render_url(asset) -> data URL or None. Returns {id: data URL} for objects with a render and >= 2 photos."""
    import base64, io
    walk = WK.load()
    if walk is None:
        return {}
    COLLAGE_DIR.mkdir(parents=True, exist_ok=True)
    idx_path = COLLAGE_DIR / "index.json"
    try:
        index = json.loads(idx_path.read_text())
    except (OSError, ValueError):
        index = {}
    key = lambda a: f"{COLLAGE_VERSION}:{key_fn(a)}"  # noqa: E731
    todo = [a for a in assets if index.get(a["id"]) != key(a) or not (COLLAGE_DIR / f"{a['id']}.jpg").exists()]
    if todo:
        picks = select(splat, todo, k=3)
        for a in todo:
            index.pop(a["id"], None)
            r = render_url(a)
            frames = picks.get(a["id"], [])
            if not r or not r.startswith("data:image/jpeg") or len(frames) < 2:
                continue
            ren = Image.open(io.BytesIO(base64.b64decode(r.split(",", 1)[1]))).convert("RGB")
            p1 = crop(a, walk, frames[0][0])
            p2 = crop(a, walk, frames[1][0])
            ctx = crop(a, walk, frames[0][0], scale=2.4, pad=120)
            out = Image.new("RGB", (2 * TILE, 2 * TILE))
            for k, (im, cap) in enumerate(((ren, "A: 3D scan render"), (p1, "B: real photo"),
                                           (p2, "C: real photo, other angle"), (ctx, "D: wider context"))):
                out.paste(_tile(im, cap), ((k % 2) * TILE, (k // 2) * TILE))
            out.save(COLLAGE_DIR / f"{a['id']}.jpg", quality=86)
            index[a["id"]] = key(a)
        idx_path.write_text(json.dumps(index))
    urls = {}
    for a in assets:
        f = COLLAGE_DIR / f"{a['id']}.jpg"
        if index.get(a["id"]) == key(a) and f.exists():
            urls[a["id"]] = "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()
    print(f"   [photos] {len(urls)}/{len(assets)} objects have a 4-view collage for labelling")
    return urls
