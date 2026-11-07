"""Stage 2c - Open-vocabulary 2D detection on rendered room views, lifted to 3D.

The brief's "multi-view vision foundation models lifted to 3D" route:
  1. scripts/render_views.py renders ~32 photo-like views from walkable positions with KNOWN cameras.
  2. Gemini detects every object in every view (2D box + class from our vocabulary), batched + cached.
  3. Each 2D box is lifted to 3D: the splats visible inside it (z-buffer test) are depth-clustered to the
     front-most surface, giving a 3D point set and box.
  4. Lifts of the same object from different views are fused; the result either CONFIRMS an existing
     cluster (and relabels it if it only had a colour-rule label) or becomes a NEW object the
     clustering missed.
Source of the 2D views (C.DETECT_SOURCE): "video" = ~42 sharp frames of the REAL walkthrough video, whose
camera poses are aligned to the scan (pipeline/walk.py); "renders" = splat renders (fallback).
On real frames Gemini also names each object in free text ("washing machine", "wet vacuum cleaner"); that
name becomes the object's label. A detection of a single-body object (appliance, cabinet, cart...) that
covers several clustering fragments merges them into ONE object with one box.
Without views or a Gemini key the stage is skipped and the pipeline is unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from collections import Counter

import numpy as np

from . import config as C
from . import decisions_client as D
from .perception import _build_asset_dict

VIEWS_DIR = C.OUT_DIR / "views3d"
DET_VERSION = "det-v2"
KEY_STEP = 8         # one keyframe (the sharpest) per 8 video frames at 3 fps -> ~42 frames
# detections of these kinds are one physical body: fragments inside them are merged into one object
# with the largest plausible extent (m) of one such object: a lift bigger than this is not one object
SINGLE_BODY = {"appliance": 1.3, "electrical_cabinet": 2.4, "tank_vessel": 2.4, "cart_trolley": 1.6, "chair_furniture": 1.3,
               "box_crate": 1.2, "container": 0.9, "cleaning_equipment": 1.8, "ladder": 2.6, "shelf_rack": 2.4,
               "sink": 1.2, "heat_exchanger": 1.6}
UNSURE = {"clutter", "industrial_asset", "structural_column"}   # fragment labels that may be absorbed by anything
ZCELL = 4            # z-buffer cell size in pixels
VIS_TOL = 0.10       # m behind the z-buffer surface still counted as visible
MIN_PTS = 40         # visible splats needed to lift a detection
MAX_EXTENT = 3.0     # m, reject lifts larger than this (walls, floor)


# ----------------------------------------------------------------------------- cameras & projection
def video_cameras() -> list[dict]:
    from . import walk as WK
    from .walk_photos import _sharpness
    w = WK.load()
    if w is None:
        return []
    sharp = np.array([_sharpness(f) for f in w["files"]])
    cams = []
    for b in range(0, len(sharp), KEY_STEP):
        fi = b + int(np.argmax(sharp[b:b + KEY_STEP]))
        c = WK.camera(w, fi)
        c["V"], c["P"] = c.pop("_V"), c.pop("_P")
        c["f"] = c["P"][1, 1] * c["H"] / 2.0
        c["path"] = c["file"]; c["file"] = w["files"][fi].name; c["source"] = "video"
        cams.append(c)
    if C.DETECT_TILES:
        cams += [t for c in list(cams) for t in _tiles(c)]
    return cams


TILE_DIR = C.OUT_DIR / "walk_tiles"


def _tiles(cam: dict) -> list[dict]:
    """Four zoomed quarters of a keyframe (SAHI-style slicing): small valves and gauges become 4x larger.
    Each tile is a camera of its own: same pose, projection scaled by 2 and shifted to that quarter."""
    from PIL import Image
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    W, H = cam["W"], cam["H"]
    out, img = [], None
    for k, (ix, iy) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
        f = TILE_DIR / f"{Path(cam['file']).stem}_t{k}.jpg"
        if not f.exists():
            img = img or Image.open(cam["path"]).convert("RGB").resize((W, H))
            img.crop((ix * W // 2, iy * H // 2, (ix + 1) * W // 2, (iy + 1) * H // 2)).resize((W, H), Image.LANCZOS).save(f, quality=90)
        S = np.eye(4)
        S[0, 0] = S[1, 1] = 2.0
        S[0, 3] = 1.0 - 2 * ix            # left quarter: x' = 2x + 1, right: 2x - 1 (clip space, times w)
        S[1, 3] = -1.0 + 2 * iy           # top quarter (NDC y up): y' = 2y - 1, bottom: 2y + 1
        t = dict(cam)
        t["P"] = S @ cam["P"]
        t["f"] = t["P"][1, 1] * H / 2.0
        t["file"], t["path"], t["source"] = f.name, str(f), "video_tile"
        out.append(t)
    return out


def load_cameras() -> list[dict]:
    if C.DETECT_SOURCE == "video":
        cams = video_cameras()
        if cams:
            return cams
    try:
        cams = json.loads((VIEWS_DIR / "cameras.json").read_text())
    except (OSError, ValueError):
        return []
    for c in cams:
        c["V"] = np.array(c["view"], dtype=np.float64).reshape(4, 4).T   # three.js is column-major
        c["P"] = np.array(c["proj"], dtype=np.float64).reshape(4, 4).T
        c["f"] = c["P"][1, 1] * c["H"] / 2.0                              # focal length in pixels
        c["path"] = str(VIEWS_DIR / c["file"]); c["source"] = "renders"
    return cams


def project(xyz: np.ndarray, cam: dict):
    h = np.c_[xyz, np.ones(len(xyz))]
    v = h @ cam["V"].T
    clip = v @ cam["P"].T
    w = clip[:, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        px = (clip[:, 0] / w * 0.5 + 0.5) * cam["W"]
        py = (1.0 - (clip[:, 1] / w * 0.5 + 0.5)) * cam["H"]
    depth = -v[:, 2]
    return px, py, depth


class ViewGeometry:
    """Per-camera projected splats + coarse z-buffer, computed once and reused for every box."""

    def __init__(self, xyz: np.ndarray, cam: dict):
        px, py, d = project(xyz, cam)
        ok = (d > 0.15) & (d < 9.0) & (px >= 0) & (px < cam["W"]) & (py >= 0) & (py < cam["H"])
        self.idx = np.where(ok)[0]
        self.px, self.py, self.d = px[ok], py[ok], d[ok]
        gw, gh = int(cam["W"] // ZCELL) + 1, int(cam["H"] // ZCELL) + 1
        cell = (self.py // ZCELL).astype(int) * gw + (self.px // ZCELL).astype(int)
        z = np.full(gw * gh, np.inf)
        np.minimum.at(z, cell, self.d)
        self.visible = self.d <= z[cell] + VIS_TOL
        self.cam = cam

    def lift(self, box_px, xyz: np.ndarray):
        """box_px = (x0, y0, x1, y1) in pixels. Returns global splat indices of the object's front surface."""
        x0, y0, x1, y1 = box_px
        sx, sy = (x1 - x0) * 0.12, (y1 - y0) * 0.12             # shrink: box borders hold background
        m = (self.visible & (self.px >= x0 + sx) & (self.px <= x1 - sx) & (self.py >= y0 + sy) & (self.py <= y1 - sy))
        if m.sum() < MIN_PTS:
            return None
        d = self.d[m]
        d0 = float(np.median(np.sort(d)[: max(MIN_PTS, len(d) // 2)]))   # front-most dominant surface
        width_m = (x1 - x0) / self.cam["f"] * d0
        tol = max(0.12, 0.4 * width_m)
        sel = self.idx[m][np.abs(d - d0) <= tol]
        if len(sel) < MIN_PTS:
            return None
        return sel


# ----------------------------------------------------------------------------- Gemini detection
def _det_key(paths: list[str]) -> str:
    h = hashlib.sha256()
    for f in paths:
        h.update(open(f, "rb").read())
    return f"{DET_VERSION}:{C.GEMINI_MODEL}:{h.hexdigest()[:24]}"


def detect(cams: list[dict], classes: dict) -> dict[str, list]:
    """Returns {view file: [{"label", "box_2d": [ymin, xmin, ymax, xmax] (0-1000)}]} (cached)."""
    from . import gemini_labels as G
    cache = D._load_cache()
    out = {}
    batch_n = C.GEMINI_DET_BATCH
    schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "image_id": {"type": "STRING"},
        "label": {"type": "STRING", "enum": [k for k in classes if k not in ("clutter",)]},
        "name": {"type": "STRING"},
        "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    }, "required": ["image_id", "label", "name", "box_2d"]}}
    guide = "\n".join(f"- {k}: {v[2]}" for k, v in classes.items() if k != "clutter")
    todo = []
    for b in range(0, len(cams), batch_n):
        batch = cams[b:b + batch_n]
        key = _det_key([c["path"] for c in batch])
        name_key = f"{DET_VERSION}-names:{C.GEMINI_MODEL}:" + "|".join(c["file"] for c in batch)
        if name_key in cache or key in cache:          # names are stable across machines, bytes may not be
            hit = cache.get(name_key) or cache[key]
            cache[name_key] = hit
            out.update(hit)
        elif os.environ.get("GEMINI_API_KEY"):
            todo.append((key, batch))

    def ask(item):
        key, batch = item
        tile = batch[0].get("source") == "video_tile"
        src = ("These images are frames of a hand-held walkthrough video of an industrial boiler / equipment room. "
               if batch[0].get("source") == "video" else
               "These images are zoomed-in quarters of frames of a walkthrough video of an industrial boiler / "
               "equipment room; objects cut by the image edge may be detected as far as visible. " if tile else
               "These images are rendered views from inside a 3D scan of an industrial boiler / equipment room. ")
        parts = [{"text": (
            src +
            "In EACH image, detect every distinct physical object: plant equipment (valves, pumps, motors, "
            "gauges/instruments, tanks, cabinets, heat exchangers, pipe runs and fittings, structural frames) and "
            "loose movable items (chairs, carts, buckets/bins, boxes/crates, ladders, hoses/cables, brooms, mops, "
            "vacuum cleaners) and facility items (washing machines and other appliances, sinks, shelves). Do not "
            "detect walls, floor or ceiling. One box per whole physical object (a washing machine is ONE box, not "
            "its door and panel). Give each detection its image_id, a label from the list, a short specific name "
            "in English (e.g. 'washing machine', 'wet vacuum cleaner', 'red expansion vessel', 'ball valve'), and "
            "box_2d as [ymin, xmin, ymax, xmax] normalised to 0-1000. Up to 40 detections per image.\n"
            f"Labels:\n{guide}")}]
        for c in batch:
            parts.append({"text": f"image_id {c['file']}"})
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(open(c["path"], "rb").read()).decode()}})
        return key, batch, G._request(parts, schema)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max(1, C.GEMINI_WORKERS)) as pool:
        for n, fut in enumerate(as_completed([pool.submit(ask, it) for it in todo]), 1):
            key, batch, res = fut.result()
            if res == "quota" or not isinstance(res, list):
                print(f"   [detect] batch {n}/{len(todo)} not detected ({'daily quota' if res == 'quota' else 'error'})")
                continue
            per = {c["file"]: [] for c in batch}
            for r in res:
                if isinstance(r, dict) and r.get("image_id") in per and len(r.get("box_2d") or []) == 4 and r.get("label") in classes:
                    per[r["image_id"]].append({"label": r["label"], "name": str(r.get("name", ""))[:40].strip().lower(),
                                               "box_2d": [int(v) for v in r["box_2d"]]})
            cache[key] = per
            cache[f"{DET_VERSION}-names:{C.GEMINI_MODEL}:" + "|".join(c["file"] for c in batch)] = per
            D.save_cache()
            out.update(per)
            print(f"   [detect] batch {n}/{len(todo)}: {sum(len(v) for v in per.values())} detections")
    return out


