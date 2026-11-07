"""End-to-end orchestrator for the Valmet Semantic 3D Digital Twin pipeline.

Runs:
  1. 3D Gaussian Splatting ingestion & RANSAC floor leveling
  2. High-density 3D semantic segmentation & PCA Oriented Bounding Boxes
  3. Valmet Excel Builder Pack ingestion & 3D asset metadata binding
  4. 2.5D obstacle occupancy grid, morphological dilation, A* pathfinding & cKDTree proximity
  5. Grounded AI diagnostic copilot synthesis & evidence bundle generation
  6. Exports binary splat buffer + digital_twin.json for the interactive 3D Web Viewer & API
"""
from __future__ import annotations

import json
import os
import struct
import time
from collections import Counter

import numpy as np

from . import config as C
from . import ingest, perception, knowledge, navigation, diagnostics, semantic_labels, retrieval, decisions_client, detect_lift, walk_photos, pipes, floorplan, local_objects


def fill_object_membership(splat: dict, assets: list[dict], base_idx: np.ndarray, margin: float = 0.02) -> np.ndarray:
    """Every Gaussian inside an object's oriented box (+2 cm) belongs to that object; the smallest box
    wins where boxes overlap. Clustering only ever sees a subsample, so this makes each object whole
    for the exploded / segmentation view."""
    xyz = splat["xyz"]
    idx = base_idx.copy()
    best_vol = np.full(len(xyz), np.inf, dtype=np.float32)
    best_vol[idx >= 0] = 0.0  # keep the clustering's own assignments
    order = np.argsort(xyz[:, 0])
    xs = xyz[order, 0]
    for i, a in enumerate(assets):
        lo, hi = np.array(a["aabb_min"]) - margin, np.array(a["aabb_max"]) + margin
        sl = order[np.searchsorted(xs, lo[0]):np.searchsorted(xs, hi[0], side="right")]
        p = xyz[sl]
        sel = sl[(p[:, 1] >= lo[1]) & (p[:, 1] <= hi[1]) & (p[:, 2] >= lo[2]) & (p[:, 2] <= hi[2])]
        if not len(sel):
            continue
        d = xyz[sel][:, [0, 2]] - [a["center"][0], a["center"][2]]
        c, s_ = np.cos(a["yaw"]), np.sin(a["yaw"])
        lx, lz = d[:, 0] * c + d[:, 1] * s_, -d[:, 0] * s_ + d[:, 1] * c
        inside = (np.abs(lx) <= a["size"][0] / 2 + margin) & (np.abs(lz) <= a["size"][2] / 2 + margin)
        sel = sel[inside]
        vol = float(np.prod(a["size"]))
        win = sel[vol < best_vol[sel]]
        idx[win] = i
        best_vol[win] = vol
    return idx


def export_splat_binary(splat: dict, splat_asset_idx: np.ndarray, out_path) -> dict:
    """Pack Gaussians with full 3D anisotropic covariance matrix (cov6) into a binary buffer for WebGL2 EWA 3DGS.

    Format per Gaussian (40 bytes, little-endian, 10 x 32-bit words):
      float32 x, y, z                            (12 bytes)
      uint8   r, g, b, a                         (4 bytes)
      float32 c_xx, c_xy, c_xz, c_yy, c_yz, c_zz (24 bytes: 3D covariance matrix Sigma = R S^2 R^T)
    """
    xyz = splat["xyz"]
    rgb = splat["rgb"]
    op = splat["opacity"]
    sc = splat["scale"]
    cov6 = splat["cov6"]
    roi = splat["roi"]

    # Keep all visible room Gaussians across the full 8.6m x 7.0m Equipment Room
    keep = (
        (op > 0.065)
        & (sc < 0.45)
        & (xyz[:, 0] >= -4.75) & (xyz[:, 0] <= 4.15)
        & (xyz[:, 1] >= -0.12) & (xyz[:, 1] <= 2.22)
        & (xyz[:, 2] >= -3.55) & (xyz[:, 2] <= 3.75)
    )
    # Per-splat object index (aligned with the splat buffer) for the exploded / segmentation view
    inst = splat_asset_idx[keep]
    inst16 = np.where(inst >= 0, inst, 65535).astype("<u2")
    with open(out_path.parent / "splat_instance.bin", "wb") as f:
        f.write(struct.pack("<4sII", b"GSID", 1, len(inst16)))
        f.write(inst16.tobytes())

    pts = xyz[keep].astype(np.float32)
    cols = np.clip(rgb[keep] * 255.0, 0, 255).astype(np.uint8)
    alpha = np.clip(op[keep] * 255.0, 4, 255).astype(np.uint8)
    cov_keep = cov6[keep].astype(np.float32)

    n = len(pts)
    dtype = np.dtype([
        ("xyz", "<f4", (3,)),
        ("rgba", "u1", (4,)),
        ("cov6", "<f4", (6,)),
    ])
    packed = np.empty(n, dtype=dtype)
    packed["xyz"] = pts
    packed["rgba"][:, :3] = cols
    packed["rgba"][:, 3] = alpha
    packed["cov6"] = cov_keep

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<4sIII", b"GSPL", 3, n, 40))
        f.write(packed.tobytes())

    return {
        "exported_splats": int(n),
        "binary_size_bytes": int(16 + n * 40),
        "stride_bytes": 40,
        "file": out_path.name,
    }


