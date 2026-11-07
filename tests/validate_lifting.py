"""Geometry check for 2D->3D lifting, independent of any AI model.

Every existing 3D object is projected into the rendered views to make a perfect synthetic 2D box
(only where the object is actually visible). The boxes are lifted back with pipeline.detect_lift and
fused across views; we measure how well the original 3D boxes are recovered.
Run:  python -m tests.validate_lifting
"""
from __future__ import annotations

import itertools

import numpy as np

from pipeline import detect_lift as DL, ingest, perception
from pipeline.semantic_labels import CLASSES

INV = {v[0]: k for k, v in CLASSES.items()}


def main():
    splat = ingest.level_scene(ingest.load_splat())
    perc = perception.extract_assets(splat)
    cams = DL.load_cameras()
    assets = [a for a in perc["assets"] if max(a["size"]) >= 0.15 and a["tier"] == "equipment"]
    xyz = splat["xyz"]
    sel = np.where(splat["roi"] & (splat["opacity"] > 0.2) & (splat["scale"] < 0.2) & (xyz[:, 1] > 0.05))[0]
    pts = xyz[sel]
    dets, truth = {}, {}
    for cam in cams:
        vg = DL.ViewGeometry(pts, cam)
        vis_global = set(sel[vg.idx[vg.visible]].tolist())
        dets[cam["file"]] = []
        for a in assets:
            lo, hi = np.array(a["aabb_min"]), np.array(a["aabb_max"])
            corners = np.array(list(itertools.product(*zip(lo, hi))))
            px, py, d = DL.project(corners, cam)
            if (d <= 0.2).any():
                continue
            x0, x1 = np.clip([px.min(), px.max()], 0, cam["W"]); y0, y1 = np.clip([py.min(), py.max()], 0, cam["H"])
            if x1 - x0 < 20 or y1 - y0 < 20 or (x1 - x0) * (y1 - y0) > 0.4 * cam["W"] * cam["H"]:
                continue
            inside = (xyz[sel] >= lo).all(1) & (xyz[sel] <= hi).all(1)
            members = set(sel[inside].tolist())
            if len(members & vis_global) < 0.3 * max(1, len(members)):   # mostly hidden: a detector wouldn't see it
                continue
            key = f"{cam['file']}#{len(dets[cam['file']])}"
            dets[cam["file"]].append({"label": INV.get(a["class"], "other"),
                                      "box_2d": [int(y0 / cam["H"] * 1000), int(x0 / cam["W"] * 1000),
                                                 int(y1 / cam["H"] * 1000), int(x1 / cam["W"] * 1000)]})
            truth.setdefault(a["id"], a)
    fused = DL.lift_and_fuse(splat, cams, dets)
    ious = []
    for a in truth.values():
        lo, hi = np.array(a["aabb_min"]), np.array(a["aabb_max"])
        best = max((DL._iou(lo, hi, F["lo"], F["hi"]) for F in fused), default=0.0)
        ctr_hit = any(DL._inside((F["lo"] + F["hi"]) / 2, lo, hi, 0.05) for F in fused)
        ious.append((best, ctr_hit))
    best = np.array([b for b, _ in ious]); hit = np.array([h for _, h in ious])
    print(f"{sum(len(v) for v in dets.values())} synthetic detections of {len(truth)} visible objects in {len(cams)} views")
    print(f"recovered (3D IoU > 0.25): {np.mean(best > 0.25):.0%}   fused centre inside original box: {hit.mean():.0%}   "
          f"median IoU {np.median(best):.2f}")
    return float(np.mean(best > 0.25)), float(hit.mean())


if __name__ == "__main__":
    main()