# ----------------------------------------------------------------------------- fusion with existing objects
def _aabb(pts):
    return np.percentile(pts, 5, axis=0), np.percentile(pts, 95, axis=0)


def _iou(alo, ahi, blo, bhi):
    inter = np.prod(np.clip(np.minimum(ahi, bhi) - np.maximum(alo, blo), 0, None))
    va, vb = np.prod(np.maximum(ahi - alo, 1e-6)), np.prod(np.maximum(bhi - blo, 1e-6))
    return inter / (va + vb - inter)


def _inside(p, lo, hi, pad=0.0):
    return bool(np.all(p >= lo - pad) and np.all(p <= hi + pad))


def lift_and_fuse(splat: dict, cams: list[dict], detections: dict) -> list[dict]:
    """Lift every 2D detection to a 3D point set and fuse lifts of the same object across views."""
    xyz = splat["xyz"]
    sel_mask = splat["roi"] & (splat["opacity"] > 0.2) & (splat["scale"] < 0.2) & (xyz[:, 1] > 0.05)
    base = np.where(sel_mask)[0]
    pts = xyz[base]
    lifts = []
    for cam in cams:
        dets = detections.get(cam["file"]) or []
        if not dets:
            continue
        vg = ViewGeometry(pts, cam)
        for det in dets:
            ymin, xmin, ymax, xmax = det["box_2d"]
            box = (xmin / 1000 * cam["W"], ymin / 1000 * cam["H"], xmax / 1000 * cam["W"], ymax / 1000 * cam["H"])
            if box[2] - box[0] < 6 or box[3] - box[1] < 6:
                continue
            loc = vg.lift(box, pts)
            if loc is None:
                continue
            p = pts[loc]
            lo, hi = _aabb(p)
            if (hi - lo).max() > MAX_EXTENT or (hi - lo).max() < 0.04:
                continue
            lifts.append({"label": det["label"], "splats": set(base[loc].tolist()), "lo": lo, "hi": hi,
                          "views": {cam["file"]}, "votes": Counter({det["label"]: 1}),
                          "names": Counter({det.get("name") or "": 1})})
    fused = []
    for L in sorted(lifts, key=lambda x: -len(x["splats"])):
        for F in fused:
            same_kind = F["label"] == L["label"]
            ctr = (L["lo"] + L["hi"]) / 2
            iou = _iou(F["lo"], F["hi"], L["lo"], L["hi"])
            # same object seen from another view; the label may differ between views (votes decide)
            if (same_kind and (iou > 0.2 or _inside(ctr, F["lo"], F["hi"]))) or iou > 0.4:
                F["splats"] |= L["splats"]; F["views"] |= L["views"]
                F["votes"] += L["votes"]; F["names"] += L["names"]
                F["label"] = F["votes"].most_common(1)[0][0]
                P = xyz[list(F["splats"])]
                F["lo"], F["hi"] = _aabb(P)
                break
        else:
            fused.append(dict(L))
    for F in fused:
        F["name"] = next((n for n, _ in F["names"].most_common() if n), "")
    return fused


