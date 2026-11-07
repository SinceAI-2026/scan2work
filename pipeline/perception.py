"""Stage 2 - Multi-Pass Chromatic + Geometric 3D Asset Extraction on `Equipment Room.ply`.

Segments the full 8.6m x 7.0m Equipment Room (1,676,481 Gaussians):
  - Pass 1A: Red inline circulation pumps (Grundfos MAGNA3), red isolation valves & red Reflex expansion vessels
  - Pass 1B: Blue flanged control/butterfly valves, blue motorized box actuators & blue expansion tank
  - Pass 1C: Green flow indicator bands & isolation valves
  - Pass 1D: Dark metallic pump motors, actuator housings & pressure gauges
  - Pass 2:  Electrical control cabinets, switchboards & vertical pipe risers (with KMeans subdivision)
  - Pass 3:  Overhead insulated pipe manifolds
"""
from __future__ import annotations

import math
import numpy as np
from sklearn.cluster import DBSCAN, KMeans
from . import config as C


def _compute_obb_2d(pts: np.ndarray) -> dict:
    p_min = np.percentile(pts, 3, axis=0)
    p_max = np.percentile(pts, 97, axis=0)
    pad = np.array([0.025, 0.025, 0.025], dtype=float)
    aabb_min = p_min - pad
    aabb_max = p_max + pad
    center = 0.5 * (aabb_min + aabb_max)
    size = np.maximum(aabb_max - aabb_min, [0.06, 0.06, 0.06])

    xz = pts[:, [0, 2]] - center[[0, 2]]
    yaw = 0.0
    if len(xz) >= 12 and max(size[0], size[2]) > 0.28:
        cov = (xz.T @ xz) / max(1, len(xz) - 1)
        eigvals, eigvecs = np.linalg.eigh(cov)
        order = np.argsort(eigvals)[::-1]
        if eigvals[order[0]] > 2.2 * max(1e-6, eigvals[order[1]]):
            v0 = eigvecs[:, order[0]]
            yaw = float(math.atan2(v0[1], v0[0]))
            if abs(yaw) < 0.22 or abs(abs(yaw) - math.pi / 2) < 0.22 or abs(abs(yaw) - math.pi) < 0.22:
                yaw = 0.0
    if yaw != 0.0:
        # extents along the object's own axes (the world-axis extents of a rotated object are too large)
        c, s = math.cos(yaw), math.sin(yaw)
        lx, lz = xz @ np.array([c, s]), xz @ np.array([-s, c])
        (x0, x1), (z0, z1) = np.percentile(lx, [3, 97]), np.percentile(lz, [3, 97])
        mx, mz = (x0 + x1) / 2, (z0 + z1) / 2
        center[0] += mx * c - mz * s
        center[2] += mx * s + mz * c
        size[0] = max(x1 - x0 + 2 * pad[0], 0.06)
        size[2] = max(z1 - z0 + 2 * pad[2], 0.06)

    return {
        "center": [round(float(x), 3) for x in center],
        "size": [round(float(x), 3) for x in size],
        "yaw": round(float(yaw), 4),
        "aabb_min": [round(float(x), 3) for x in aabb_min],
        "aabb_max": [round(float(x), 3) for x in aabb_max],
        "volume_m3": round(float(size[0] * size[1] * size[2]), 4),
    }


def _build_asset_dict(
    idx: int,
    pts: np.ndarray,
    cols: np.ndarray,
    ops: np.ndarray,
    cls: str,
    label: str,
    tier: str,
    conf_boost: float = 0.0,
) -> dict:
    obb = _compute_obb_2d(pts)
    mean_rgb = cols.mean(axis=0)
    n_pts = len(pts)
    density = min(1.0, math.log10(max(10, n_pts)) / 3.1)
    conf = float(np.clip(0.62 + 0.22 * density + 0.10 * float(ops.mean()) + conf_boost, 0.65, 0.98))

    return {
        "id": f"OBJ-{idx:03d}",
        "tier": tier,
        "class": cls,
        "label": label,
        "class_confidence": round(conf, 2),
        "n_gaussians": int(n_pts),
        "mean_rgb": [round(float(x), 3) for x in mean_rgb],
        **obb,
    }


