"""Render ~32 photo-like views from inside the room with known cameras (for 2D detection -> 3D lifting).

Writes web/data/views3d/view_XX.jpg and web/data/views3d/cameras.json (view + projection matrices).
Needs Playwright + Chromium:   python -m scripts.render_views [--positions 8 --yaws 4]
"""
from __future__ import annotations

import argparse, json, math, shutil, subprocess, sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline import config as C, ingest  # noqa: E402

JOB_DIR = ROOT / "web" / "data" / "view_jobs"
OUT_DIR = ROOT / "web" / "data" / "views3d"
EYE_H, W, H, FOV = 1.25, 1024, 768, 60


def viewpoints(n_pos: int, n_yaw: int):
    nav = json.loads((C.OUT_DIR / "digital_twin.json").read_text())["navigation"]
    g = nav["grid_meta"]
    walk = np.array(nav["walkable_grid"], dtype=bool)
    clear = np.array(nav["clearance_grid_m"], dtype=float)
    cand = np.argwhere(walk & (clear >= 0.45))
    if len(cand) < n_pos:
        cand = np.argwhere(walk)
    xz = np.c_[g["x_min"] + (cand[:, 0] + 0.5) * g["res"], g["z_min"] + (cand[:, 1] + 0.5) * g["res"]]
    pick = [int(np.argmax(clear[tuple(cand.T)]))]           # farthest-point sampling, start at most open spot
    d = np.linalg.norm(xz - xz[pick[0]], axis=1)
    for _ in range(n_pos - 1):
        pick.append(int(np.argmax(d)))
        d = np.minimum(d, np.linalg.norm(xz - xz[pick[-1]], axis=1))
    views = []
    for p in pick:
        x, z = xz[p]
        for k in range(n_yaw):
            a = 2 * math.pi * k / n_yaw
            views.append({"eye": [x, EYE_H, z], "target": [x + math.sin(a), EYE_H - 0.18, z + math.cos(a)],
                          "up": [0, 1, 0], "W": W, "H": H, "fov": FOV, "box": None})
    return views


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--positions", type=int, default=8); ap.add_argument("--yaws", type=int, default=4)
    args = ap.parse_args()
    splat = ingest.level_scene(ingest.load_splat())
    xyz, rgb, op, sc, cov = splat["xyz"], splat["rgb"], splat["opacity"], splat["scale"], splat["cov6"]
    keep = (op > 0.065) & (sc < 0.45) & splat["roi"]
    rec = np.dtype([("xyz", "<f4", 3), ("rgba", "u1", 4), ("cov", "<f4", 6)])
    r = np.empty(int(keep.sum()), dtype=rec)
    r["xyz"] = xyz[keep]; r["cov"] = cov[keep]
    r["rgba"][:, :3] = np.clip(rgb[keep] * 255, 0, 255); r["rgba"][:, 3] = np.clip(op[keep] * 255, 4, 255)
    JOB_DIR.mkdir(parents=True, exist_ok=True); OUT_DIR.mkdir(parents=True, exist_ok=True)
    (JOB_DIR / "room.bin").write_bytes(r.tobytes())
    views = viewpoints(args.positions, args.yaws)
    (JOB_DIR / "views.json").write_text(json.dumps(views))
    print(f"{len(views)} views of {len(r)} splats; rendering...")
    subprocess.run(["node", str(ROOT / "scripts" / "render_views.mjs"), str(ROOT / "web"), str(OUT_DIR)], check=True)
    shutil.rmtree(JOB_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
