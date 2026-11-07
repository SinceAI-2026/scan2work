"""Stage 2b - Image-grounded semantic labelling, movable detection and object clean-up.

1. Every extracted cluster is rendered as a two-view crop (front + top-down; its own Gaussians in
   full colour inside a yellow frame, surroundings dimmed) and sent to the OpenAI Decisions API with
   two questions: a `choice` over equipment / movable-object classes, and a `predicate` "movable?".
2. Clusters the model calls scan clutter are dropped; touching clusters of the same structural class
   (pipe runs, tanks, cabinets, structure) are merged into one object, and their boxes refit.
Without an API key (and no cache) the geometric labels from perception.py are kept unchanged.
"""
from __future__ import annotations

import base64
import math
import io
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw

from . import config as C
from . import decisions_client as D
from . import gemini_labels as G
from . import walk_photos as WP
from .perception import _build_asset_dict

# value -> (internal class, display label, description for the model, movable by nature)
CLASSES = {
    "control_valve": ("control_valve", "Control / Isolation Valve", "Valve body with handwheel, lever or actuator on a pipe.", False),
    "pump": ("circulation_pump", "Circulation Pump", "Inline or base-mounted pump, usually with a motor head, on pipework.", False),
    "electric_motor": ("electric_motor", "Electric Motor", "Cylindrical finned motor housing, often driving a pump.", False),
    "instrument": ("pressure_sensor", "Instrument / Gauge / Transmitter", "Small gauge, transmitter, sensor or thermometer.", False),
    "tank_vessel": ("expansion_vessel", "Tank / Expansion Vessel", "Large closed vessel, expansion tank or boiler drum.", False),
    "heat_exchanger": ("heat_exchanger", "Heat Exchanger", "Plate or shell-and-tube heat exchanger.", False),
    "electrical_cabinet": ("electrical_cabinet", "Electrical / Control Cabinet", "Flat-fronted box, switchboard or control panel.", False),
    "pipe_vertical": ("vertical_pipe", "Vertical Pipe Riser", "Straight vertical pipe or insulated riser.", False),
    "pipe_horizontal": ("horizontal_pipe", "Horizontal Pipe Run", "Straight horizontal pipe, header or manifold.", False),
    "pipe_fitting": ("pipe_fitting", "Pipe Fitting (elbow/tee/flange)", "Elbow, tee, reducer, flange or strainer joining pipes.", False),
    "appliance": ("appliance", "Appliance (washing machine / dryer / fridge)", "Washing machine, dryer, fridge, water heater or similar white-goods box with a door or control panel.", False),
    "sink": ("sink", "Sink / Washbasin", "Wall or floor sink, washbasin or drain tray with a tap.", False),
    "shelf_rack": ("shelf_rack", "Shelf / Storage Rack", "Shelving unit, wire rack, wall shelf or storage cage.", False),
    "structure": ("structural_column", "Structure (wall/column/beam)", "Wall section, column, beam or support frame.", False),
    "chair_furniture": ("movable_furniture", "Chair / Table (movable)", "Chair, stool, table or workbench that can be moved.", True),
    "cart_trolley": ("movable_cart", "Cart / Trolley (movable)", "Wheeled cart, roll cage, trolley, hand truck or pallet jack (not a vacuum cleaner).", True),
    "container": ("movable_container", "Bucket / Bin / Canister (movable)", "Bucket, bin, canister, jerrycan, drum or tote.", True),
    "box_crate": ("movable_crate", "Box / Crate / Pallet (movable)", "Cardboard box, crate, pallet or toolbox.", True),
    "ladder": ("movable_ladder", "Ladder (movable)", "Free-standing or leaning ladder or step stool.", True),
    "cleaning_equipment": ("movable_cleaning", "Cleaning Equipment (movable)", "Broom, mop, dustpan, squeegee, mop bucket or wet/dry vacuum cleaner.", True),
    "hose_cable": ("movable_hose", "Hose / Cable (movable)", "Loose hose, cable reel or extension lead.", True),
    "clutter": ("clutter", "Scan clutter", "No real object: reflections, floaters, empty space or a fragment of floor/wall.", False),
    "other": ("industrial_asset", "Unclassified Object", "A real object that fits none of the above.", False),
}
MERGEABLE = {"vertical_pipe", "horizontal_pipe", "overhead_pipe", "pipe_fitting", "expansion_vessel", "appliance", "shelf_rack",
             "structural_column", "electrical_cabinet"}