def render_verification_map(splat: dict, assets: list[dict], nav: dict, out_path):
    """Save a high-resolution verification floorplan image with boxes & A* trajectory."""
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    fig, ax = plt.subplots(figsize=(14, 6.2), facecolor="#0b0f17")
    ax.set_facecolor("#0b0f17")

    m = splat["roi"] & (splat["opacity"] > 0.20) & (splat["scale"] < 0.22) & (splat["xyz"][:, 1] < 1.85)
    pts = splat["xyz"][m]
    cols = splat["rgb"][m]
    ax.scatter(pts[:, 0], pts[:, 2], c=cols, s=0.35, alpha=0.45, linewidths=0)

    # Plot obstacle cells faintly
    if nav["obstacle_cells_xz"]:
        ob = np.array(nav["obstacle_cells_xz"])
        ax.scatter(ob[:, 0], ob[:, 1], c="#ef4444", s=2.0, alpha=0.18, label="Dilated Obstacles (0.16–1.62m)")

    # Plot 3D asset OBB footprints
    for a in assets:
        if a["tier"] != "equipment":
            continue
        cx, _, cz = a["center"]
        sx, _, sz = a["size"]
        yaw = a["yaw"]
        ca, sa = np.cos(yaw), np.sin(yaw)
        R = np.array([[ca, -sa], [sa, ca]])
        corners = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * [sx / 2, sz / 2]
        world_corners = corners @ R.T + [cx, cz]
        if a["is_target"]:
            ec, lw, zord = "#ff3b5c", 2.2, 9
        elif a["condition_state"] == "degraded":
            ec, lw, zord = "#f97316", 1.4, 7
        elif a["condition_state"] == "watch":
            ec, lw, zord = "#eab308", 1.2, 6
        elif a["mapped"]:
            ec, lw, zord = "#22d3ee", 1.0, 5
        else:
            ec, lw, zord = "#38bdf8", 0.45, 3
        poly = Polygon(world_corners, closed=True, fill=a["is_target"], facecolor="#ff3b5c33", edgecolor=ec, linewidth=lw, zorder=zord)
        ax.add_patch(poly)
        if a["mapped"] and (a["is_target"] or a["condition_state"] != "healthy"):
            ax.text(cx, cz + 0.08, a["tag"], color=ec, fontsize=7.5, fontweight="bold", ha="center", zorder=10)

    # Plot primary collision-free A* path
    wps = nav["primary_route"]["waypoints"]
    px = [w["position"][0] for w in wps]
    pz = [w["position"][2] for w in wps]
    ax.plot(px, pz, color="#10b981", linewidth=2.8, label=f"Collision-Free A* Path ({nav['primary_route']['total_distance_m']:.2f}m)", zorder=11)
    ax.scatter([px[0]], [pz[0]], c="#10b981", s=70, edgecolors="white", zorder=12, label="Avatar Start")
    ax.scatter([px[-1]], [pz[-1]], c="#ff3b5c", s=90, marker="*", edgecolors="white", zorder=12, label=f"Target Standoff ({nav['target_tag']})")

    ax.set_aspect("equal")
    ax.set_xlim(-4.65, 4.05)
    ax.set_ylim(-3.55, 3.65)
    ax.set_xlabel("Plant X (meters)", color="#94a3b8")
    ax.set_ylabel("Plant Z (meters)", color="#94a3b8")
    ax.tick_params(colors="#94a3b8")
    ax.set_title(
        f"Valmet Boiler Room — {len(assets)} Extracted 3D Assets & Collision-Free A* Route to Faulty Target {nav['target_tag']} ({nav['target_asset_id']})",
        color="white", fontsize=11, pad=10,
    )
    ax.legend(loc="lower left", facecolor="#111827", edgecolor="#334155", labelcolor="white", fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130, facecolor="#0b0f17")
    plt.close()