def _share_inside(a: dict, lo, hi, pad: float = 0.10) -> float:
    """Share of object a's box volume inside the (padded) detection box."""
    alo, ahi = np.array(a["aabb_min"]), np.array(a["aabb_max"])
    inter = np.prod(np.clip(np.minimum(ahi, hi + pad) - np.maximum(alo, lo - pad), 0, None))
    return float(inter / max(np.prod(np.maximum(ahi - alo, 1e-6)), 1e-9))


def _display(name: str, label: str) -> str:
    return name[:1].upper() + name[1:] if name and len(name) <= 32 else label


def integrate(splat: dict, perc: dict, fused: list[dict], classes: dict, protected) -> dict:
    """Merge fragments, confirm/relabel existing objects and add the ones clustering missed. Mutates perc."""
    assets, idx = perc["assets"], perc["splat_asset_idx"]
    next_id = 1 + max(int(a["id"].split("-")[1]) for a in assets)
    confirmed = relabelled = added = merged = 0
    removed = set()
    xyz, rgb, op = splat["xyz"], splat["rgb"], splat["opacity"]
    for F in sorted(fused, key=lambda F: (-len(F["views"]), -len(F["splats"]))):
        cls, label, _, movable_kind = classes[F["label"]]
        nice = _display(F.get("name", ""), label)
        strong = len(F["views"]) >= 2
        lo = np.array([a["aabb_min"] for a in assets]); hi = np.array([a["aabb_max"] for a in assets])
        live = [k for k in range(len(assets)) if k not in removed]
        # 1. one physical body seen whole in the photos, split into fragments by clustering -> merge
        if F["label"] in SINGLE_BODY and strong and (F["hi"] - F["lo"]).max() <= SINGLE_BODY[F["label"]]:
            vol = np.prod(np.maximum(F["hi"] - F["lo"], 1e-6))

            def agrees(a):                       # same kind, or no confident label of its own
                return (a["class"] == cls or a["class"] in UNSURE or a.get("label_source") == "geometric_rules"
                        or a.get("class_confidence", 1.0) < 0.6)
            frags = [k for k in live if _share_inside(assets[k], F["lo"], F["hi"], pad=0.04) >= 0.8
                     and np.prod(assets[k]["size"]) <= 0.7 * vol and not protected(assets[k])
                     and assets[k].get("label_source") != "cylinder_fit" and agrees(assets[k])]
            if len(frags) >= 2:
                keep = max(frags, key=lambda k: assets[k]["n_gaussians"])
                sp = np.union1d(np.array(sorted(F["splats"])), np.where(np.isin(idx, frags))[0])
                new = _build_asset_dict(int(assets[keep]["id"].split("-")[1]) - 1, xyz[sp], rgb[sp], op[sp], cls, nice,
                                        assets[keep]["tier"])
                new.update({"id": assets[keep]["id"], "geo_class": assets[keep].get("geo_class", cls),
                            "label_source": "gemini_detection", "movable": bool(movable_kind), "name": F.get("name", ""),
                            "class_label": label, "detection_views": sorted(F["views"]), "detection_label": F["label"],
                            "merged_fragments": sorted(assets[k]["id"] for k in frags),
                            "class_confidence": round(min(0.95, 0.55 + 0.1 * len(F["views"])), 2)})
                assets[keep] = new
                idx[sp] = keep
                removed |= set(frags) - {keep}
                merged += len(frags) - 1
                continue
        # 2. matches an existing object -> confirm, and relabel when the photos are the better evidence
        ious = np.array([_iou(lo[k], hi[k], F["lo"], F["hi"]) if k not in removed else 0.0 for k in range(len(assets))])
        ctr = (F["lo"] + F["hi"]) / 2
        contains = np.array([k not in removed and (_inside(ctr, lo[k], hi[k], 0.03) or _inside((lo[k] + hi[k]) / 2, F["lo"], F["hi"]))
                             for k in range(len(assets))])
        score = ious + 0.3 * contains
        j = int(np.argmax(score)) if len(score) else -1
        if j >= 0 and (ious[j] > 0.15 or contains[j]):
            a = assets[j]
            a["detection_views"] = sorted(set(a.get("detection_views", [])) | F["views"])
            a["detection_label"] = F["label"]
            confirmed += 1
            if protected(a):
                continue
            weak_label = (a.get("label_source") in ("geometric_rules", "decisions_api") or a.get("class_confidence", 0) < 0.75)
            if a["class"] == cls:
                if F.get("name"):
                    a["name"], a["label"], a["class_label"] = F["name"], nice, label
            elif weak_label and (strong or a.get("label_source") == "geometric_rules"):
                a["class"], a["label"], a["class_label"], a["label_source"] = cls, nice, label, "gemini_detection"
                a["name"] = F.get("name", "")
                a["movable"] = bool(movable_kind)
                relabelled += 1
            continue
        # 3. nothing there -> a new object the clustering missed
        n_splats = len(F["splats"])
        if not strong and n_splats < 200:
            continue                                     # single weak view: not trusted as a new object
        sp = np.array(sorted(F["splats"]))
        sp = sp[idx[sp] < 0] if (idx[sp] < 0).sum() >= MIN_PTS else sp
        new = _build_asset_dict(next_id, xyz[sp], rgb[sp], op[sp], cls, nice,
                                "overhead" if ctr[1] > C.OVERHEAD_SPLIT_Y else "equipment")
        if protected(new):
            continue
        new.update({"geo_class": cls, "label_source": "gemini_detection", "movable": bool(movable_kind),
                    "name": F.get("name", ""), "class_label": label,
                    "detection_views": sorted(F["views"]), "detection_label": F["label"],
                    "class_confidence": round(min(0.95, 0.55 + 0.1 * len(F["views"])), 2)})
        free = sp[idx[sp] < 0]
        idx[free] = len(assets)
        assets.append(new)
        next_id += 1
        added += 1
    if removed:                                          # drop merged fragments, remap splat ownership
        order = [k for k in range(len(assets)) if k not in removed]
        remap = np.full(len(assets) + 1, -1)
        remap[order] = np.arange(len(order))
        own = idx >= 0
        idx[own] = remap[idx[own]]
        perc["assets"] = [assets[k] for k in order]
    stats = {"fused_detections": len(fused), "confirmed_existing": confirmed, "merged_fragments": merged,
             "relabelled_from_detection": relabelled, "new_objects_from_detection": added}
    print(f"   [detect] {len(fused)} fused 3D detections: {merged} fragments merged into whole objects, "
          f"{confirmed} confirm existing ({relabelled} relabelled), {added} new objects added")
    return stats


def run(splat: dict, perc: dict, classes: dict, protected) -> dict:
    cams = load_cameras()
    if not cams:
        return {"skipped": "no rendered views (python -m scripts.render_views)"}
    dets = detect(cams, classes)
    if not dets:
        print("   [detect] no detections available (needs GEMINI_API_KEY or cache); skipping")
        return {"skipped": "no detections"}
    fused = lift_and_fuse(splat, cams, dets)
    return integrate(splat, perc, fused, classes, protected)