IMG = 224
CACHE_VERSION = "label-v2"
MOVABLE_MIN_CONF = 0.25   # min confidence of a movable class (calibrated on tests/gold_labels.json)
CLUTTER_MIN_CONF = 0.28   # "clutter" as top answer at/above this -> object dropped


def _paint(img, pts, cols, sc, px_per_m, half, cx, cy, cz, u_axis, d, top: bool, dim: float, gain: float):
    if len(pts) == 0:
        return
    rel = pts - np.array([cx, cy, cz])
    u = rel[:, [0, 2]] @ u_axis
    depth = rel[:, [0, 2]] @ d
    if top:  # looking down: image-y is distance from room centre, paint lowest first
        v, order = depth, np.argsort(rel[:, 1])
    else:    # looking outward from room centre: paint farthest first
        v, order = -rel[:, 1], np.argsort(depth)[::-1]
    px = ((u + half) * px_per_m).astype(int)[order]
    py = ((v + half) * px_per_m).astype(int)[order]
    rad = np.clip(sc[order] * px_per_m, 1, 4).astype(int)
    c = (np.clip(cols[order] * gain, 0, 1) * 255 * dim).astype(np.uint8)
    for ox in range(-3, 4):
        for oy in range(-3, 4):
            x, y = px + ox, py + oy
            ok = (np.maximum(abs(ox), abs(oy)) <= rad) & (x >= 0) & (x < IMG) & (y >= 0) & (y < IMG)
            img[y[ok], x[ok]] = c[ok]


def _render_crop(asset: dict, member, ctx) -> str:
    """member/ctx = (xyz, rgb, scale_m). Returns a PNG data-URL: [front view | top view]."""
    cx, cy, cz = asset["center"]
    d = np.array([cx, cz], dtype=np.float64)
    d = d / n if (n := np.linalg.norm(d)) > 0.3 else np.array([0.0, 1.0])
    u_axis = np.array([-d[1], d[0]])
    half = max(asset["size"]) / 2 * 1.6 + 0.08
    px_per_m = IMG / (2 * half)
    gain = 1.0 / max(0.25, float(np.percentile(member[1].max(1), 90))) if len(member[0]) else 1.0

    panels = []
    for top in (False, True):
        img = np.full((IMG, IMG, 3), 24, dtype=np.uint8)
        for (pts, cols, sc), dim in ((ctx, 0.45), (member, 1.0)):
            _paint(img, pts, cols, sc, px_per_m, half, cx, cy, cz, u_axis, d, top, dim, gain)
        pil = Image.fromarray(img)
        if len(member[0]):
            rel = member[0] - np.array([cx, cy, cz])
            u = rel[:, [0, 2]] @ u_axis
            v = (rel[:, [0, 2]] @ d) if top else -rel[:, 1]
            x0, x1 = (np.percentile(u, [2, 98]) + half) * px_per_m
            y0, y1 = (np.percentile(v, [2, 98]) + half) * px_per_m
            ImageDraw.Draw(pil).rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], outline=(255, 214, 0), width=2)
        ImageDraw.Draw(pil).text((6, 4), "TOP" if top else "FRONT", fill=(200, 200, 200))
        panels.append(pil)
    both = Image.new("RGB", (IMG * 2 + 4, IMG), (60, 60, 60))
    both.paste(panels[0], (0, 0))
    both.paste(panels[1], (IMG + 4, 0))
    buf = io.BytesIO()
    both.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def label_key(asset: dict) -> str:
    geo = ",".join(f"{v:.3f}" for v in (*asset["center"], *asset["size"]))
    return f"{CACHE_VERSION}:{C.DECISIONS_MODEL}:{asset['id']}:{geo}"


CROP_DIR = C.OUT_DIR / "crops"
_crop_index = None


def rendered_crop(asset: dict) -> str | None:
    """Photo-like Gaussian-splat render from scripts/render_crops.py, if it matches this object's geometry."""
    global _crop_index
    if _crop_index is None:
        try:
            import json as _json
            _crop_index = _json.loads((CROP_DIR / "index.json").read_text())
        except (OSError, ValueError):
            _crop_index = {}
    f = CROP_DIR / f"{asset['id']}.jpg"
    if _crop_index.get(asset["id"]) != label_key(asset) or not f.exists():
        return None
    return "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()


