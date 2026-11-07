"""Stage 2d - Straight pipe segments by geometry alone (cylinder fit).

Ported from Linards' pipeline/cylinders.py (SinceAI-2026/linards-project), after Qiu, Zhou & Neumann,
"Pipe-Run Extraction and Reconstruction from Point Clouds" (ECCV 2014):
  1. a flat Gaussian's thinnest axis is its surface normal;
  2. pipes run along the room's three main axes (wall yaw from horizontal normals);
  3. per axis, surface points vote for the axis position (point -/+ r * normal) for each radius;
     peaks in (position, radius) are candidate pipe axes;
  4. along a candidate, the supporting Gaussians are split at gaps; a segment is a pipe only if they
     cover a continuous arc of the circle (a wall or a box face votes from one side only).
Here, in our levelled frame, each segment then either gives an existing object its true pipe axis and
radius (asset["cylinder"]) or, if clustering missed it, becomes a new framed pipe asset.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from . import config as C
from .ingest import FLOOR_Y_PLY
from .perception import _build_asset_dict

FLAT = 0.3
MIN_OPACITY = 0.1
HORIZONTAL = 0.2
PERPENDICULAR = np.cos(np.radians(75))
CELL = 0.01
RADII = np.arange(0.02, 0.155, 0.01)
BLUR = 1.0
MIN_VOTES = 10
PEAK_WINDOW = 4
BAND = 0.25
MIN_BAND = 0.01
ALIGNED = np.cos(np.radians(30))
GAP = 0.15
MIN_LENGTH = 0.3
MIN_SUPPORT = 30
ARC_BINS = 36
BIN_SHARE = 0.25
MIN_ARC = 90
COVERED = 0.6        # share of a segment's axis inside existing boxes for it to belong to them
ROI_LO, ROI_HI = np.array([-4.75, -0.1, -3.55]), np.array([4.15, 2.25, 3.75])


def surface_normals(raw: dict):
    """Centres and unit normals (our levelled frame) of flat, solid Gaussians. `raw` = ingest.load_splat()."""
    s = np.asarray(raw["scales_3d"], float)
    q = np.asarray(raw["rots"], float)                       # PLY order rot_1, rot_2, rot_3, rot_0 -> x, y, z, w
    q /= np.linalg.norm(q, axis=1, keepdims=True) + 1e-12
    x, y, z, w = q.T
    xyz = np.asarray(raw["xyz_raw"], float)
    ours = np.c_[xyz[:, 0], FLOOR_Y_PLY - xyz[:, 1], -xyz[:, 2]]
    order = np.sort(s, 1)
    keep = ((order[:, 0] < FLAT * order[:, 1]) & (raw["opacity"] > MIN_OPACITY)
            & (ours >= ROI_LO).all(1) & (ours <= ROI_HI).all(1))
    x, y, z, w, sk = x[keep], y[keep], z[keep], w[keep], s[keep]
    R = np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], 1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], 1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], 1),
    ], 1)
    thin = np.argmin(sk, 1)
    n = R[np.arange(len(thin)), :, thin] * [1.0, -1.0, -1.0]  # same flip as the positions
    return ours[keep], n / np.linalg.norm(n, axis=1, keepdims=True)


def room_axes(normals):
    flat = normals[np.abs(normals[:, 1]) < HORIZONTAL]
    yaw = np.angle(np.exp(4j * np.arctan2(flat[:, 2], flat[:, 0])).mean()) / 4
    c, s = np.cos(yaw), np.sin(yaw)
    return {"x": np.array([c, 0, s]), "y": np.array([0.0, 1, 0]), "z": np.array([-s, 0, c])}


def _plane_basis(axis):
    helper = np.array([0.0, 1, 0]) if abs(axis[1]) < 0.9 else np.array([1.0, 0, 0])
    u = np.cross(axis, helper); u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def _vote(flat, inward):
    origin = flat.min(0) - RADII[-1] - 2 * CELL
    shape = np.ceil((flat.max(0) + RADII[-1] + 2 * CELL - origin) / CELL).astype(int)
    votes = np.zeros((len(RADII), *shape), np.float32)
    for k, r in enumerate(RADII):
        for sign in (1, -1):
            cells = np.floor((flat + sign * r * inward - origin) / CELL).astype(int)
            votes[k] += np.bincount(np.ravel_multi_index(cells.T, shape), minlength=votes[k].size).reshape(shape)
    return ndimage.gaussian_filter(votes, (0, BLUR, BLUR)), origin


def _arc_degrees(angles):
    counts = np.bincount(((angles + np.pi) / (2 * np.pi) * ARC_BINS).astype(int) % ARC_BINS, minlength=ARC_BINS)
    covered = counts >= BIN_SHARE * len(angles) / ARC_BINS
    if covered.all():
        return 360
    run = best = 0
    for c in np.concatenate([covered, covered]):
        run = run + 1 if c else 0
        best = max(best, run)
    return best * 360 / ARC_BINS


def _segments_along(name, axis, centres, normals):
    u, v = _plane_basis(axis)
    across = np.abs(normals @ axis) < PERPENDICULAR
    pts, n = centres[across], normals[across]
    flat = np.stack([pts @ u, pts @ v], 1)
    inward = np.stack([n @ u, n @ v], 1)
    inward /= np.linalg.norm(inward, axis=1, keepdims=True) + 1e-12
    along = pts @ axis
    votes, origin = _vote(flat, inward)
    window = (len(RADII), 2 * PEAK_WINDOW + 1, 2 * PEAK_WINDOW + 1)
    peaks = np.argwhere((votes == ndimage.maximum_filter(votes, window)) & (votes >= MIN_VOTES))
    tree = cKDTree(flat)
    found = []
    for k, i, j in peaks:
        r = RADII[k]
        centre = origin + (np.array([i, j]) + 0.5) * CELL
        band = max(BAND * r, MIN_BAND)
        near = np.array(tree.query_ball_point(centre, r + band), int)
        if len(near) < MIN_SUPPORT:
            continue
        off = flat[near] - centre
        dist = np.linalg.norm(off, axis=1)
        radial = off / np.maximum(dist, 1e-9)[:, None]
        on = near[(dist > r - band) & (np.abs(np.sum(radial * inward[near], 1)) > ALIGNED)]
        if len(on) < MIN_SUPPORT:
            continue
        order = on[np.argsort(along[on])]
        for piece in np.split(order, np.flatnonzero(np.diff(along[order]) > GAP) + 1):
            a0, a1 = along[piece[0]], along[piece[-1]]
            if a1 - a0 < MIN_LENGTH or len(piece) < MIN_SUPPORT:
                continue
            o = flat[piece] - centre
            arc = _arc_degrees(np.arctan2(o[:, 1], o[:, 0]))
            if arc < MIN_ARC:
                continue
            base = centre[0] * u + centre[1] * v
            found.append({"axis": name, "direction": axis, "start": base + a0 * axis, "end": base + a1 * axis,
                          "radius": float(r), "length": float(a1 - a0), "gaussians": len(piece), "arc": float(arc),
                          "centre": centre, "span": (a0, a1)})
    kept = []                                               # one fat pipe gives several neighbouring peaks
    for sgm in sorted(found, key=lambda s: -s["gaussians"]):
        if not any(np.linalg.norm(sgm["centre"] - o["centre"]) < max(sgm["radius"], o["radius"])
                   and min(sgm["span"][1], o["span"][1]) > max(sgm["span"][0], o["span"][0]) for o in kept):
            kept.append(sgm)
    return kept


def fit_segments(raw: dict) -> list[dict]:
    centres, normals = surface_normals(raw)
    segs = []
    for name, axis in room_axes(normals).items():
        segs += _segments_along(name, axis, centres, normals)
    return segs


def _cyl(s):
    return {"start": [round(float(c), 3) for c in s["start"]], "end": [round(float(c), 3) for c in s["end"]],
            "radius": round(s["radius"], 3), "length": round(s["length"], 3), "axis": s["axis"],
            "arc_deg": round(s["arc"]), "support": int(s["gaussians"])}


def integrate(splat: dict, perc: dict, segs: list[dict], protected) -> dict:
    """Attach each cylinder to the object(s) it runs through, or add it as a new pipe asset. Mutates perc."""
    assets, idx = perc["assets"], perc["splat_asset_idx"]
    lo = np.array([a["aabb_min"] for a in assets]) - 0.03
    hi = np.array([a["aabb_max"] for a in assets]) + 0.03
    xyz, rgb, op = splat["xyz"], splat["rgb"], splat["opacity"]
    roi = np.where(splat["roi"] & (op > 0.1))[0]
    tree = cKDTree(xyz[roi])
    next_id = 1 + max(int(a["id"].split("-")[1]) for a in assets)
    attached = added = 0
    movable = np.array([bool(a.get("movable")) for a in assets])
    skipped = {"floor": 0, "movable": 0}
    for s in sorted(segs, key=lambda s: -s["length"]):
        t = np.linspace(0, 1, max(6, int(s["length"] / 0.05)))[:, None]
        samples = s["start"] + t * (s["end"] - s["start"])
        if s["axis"] != "y" and samples[:, 1].mean() < s["radius"] + 0.05:
            skipped["floor"] += 1                     # floor tape, cracks, tank bottoms: not a pipe lying there
            continue
        inside = ((samples[:, None, :] >= lo[None]) & (samples[:, None, :] <= hi[None])).all(-1)   # (S, A)
        if inside[:, movable[: inside.shape[1]]].any(1).mean() >= 0.3:
            skipped["movable"] += 1                   # ladder rails, cart frames: already a movable object
            continue
        if inside.any(1).mean() >= COVERED:
            j = int(np.argmax(inside.sum(0)))
            a = assets[j]
            if a["class"] in ("vertical_pipe", "horizontal_pipe", "overhead_pipe", "pipe_fitting") or \
                    a.get("label_source") == "geometric_rules":
                if s["length"] > a.get("cylinder", {}).get("length", 0):
                    a["cylinder"] = _cyl(s)
                attached += 1
            continue
        mid = (s["start"] + s["end"]) / 2
        near = np.array(tree.query_ball_point(mid, s["length"] / 2 + s["radius"] + 0.03), int)
        if not len(near):
            continue
        p = xyz[roi[near]]
        d = s["end"] - s["start"]; L = np.linalg.norm(d); d = d / L
        tt = (p - s["start"]) @ d
        radial = np.linalg.norm(p - s["start"] - np.outer(tt, d), axis=1)
        mem = roi[near][(tt >= -0.02) & (tt <= L + 0.02) & (radial <= s["radius"] + 0.03)]
        free = mem[idx[mem] < 0]
        if len(free) < MIN_SUPPORT:
            continue
        if s["axis"] == "y":
            cls, label = "vertical_pipe", "Vertical Pipe Riser"
        elif mid[1] > C.OVERHEAD_SPLIT_Y:
            cls, label = "overhead_pipe", "Overhead Pipe Run"
        else:
            cls, label = "horizontal_pipe", "Horizontal Pipe Run"
        new = _build_asset_dict(next_id, xyz[mem], rgb[mem], op[mem], cls, label,
                                "overhead" if mid[1] > C.OVERHEAD_SPLIT_Y else "equipment")
        if protected(new):
            continue
        new.update({"geo_class": cls, "label_source": "cylinder_fit", "movable": False, "cylinder": _cyl(s),
                    "class_confidence": round(min(0.9, 0.5 + s["arc"] / 720), 2)})
        idx[free] = len(assets)
        assets.append(new)
        movable = np.r_[movable, False]
        lo = np.vstack([lo, np.array(new["aabb_min"]) - 0.03]); hi = np.vstack([hi, np.array(new["aabb_max"]) + 0.03])
        next_id += 1
        added += 1
    stats = {"segments": len(segs), "attached_to_existing": attached, "new_pipe_assets": added, "rejected": skipped}
    print(f"   [pipes] {len(segs)} fitted pipe cylinders: {attached} give existing objects a true axis, "
          f"{added} new pipe assets (rejected {skipped})")
    return stats