def export_floorplan(splat: dict, nav: dict, out_path):
    """Orthographic scan raster sharing the navigation grid's exact X/Z bounds."""
    from PIL import Image
    from scipy import ndimage
    meta = nav["grid_meta"]
    scale = 4
    width, height = meta["nx"] * scale, meta["nz"] * scale
    xyz = splat["xyz"]
    ix = np.floor((xyz[:, 0] - meta["x_min"]) / meta["res"] * scale).astype(int)
    iz = np.floor((xyz[:, 2] - meta["z_min"]) / meta["res"] * scale).astype(int)
    keep = ((ix >= 0) & (ix < width) & (iz >= 0) & (iz < height)
            & (xyz[:, 1] >= -0.06) & (xyz[:, 1] <= 1.62)
            & (splat["opacity"] >= .25) & (splat["scale"] <= .16))
    # Top-most surface at each pixel gives a true plan view, without ceiling pipes.
    selected = np.where(keep)[0]
    selected = selected[np.argsort(xyz[selected, 1])]
    raster = np.full((height, width, 3), [223, 225, 216], dtype=np.uint8)
    hit = np.zeros((height, width), dtype=bool)
    yy, xx = height - 1 - iz[selected], ix[selected]
    raster[yy, xx] = np.clip(splat["rgb"][selected] * 255, 0, 255).astype(np.uint8)
    hit[yy, xx] = True
    # Fill sub-pixel sampling gaps only, without inventing room geometry.
    distance, nearest = ndimage.distance_transform_edt(~hit, return_indices=True)
    fill = (~hit) & (distance <= 1.5)
    raster[fill] = raster[nearest[0][fill], nearest[1][fill]]
    Image.fromarray(raster).save(out_path)