def _classify(asset: dict, image_url: str):
    sx, sy, sz = asset["size"]
    rendered = image_url.startswith("data:image/jpeg")
    view_text = ("A rendered view (real Gaussian-splat rendering) of part of a 3D scan of an industrial boiler / "
                 "equipment room. Classify the object inside the yellow 3D box; everything else is context. "
                 if rendered else
                 "Two orthographic views (left FRONT, right TOP-DOWN) of one object from a 3D Gaussian-splat "
                 "scan of an industrial boiler / equipment room. The object is shown in full colour inside the "
                 "yellow frame; dimmed points are its surroundings. ")
    answers = D.decide(
        cache_key=("render-" if rendered else "") + label_key(asset),
        input_=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": view_text + (
                    f"Object size {sx:.2f} x {sy:.2f} x {sz:.2f} m (W x H x D), centre {asset['center'][1]:.2f} m above floor."
                )},
                {"type": "input_image", "image_url": image_url},
            ],
        }],
        questions=[
            {"type": "choice", "name": "equipment_class",
             "instructions": "What is the framed object? Splat scans are noisy; judge shape, size, height and context. "
                             "Use 'clutter' only if there is no real object inside the frame.",
             "choices": [{"value": k, "description": v[2]} for k, v in CLASSES.items()]},
            {"type": "predicate", "name": "movable",
             "instructions": "Could one or two people move this object by hand without tools, so it may be somewhere "
                             "else next time (unlike pipes, pumps, valves, tanks or wall-mounted equipment)?"},
        ],
    )
    return answers or {}


def _member_crops(splat, perc, assets):
    idx = perc["splat_asset_idx"]
    keep = splat["roi"] & (splat["opacity"] > 0.12)
    xyz, rgb, sc, own = splat["xyz"][keep], splat["rgb"][keep], splat["scale"][keep], idx[keep]
    jobs = []
    for i, a in enumerate(assets):
        if a["tier"] not in C.DECISIONS_LABEL_TIER:
            continue
        m = max(a["size"]) * 0.8 + 0.08
        box = (xyz >= np.array(a["aabb_min"]) - m).all(1) & (xyz <= np.array(a["aabb_max"]) + m).all(1)
        mem, ctx = box & (own == i), box & (own != i)
        jobs.append((a, (xyz[mem], rgb[mem], sc[mem]), (xyz[ctx], rgb[ctx], sc[ctx])))
    return jobs


