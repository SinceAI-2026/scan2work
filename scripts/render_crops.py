"""Render real Gaussian-splat crops (front + top) for every extracted object.

Writes web/data/crops/<OBJ-id>.jpg (front view, object box in yellow) + index.json, which
pipeline/semantic_labels.py sends to the Decisions API instead of point-dot drawings.
Needs Playwright + Chromium (run where they exist):   python -m scripts.render_crops [--ids OBJ-1,OBJ-2]
"""
from __future__ import annotations

import argparse, base64, io, json, math, shutil, struct, subprocess, sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import config as C, ingest, perception  # noqa: E402
from pipeline.semantic_labels import label_key  # noqa: E402

JOB_DIR = ROOT / "web" / "data" / "crop_jobs"
OUT_DIR = ROOT / "web" / "data" / "crops"


def jobs_for(splat, assets, only=None, px=448, out_dir=None):
    out_dir = out_dir or OUT_DIR
    xyz, rgb, op, sc, cov = splat["xyz"], splat["rgb"], splat["opacity"], splat["scale"], splat["cov6"]
    keep = (op > 0.065) & (sc < 0.45)
    xyz, rgb, op, cov = xyz[keep], rgb[keep], op[keep], cov[keep]
    order = np.argsort(xyz[:, 0]); xs = xyz[order, 0]
    rec = np.dtype([("xyz", "<f4", 3), ("rgba", "u1", 4), ("cov", "<f4", 6)])
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    jobs = []
    for a in assets:
        if only and a["id"] not in only:
            continue
        c = np.array(a["center"]); size = np.array(a["size"])
        m = size.max() * 0.9 + 0.15
        lo, hi = np.array(a["aabb_min"]) - m, np.array(a["aabb_max"]) + m
        sl = order[np.searchsorted(xs, lo[0]):np.searchsorted(xs, hi[0], side="right")]
        p = xyz[sl]
        sel = sl[(p[:, 1] >= lo[1]) & (p[:, 1] <= hi[1]) & (p[:, 2] >= lo[2]) & (p[:, 2] <= hi[2])]
        r = np.empty(len(sel), dtype=rec)
        r["xyz"] = xyz[sel]; r["cov"] = cov[sel]
        r["rgba"][:, :3] = np.clip(rgb[sel] * 255, 0, 255); r["rgba"][:, 3] = np.clip(op[sel] * 255, 4, 255)
        (JOB_DIR / f"{a['id']}.bin").write_bytes(r.tobytes())
        d = np.array([c[0], c[2]]); n = np.linalg.norm(d)
        d = d / n if n > 0.3 else np.array([0.0, 1.0])
        dist = (size.max() * 0.75 + 0.12) / math.tan(math.radians(25))
        front = {"eye": [c[0] - d[0] * dist, c[1] + 0.05, c[2] - d[1] * dist], "target": c.tolist(), "up": [0, 1, 0]}
        top = {"eye": [c[0], c[1] + dist, c[2]], "target": c.tolist(), "up": [d[0], 0, d[1]]}
        box = {"center": c.tolist(), "size": size.tolist(), "yaw": a["yaw"]}
        jobs.append({"id": a["id"], "key": label_key(a), "bin": f"../data/crop_jobs/{a['id']}.bin",
                     "views": [{**front, "box": box, "W": px, "H": px}]})
    (JOB_DIR / "jobs.json").write_text(json.dumps(jobs))
    # index: object id -> geometry key, so the pipeline only uses a crop if the object is unchanged
    out_dir.mkdir(parents=True, exist_ok=True)
    idx_path = out_dir / "index.json"
    index = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    index.update({j["id"]: j["key"] for j in jobs})
    idx_path.write_text(json.dumps(index))
    return jobs


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--ids", default="")
    ap.add_argument("--size", type=int, default=448, help="pixels; 896 = 2x resolution")
    ap.add_argument("--out", default="crops", help="folder under web/data")
    args = ap.parse_args()
    out_dir = ROOT / "web" / "data" / args.out
    only = set(filter(None, args.ids.split(",")))
    splat = ingest.level_scene(ingest.load_splat())
    perc = perception.extract_assets(splat)
    jobs = jobs_for(splat, perc["assets"], only, args.size, out_dir)
    print(f"{len(jobs)} crop jobs written; rendering...")
    subprocess.run(["node", str(ROOT / "scripts" / "render_crops.mjs"), str(ROOT / "web"), str(out_dir)], check=True)
    shutil.rmtree(JOB_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