def extract_assets(splat: dict) -> dict:
    xyz_all = splat["xyz"]
    rgb_all = splat["rgb"]
    op_all = splat["opacity"]
    sc_all = splat["scale"]

    # Interior room mask covering the full 8.6m x 7.0m Equipment Room
    # Excludes floor slab (Y < 0.10m), ceiling slab (Y > 2.00m), and outer perimeter brick walls
    in_room = (
        (xyz_all[:, 0] > -4.48) & (xyz_all[:, 0] < 3.88)
        & (xyz_all[:, 1] > 0.10) & (xyz_all[:, 1] < 2.00)
        & (xyz_all[:, 2] > -3.28) & (xyz_all[:, 2] < 3.46)
        & (op_all >= 0.20)
        & (sc_all <= 0.18)
    )

    room_indices = np.where(in_room)[0]
    xyz = xyz_all[room_indices]
    rgb = rgb_all[room_indices]
    op = op_all[room_indices]

    used = np.zeros(len(xyz), dtype=bool)
    splat_asset_idx = np.full(len(xyz_all), -1, dtype=np.int32)
    proposals = []

    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    luma = 0.299 * r + 0.587 * g + 0.114 * b

    def add_proposal(local_idx: np.ndarray, cls: str, label: str, tier: str, boost: float = 0.0):
        pts = xyz[local_idx]
        cols = rgb[local_idx]
        ops = ops_arr = op[local_idx]
        aid_num = len(proposals) + 1
        prop = _build_asset_dict(aid_num, pts, cols, ops_arr, cls, label, tier, boost)
        proposals.append((prop, room_indices[local_idx]))

    # -------------------------------------------------------------------------
    # PASS 1A: Red Circulation Pumps (Grundfos MAGNA3), Red Valves & Red Reflex Tanks
    # -------------------------------------------------------------------------
    red_mask = (r > 0.45) & ((r - np.maximum(g, b)) > 0.13) & (xyz[:, 1] > 0.14) & (xyz[:, 1] < 1.82)
    red_idx = np.where(red_mask)[0]
    if len(red_idx) >= 18:
        labels = DBSCAN(eps=0.09, min_samples=14).fit_predict(xyz[red_idx])
        for cid in sorted(set(labels)):
            if cid < 0:
                continue
            c_idx = red_idx[labels == cid]
            pts = xyz[c_idx]
            sz = np.percentile(pts, 97, axis=0) - np.percentile(pts, 3, axis=0)
            if sz.max() > 1.35 or sz.max() < 0.05:
                continue
            used[c_idx] = True
            if sz[1] > 0.65 and sz[0] > 0.30:
                add_proposal(c_idx, "expansion_vessel", "Pressure Expansion Vessel (Reflex)", "equipment", 0.14)
            else:
                add_proposal(c_idx, "circulation_pump", "Inline Circulation Pump (Grundfos)", "equipment", 0.14)

    # -------------------------------------------------------------------------
    # PASS 1B: Blue Flanged Control Valves, Motorized Box Actuators & Blue Tank
    # -------------------------------------------------------------------------
    blue_mask = (
        ~used
        & (((b > 0.36) & ((b - r) > 0.09)) | ((b > 0.32) & (g > 0.30) & ((b - r) > 0.11)))
        & (xyz[:, 1] > 0.14) & (xyz[:, 1] < 1.82)
    )
    blue_idx = np.where(blue_mask)[0]
    if len(blue_idx) >= 14:
        labels = DBSCAN(eps=0.09, min_samples=12).fit_predict(xyz[blue_idx])
        for cid in sorted(set(labels)):
            if cid < 0:
                continue
            c_idx = blue_idx[labels == cid]
            pts = xyz[c_idx]
            sz = np.percentile(pts, 97, axis=0) - np.percentile(pts, 3, axis=0)
            if sz.max() > 1.30 or sz.max() < 0.04:
                continue
            used[c_idx] = True
            if sz[1] > 0.70 and sz[0] > 0.28:
                add_proposal(c_idx, "expansion_vessel", "Hydronic Buffer / Expansion Tank", "equipment", 0.14)
            else:
                add_proposal(c_idx, "control_valve", "Automated Control Valve", "equipment", 0.15)

    # -------------------------------------------------------------------------
    # PASS 1C: Green Pipe Flow Indicator Bands & Isolation Valves
    # -------------------------------------------------------------------------
    green_mask = (
        ~used
        & (g > 0.40) & ((g - r) > 0.08) & ((g - b) > 0.07)
        & (xyz[:, 1] > 0.22) & (xyz[:, 1] < 1.88)
    )
    green_idx = np.where(green_mask)[0]
    if len(green_idx) >= 14:
        labels = DBSCAN(eps=0.09, min_samples=12).fit_predict(xyz[green_idx])
        for cid in sorted(set(labels)):
            if cid < 0:
                continue
            c_idx = green_idx[labels == cid]
            pts = xyz[c_idx]
            ctr = pts.mean(axis=0)
            # Exclude green plastic office chair near (0.88, 0.53, -1.05)
            if abs(ctr[0] - 0.88) < 0.38 and abs(ctr[2] - (-1.05)) < 0.38:
                used[c_idx] = True
                continue
            sz = np.percentile(pts, 97, axis=0) - np.percentile(pts, 3, axis=0)
            if sz.max() > 0.65 or sz.max() < 0.04:
                continue
            used[c_idx] = True
            add_proposal(c_idx, "control_valve", "Flow Direction & Valve Assembly", "equipment", 0.10)

    # -------------------------------------------------------------------------
    # PASS 1D: Dark Metallic Pump Motors, Valve Bodies & Gauges
    # -------------------------------------------------------------------------
    dark_mask = (
        ~used
        & (luma < 0.24)
        & (xyz[:, 1] > 0.18) & (xyz[:, 1] < 1.72)
        & (op > 0.30)
    )
    dark_idx = np.where(dark_mask)[0]
    if len(dark_idx) >= 24:
        labels = DBSCAN(eps=0.075, min_samples=18).fit_predict(xyz[dark_idx])
        for cid in sorted(set(labels)):
            if cid < 0:
                continue
            c_idx = dark_idx[labels == cid]
            pts = xyz[c_idx]
            sz = np.percentile(pts, 97, axis=0) - np.percentile(pts, 3, axis=0)
            if sz.max() > 0.70 or sz.max() < 0.05:
                continue
            used[c_idx] = True
            if sz.max() < 0.17:
                add_proposal(c_idx, "pressure_sensor", "Pressure / Vibration Transmitter", "equipment", 0.09)
            elif sz[1] > 0.21:
                add_proposal(c_idx, "electric_motor", "Pump Drive Electric Motor", "equipment", 0.11)
            else:
                add_proposal(c_idx, "control_valve", "Motorized Valve Actuator", "equipment", 0.10)

    # -------------------------------------------------------------------------
    # PASS 2: Cabinets, Switchboards & Vertical Pipe Risers (0.15m < Y <= 1.65m)
    # -------------------------------------------------------------------------
    mid_mask = ~used & (xyz[:, 1] > 0.15) & (xyz[:, 1] <= 1.65) & (op > 0.26)
    mid_idx = np.where(mid_mask)[0]
    if len(mid_idx) > 22000:
        rng = np.random.default_rng(C.RANDOM_SEED)
        mid_sub = rng.choice(mid_idx, size=22000, replace=False)
    else:
        mid_sub = mid_idx

    if len(mid_sub) >= 40:
        labels = DBSCAN(eps=0.09, min_samples=24).fit_predict(xyz[mid_sub])
        for cid in sorted(set(labels)):
            if cid < 0:
                continue
            c_idx = mid_sub[labels == cid]
            pts = xyz[c_idx]
            sz = np.percentile(pts, 97, axis=0) - np.percentile(pts, 3, axis=0)
            if sz.max() < 0.08:
                continue

            if sz.max() > 1.20:
                n_sub = max(2, min(8, int(round(len(pts) / 260))))
                km = KMeans(n_clusters=n_sub, random_state=C.RANDOM_SEED, n_init=5).fit(pts)
                sub_labels = km.labels_
            else:
                sub_labels = np.zeros(len(pts), dtype=int)

            for sid in sorted(set(sub_labels)):
                s_mask = sub_labels == sid
                if s_mask.sum() < 22:
                    continue
                s_idx = c_idx[s_mask]
                s_pts = xyz[s_idx]
                s_sz = np.percentile(s_pts, 97, axis=0) - np.percentile(s_pts, 3, axis=0)
                if s_sz.max() > 1.45 or s_sz.max() < 0.08:
                    continue

                horiz = max(s_sz[0], s_sz[2])
                if s_sz[1] > 0.45 and horiz > 0.38 and min(s_sz[0], s_sz[2]) < 0.40:
                    add_proposal(s_idx, "electrical_cabinet", "Electrical Control Cabinet", "equipment", 0.08)
                elif s_sz[1] > 1.8 * horiz and horiz < 0.30:
                    add_proposal(s_idx, "vertical_pipe", "Insulated Vertical Pipe Riser", "equipment", 0.05)
                elif s_sz.max() < 0.22:
                    add_proposal(s_idx, "pressure_sensor", "Pressure / Vibration Sensor", "equipment", 0.06)
                elif s_sz[1] > 0.26 and horiz < 0.36:
                    add_proposal(s_idx, "circulation_pump", "Inline Circulation Pump", "equipment", 0.06)
                else:
                    add_proposal(s_idx, "horizontal_pipe", "Distribution Pipe Manifold", "equipment", 0.04)

    # -------------------------------------------------------------------------
    # PASS 3: Overhead Insulated Pipe Headers (1.65m < Y < 1.98m)
    # -------------------------------------------------------------------------
    hi_mask = ~used & (xyz[:, 1] > 1.65) & (xyz[:, 1] < 1.98) & (op > 0.28)
    hi_idx = np.where(hi_mask)[0]
    if len(hi_idx) > 10000:
        rng = np.random.default_rng(C.RANDOM_SEED + 1)
        hi_sub = rng.choice(hi_idx, size=10000, replace=False)
    else:
        hi_sub = hi_idx

    if len(hi_sub) >= 35:
        labels = DBSCAN(eps=0.11, min_samples=25).fit_predict(xyz[hi_sub])
        for cid in sorted(set(labels)):
            if cid < 0:
                continue
            c_idx = hi_sub[labels == cid]
            pts = xyz[c_idx]
            sz = np.percentile(pts, 97, axis=0) - np.percentile(pts, 3, axis=0)
            if sz.max() < 0.18 or sz.max() > 1.65:
                continue
            add_proposal(c_idx, "overhead_pipe", "Overhead Insulated Steam Header", "overhead", 0.04)

    # Order proposals by equipment priority
    priority = {
        "control_valve": 0,
        "circulation_pump": 1,
        "electric_motor": 2,
        "pressure_sensor": 3,
        "expansion_vessel": 4,
        "electrical_cabinet": 5,
        "vertical_pipe": 6,
        "horizontal_pipe": 7,
        "overhead_pipe": 8,
    }
    proposals.sort(key=lambda item: (
        0 if item[0]["tier"] == "equipment" else 1,
        priority.get(item[0]["class"], 9),
        -item[0]["class_confidence"],
        -item[0]["n_gaussians"],
    ))

    assets = []
    for i, (prop, g_idx) in enumerate(proposals):
        prop["id"] = f"OBJ-{i + 1:03d}"
        splat_asset_idx[g_idx] = i
        assets.append(prop)

    # Avatar-height obstacle points (0.18m <= Y <= 1.60m) inside the Equipment Room
    obst_mask = (
        (xyz_all[:, 0] >= -4.55) & (xyz_all[:, 0] <= 3.95)
        & (xyz_all[:, 2] >= -3.35) & (xyz_all[:, 2] <= 3.55)
        & (xyz_all[:, 1] >= C.OBST_MIN_H)
        & (xyz_all[:, 1] <= C.OBST_MAX_H)
        & (op_all >= 0.25)
        & (sc_all <= 0.16)
    )
    obstacle_points = xyz_all[obst_mask]

    walls = [
        {"name": "North Process Manifold Wall", "from": [-4.55, 3.48], "to": [3.95, 3.48]},
        {"name": "South Expansion Vessel Wall", "from": [-4.55, -3.35], "to": [3.95, -3.35]},
        {"name": "West Hydronic Pump Wall", "from": [-4.55, -3.35], "to": [-4.55, 3.48]},
        {"name": "East Utility Header Wall", "from": [3.95, -3.35], "to": [3.95, 3.48]},
    ]

    eq_cnt = sum(1 for a in assets if a["tier"] == "equipment")
    ov_cnt = sum(1 for a in assets if a["tier"] == "overhead")

    return {
        "assets": assets,
        "walls": walls,
        "obstacle_points": obstacle_points,
        "floor_points": xyz_all[
            (xyz_all[:, 1] >= -0.06) & (xyz_all[:, 1] <= 0.10)
            & (op_all >= 0.25) & (sc_all <= 0.16)
        ],
        "splat_asset_idx": splat_asset_idx,
        "stats": {
            "total_extracted_assets": len(assets),
            "equipment_tier_assets": eq_cnt,
            "overhead_tier_assets": ov_cnt,
            "obstacle_splat_points": int(len(obstacle_points)),
        },
    }