def relabel_assets(splat: dict, perc: dict) -> dict:
    """Mutates perc['assets'] in place (labels, movable flag). Returns stats."""
    assets = perc["assets"]
    for a in assets:
        a["geo_class"] = a["class"]
        a["label_source"] = "geometric_rules"
        a["movable"] = False
    if not D.enabled() and not G.enabled():
        print("   [decisions] OPENAI_API_KEY not set and no cache: keeping geometric labels")
        return {"relabelled": 0, "source": "geometric_rules"}

    jobs = [(a, rendered_crop(a) or _render_crop(a, mem, ctx)) for a, mem, ctx in _member_crops(splat, perc, assets)]
    n_rendered = sum(j[1].startswith("data:image/jpeg") for j in jobs)
    print(f"   [decisions] {n_rendered}/{len(jobs)} objects use photo-like splat renders, rest use point crops")
    photo_jobs = []
    if C.LABEL_IMAGES in ("render", "collage") and G.enabled():
        # objects without a splat render would fall back to point crops (weak): use their real photo instead
        no_render = [a for a, u in jobs if not u.startswith("data:image/jpeg")]
        if no_render:
            WP.build(splat, no_render, label_key, out_dir=WP.LABEL_DIR, set_field=False)
            pj = [(a, u) for a, u in ((a, WP.data_url(a, label_key)) for a in no_render) if u]
            print(f"   [photos] {len(pj)}/{len(no_render)} objects without a splat render are labelled from a real photo")
            G.label_batches(pj, CLASSES, label_key, prefix=G.PHOTO_PREFIX, kind="photo", batch=C.GEMINI_PHOTO_BATCH)
    if C.LABEL_IMAGES in ("photo", "both"):
        WP.build(splat, [a for a, _ in jobs], label_key, out_dir=WP.LABEL_DIR, set_field=False)
        photo_jobs = [(a, u) for a, u in ((a, WP.data_url(a, label_key)) for a, _ in jobs) if u]
        print(f"   [photos] {len(photo_jobs)}/{len(jobs)} clusters have a real walkthrough frame for labelling")
    if G.enabled() and C.LABEL_IMAGES == "collage":    # 4-view collage: render + 2 photos + context
        urls = WP.build_collages(splat, [a for a, _ in jobs], label_key, rendered_crop)
        G.label_batches([(a, urls[a["id"]]) for a, _ in jobs if a["id"] in urls], CLASSES, label_key,
                        prefix=G.COLLAGE_PREFIX, kind="collage", batch=C.GEMINI_COLLAGE_BATCH)
        G.label_batches([j for j in jobs if j[0]["id"] not in urls], CLASSES, label_key)
    elif G.enabled():
        if photo_jobs and C.LABEL_IMAGES == "both":       # render + real photo of the same object, one answer
            render_of = {a["id"]: u for a, u in jobs}
            photo_jobs = [(a, [render_of[a["id"]], u]) for a, u in photo_jobs if render_of[a["id"]].startswith("data:image/jpeg")]
            G.label_batches(photo_jobs, CLASSES, label_key, prefix=G.BOTH_PREFIX, kind="both", batch=C.GEMINI_PHOTO_BATCH)
        elif photo_jobs:
            G.label_batches(photo_jobs, CLASSES, label_key, prefix=G.PHOTO_PREFIX, kind="photo", batch=C.GEMINI_PHOTO_BATCH)
        with_photo = {a["id"] for a, _ in photo_jobs}
        G.label_batches([j for j in jobs if j[0]["id"] not in with_photo], CLASSES, label_key)
    prefixes = {"render": (G.RENDER_PREFIX, G.PHOTO_PREFIX), "photo": (G.PHOTO_PREFIX, G.RENDER_PREFIX), "both": (G.BOTH_PREFIX, G.RENDER_PREFIX),
                "collage": (G.COLLAGE_PREFIX, G.RENDER_PREFIX, G.PHOTO_PREFIX)}.get(
        C.LABEL_IMAGES, (G.RENDER_PREFIX,))

    def pick(job):
        a, url = job
        return (G.cached_answer(a, label_key, prefixes) if G.enabled() else None) or _classify(a, url)

    with ThreadPoolExecutor(C.DECISIONS_WORKERS) as pool:
        results = list(pool.map(pick, jobs))
    D.save_cache()

    n = 0
    for (a, _), ans in zip(jobs, results):
        cls_ans = ans.get("equipment_class")
        if not cls_ans or cls_ans.get("choice") not in CLASSES:
            continue
        a["class_probabilities"] = {k: round(v, 3) for k, v in D.choice_probs(cls_ans).items() if v >= 0.05}
        p_mov = float(ans.get("movable", {}).get("probability", 0.0))
        choice = cls_ans["choice"]
        if choice == "other":  # model unsure: keep the geometric label
            continue
        cls, label, _, movable_kind = CLASSES[choice]
        if movable_kind and _protected(a):  # video-verified CV-003 valve zone: keep fixed
            continue
        conf = float(cls_ans.get("confidence", a["class_confidence"]))
        a["class"], a["label"] = cls, label
        a["class_confidence"] = round(conf, 2)
        # The standalone "movable?" predicate proved uninformative on this scan (median p=0.62 even for
        # pipes), so it is recorded but only a confidently chosen movable CLASS marks an object movable.
        a["movable"] = bool(movable_kind and conf >= MOVABLE_MIN_CONF)
        a["movable_probability"] = round(p_mov, 2)
        a["label_source"] = "gemini" if cls_ans.get("source", "").startswith("gemini") else "decisions_api"
        a["label_image"] = cls_ans.get("image", "render")
        n += 1
    n_gem = sum(a.get("label_source") == "gemini" for a in assets)
    print(f"   [labels] {n_gem} labelled by Gemini, {n - n_gem} by the Decisions API")
    print(f"   [decisions] relabelled {n}/{len(jobs)} clusters from two-view crops "
          f"({sum(a['movable'] for a in assets)} movable)")
    return {"relabelled": n, "candidates": len(jobs), "source": "decisions_api" if n else "geometric_rules"}


def _protected(a: dict) -> bool:
    """Never drop/merge candidates in the video-verified demo target zone (C.TARGET_ZONE, CV-003)."""
    lo, hi = C.TARGET_ZONE
    return all(l <= c <= h for c, l, h in zip(a["center"], lo, hi))