def main():
    t0 = time.time()
    print("[1/6] Loading and leveling 3D Gaussian Splatting scene...")
    raw = ingest.load_splat()
    pipe_segments = pipes.fit_segments(raw)             # needs the raw Gaussian orientations
    splat = ingest.level_scene(raw)

    print("[2/6] Running 3D semantic perception & PCA OBB framing...")
    perc = perception.extract_assets(splat)             # also the walls, floor and obstacles navigation needs
    if C.SEGMENTER == "local":                          # objects from the open-model pipeline (local_models/)
        local = local_objects.load(splat)
        perc["assets"], perc["splat_asset_idx"] = local["assets"], local["splat_asset_idx"]
        label_stats = {"source": "local_models", "local": local["stats"]}
    else:                                               # colour/geometry clusters, named by Gemini / Decisions
        label_stats = semantic_labels.relabel_assets(splat, perc)
        label_stats.update(semantic_labels.merge_and_clean(splat, perc))
        label_stats["detection"] = detect_lift.run(splat, perc, semantic_labels.CLASSES, semantic_labels._protected)
    label_stats["target"] = semantic_labels.consolidate_target(perc)
    label_stats["pipes"] = pipes.integrate(splat, perc, pipe_segments, semantic_labels._protected)
    label_stats["real_photos"] = walk_photos.build(splat, perc["assets"], semantic_labels.label_key)
    perc["stats"]["total_extracted_assets"] = len(perc["assets"])
    perc["stats"]["equipment_tier_assets"] = sum(a["tier"] == "equipment" for a in perc["assets"])
    perc["stats"]["overhead_tier_assets"] = sum(a["tier"] == "overhead" for a in perc["assets"])
    perc["stats"]["movable_objects"] = sum(bool(a.get("movable")) for a in perc["assets"])

    print("[3/6] Loading Valmet Excel Builder Pack & binding 3D assets...")
    excel_pack = knowledge.load_excel_pack()
    retriever = retrieval.Retriever(retrieval.build_corpus(excel_pack))
    print(f"   [retrieval] {len(retriever.items)} docs/cases/asset cards indexed with {retriever.backend}")
    assets = knowledge.bind_assets_to_scene(perc["assets"], excel_pack)

    print("[4/6] Building 2.5D occupancy grid & planning collision-free A* routes...")
    nav = navigation.build_navigation_bundle(perc, assets)

    print("[5/6] Generating grounded AI diagnostics, citations & work orders...")
    assets = diagnostics.enrich_with_diagnostics(assets, excel_pack, retriever)
    decisions_client.save_cache()
    retriever.save()

    print("[6/6] Exporting WebGL binary splat & digital_twin.json...")
    C.OUT_DIR.mkdir(parents=True, exist_ok=True)
    member_idx = fill_object_membership(splat, perc["assets"], perc["splat_asset_idx"])
    bin_meta = export_splat_binary(splat, member_idx, C.OUT_DIR / "boiler_room_points.bin")
    bin_meta["object_splats"] = int((member_idx >= 0).sum())
    render_verification_map(splat, assets, nav, C.OUT_DIR / "navigation_map_preview.png")
    export_floorplan(splat, nav, C.OUT_DIR / "floorplan_scan.png")
    nav["floorplan_image"] = "./data/floorplan_scan.png"
    try:                                                # architectural-style plan for service workers (Valmet request)
        nav["semantic_floorplan"] = floorplan.build(splat, assets, nav)
    except Exception as err:                            # never let a drawing break the twin
        print(f"   [floorplan] skipped: {err}")

    class_counts = dict(Counter(a["class"] for a in assets))
    state_counts = dict(Counter(a["condition_state"] for a in assets if a["mapped"]))

    payload = {
        "project": "Valmet Industrial Metaverse — Semantic 3D Scene Understanding & Autonomous Field-Service Copilot",
        "generated_in_sec": round(time.time() - t0, 2),
        "scene": {
            "source_file": C.PLY_PATH.name,
            "ingestion_stats": splat["stats"],
            "coordinate_frame": splat["frame"],
            "perception_stats": perc["stats"],
            "segmenter": C.SEGMENTER,
            "walls": perc["walls"],
            "splat_binary": bin_meta,
            "class_breakdown": class_counts,
            "canonical_state_breakdown": state_counts,
            "semantic_labelling": label_stats,
        },
        "ai_stack": {
            "cluster_labels": label_stats["source"],
            "retrieval_backend": retriever.backend,
            "triage": "decisions_api" if any(a["diagnostics"].get("ai_triage") for a in assets) else "deterministic_rules",
        },
        "target_asset_id": nav["target_asset_id"],
        "target_object_id": nav["target_object_id"],
        "target_tag": nav["target_tag"],
        "assets": assets,
        "navigation": nav,
        "knowledge_docs": excel_pack["docs"],
        "builder_questions": excel_pack["builder_questions"],
        "api_contract": excel_pack["api_contract"],
    }

    json_path = C.OUT_DIR / "digital_twin.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    print(f"Done in {payload['generated_in_sec']}s!")
    print(f"  -> Extracted 3D assets : {len(assets)} (Equipment: {perc['stats']['equipment_tier_assets']}, Overhead: {perc['stats']['overhead_tier_assets']})")
    print(f"  -> Mapped Excel assets : {sum(1 for a in assets if a['mapped'])} (Degraded/Watch: {sum(1 for a in assets if a['mapped'] and a['condition_state'] != 'healthy')})")
    print(f"  -> Primary target      : {nav['target_tag']} ({nav['target_asset_id']}) via {nav['target_object_id']}")
    print(f"  -> Collision-free path : {nav['primary_route']['total_distance_m']}m ({nav['primary_route']['duration_s']}s)")
    print(f"  -> Output JSON         : {json_path} ({json_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