def merge_and_clean(splat: dict, perc: dict, gap: float = 0.03, max_extent: float = 2.6) -> dict:
    """Drop AI-flagged clutter and merge touching same-class structural fragments. Keeps
    perc['splat_asset_idx'] consistent with the new perc['assets'] list."""
    assets = perc["assets"]
    idx = perc["splat_asset_idx"]
    n = len(assets)
    drop = {i for i, a in enumerate(assets)
            if a["class"] == "clutter" and a.get("class_confidence", 0) >= CLUTTER_MIN_CONF and not _protected(a)}
    for i, a in enumerate(assets):  # low-confidence "clutter" keeps its geometric label instead
        if a["class"] == "clutter" and i not in drop:
            a["class"], a["label_source"] = a["geo_class"], "geometric_rules"

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    lo = np.array([a["aabb_min"] for a in assets]); hi = np.array([a["aabb_max"] for a in assets])
    for i in range(n):
        ai = assets[i]
        if i in drop or ai["class"] not in MERGEABLE or ai["label_source"] not in ("decisions_api", "gemini") or _protected(ai):
            continue
        for j in range(i + 1, n):
            aj = assets[j]
            if j in drop or aj["class"] != ai["class"] or aj["label_source"] not in ("decisions_api", "gemini") or _protected(aj):
                continue
            if np.all(lo[j] <= hi[i] + gap) and np.all(lo[i] <= hi[j] + gap):
                ri, rj = find(i), find(j)
                ext = np.maximum(hi[ri], hi[rj]) - np.minimum(lo[ri], lo[rj])
                if ri != rj and ext.max() <= max_extent:
                    parent[rj] = ri
                    lo[ri], hi[ri] = np.minimum(lo[ri], lo[rj]), np.maximum(hi[ri], hi[rj])

    groups = {}
    for i in range(n):
        if i not in drop:
            groups.setdefault(find(i), []).append(i)

    new_assets, remap = [], np.full(n + 1, -1, dtype=np.int32)
    xyz, rgb, op = splat["xyz"], splat["rgb"], splat["opacity"]
    merged = 0
    for root in sorted(groups):
        members = groups[root]
        base = assets[root]
        k = len(new_assets)
        for m in members:
            remap[m] = k
        if len(members) > 1:
            sel = np.isin(idx, members)
            refit = _build_asset_dict(0, xyz[sel], rgb[sel], op[sel], base["class"], base["label"], base["tier"])
            base = dict(base)
            base.update({k_: v for k_, v in refit.items() if k_ not in ("id", "class", "label", "class_confidence")})
            base["merged_from"] = [assets[m]["id"] for m in members]
            merged += len(members) - 1
        new_assets.append(base)

    valid = idx >= 0
    idx[valid] = remap[idx[valid]]
    perc["assets"] = new_assets
    perc["splat_asset_idx"] = idx
    stats = {"dropped_clutter": len(drop), "merged_fragments": merged, "objects_after_cleanup": len(new_assets)}
    print(f"   [cleanup] dropped {len(drop)} clutter, merged {merged} fragments -> {len(new_assets)} objects")
    return stats


def consolidate_target(perc: dict) -> dict:
    """The demo target (C.TARGET_ANCHOR) is one physical valve; clustering splits it into nested fragments.
    Fold every smaller object whose centre lies inside the target's box into the target. Mutates perc."""
    assets, idx = perc["assets"], perc["splat_asset_idx"]
    zone = [k for k, a in enumerate(assets) if _protected(a)]
    if not zone:
        return {"absorbed": 0}
    t = min(zone, key=lambda k: math.dist(assets[k]["center"], C.TARGET_ANCHOR))
    lo, hi = np.array(assets[t]["aabb_min"]), np.array(assets[t]["aabb_max"])
    vol = np.prod(assets[t]["size"])
    gone = [k for k, a in enumerate(assets) if k != t and np.prod(a["size"]) < vol
            and (np.array(a["center"]) >= lo).all() and (np.array(a["center"]) <= hi).all()]
    if gone:
        idx[np.isin(idx, gone)] = t
        keep = [k for k in range(len(assets)) if k not in set(gone)]
        remap = np.full(len(assets), -1); remap[keep] = np.arange(len(keep))
        own = idx >= 0
        idx[own] = remap[idx[own]]
        assets[t]["merged_fragments"] = sorted(assets[k]["id"] for k in gone)
        perc["assets"] = [assets[k] for k in keep]
    print(f"   [target] {assets[t]['id']} is the video-verified CV-003 valve; folded {len(gone)} nested fragments into it")
    return {"target_object": assets[t]["id"], "absorbed": len(gone)}
