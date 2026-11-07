"""The real walkthrough video's camera path, in our levelled scene frame.

walk_poses.json is Linards' COLMAP alignment of the challenge video (SinceAI-2026/linards-project,
tools/sfm.py + tools/align.py): one camera per video frame at 3 fps, in his viewer frame
(viewer = diag(1,-1,-1) @ PLY, three.js [x,y,z,w] quaternions, camera looks down -Z).
Our levelled frame is the same rotation with the floor moved to y=0, so ours = viewer + (0, FLOOR_Y_PLY, 0).
The resulting camera height is ~1.46 m above our floor: a hand-held phone, which confirms the alignment.

Frames are web/data/walk_frames/f_%04d.jpg (ffmpeg -vf fps=3,scale=1280:-2), frame i <-> pose i-1.
"""
from __future__ import annotations

import json
import math

import numpy as np

from . import config as C
from .ingest import FLOOR_Y_PLY

POSES = C.ROOT / "web" / "data" / "walk_poses.json"
FRAMES = C.ROOT / "web" / "data" / "walk_frames"
VFOV_DEG = 43.0          # phone vertical FOV (linards-project index.html, confirmed by overlay)
FRAME_W, FRAME_H = 1280, 720


def _rotate(q, v):
    u, w = q[:, :3], q[:, 3:4]
    t = 2 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def available() -> bool:
    return POSES.exists() and FRAMES.exists()


def load() -> dict | None:
    if not available():
        return None
    fr = json.loads(POSES.read_text())
    q = np.array([f["quaternion"] for f in fr], float)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    n = len(fr)
    pos = np.array([f["position"] for f in fr], float) + [0.0, FLOOR_Y_PLY, 0.0]
    fwd = _rotate(q, np.tile([0.0, 0.0, -1.0], (n, 1)))
    up = _rotate(q, np.tile([0.0, 1.0, 0.0], (n, 1)))
    files = [FRAMES / f"f_{i + 1:04d}.jpg" for i in range(n)]
    keep = np.array([f.exists() for f in files])
    return {"t": np.array([f["t"] for f in fr])[keep], "pos": pos[keep], "fwd": fwd[keep], "up": up[keep],
            "files": [f for f, k in zip(files, keep) if k]}


def camera(walk: dict, i: int, W: int = FRAME_W, H: int = FRAME_H) -> dict:
    """A camera dict compatible with detect_lift.project (column-major three.js matrices)."""
    eye, f, u = walk["pos"][i], walk["fwd"][i], walk["up"][i]
    z = -f / np.linalg.norm(f)
    x = np.cross(u, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])                                   # world -> camera rotation (rows)
    V = np.eye(4); V[:3, :3] = R; V[:3, 3] = -R @ eye
    t = 1 / math.tan(math.radians(VFOV_DEG) / 2); a = W / H; n_, f_ = 0.05, 50.0
    P = np.array([[t / a, 0, 0, 0], [0, t, 0, 0], [0, 0, -(f_ + n_) / (f_ - n_), -2 * f_ * n_ / (f_ - n_)], [0, 0, -1, 0]])
    return {"file": str(walk["files"][i]), "W": W, "H": H, "eye": eye.tolist(),
            "view": V.T.ravel().tolist(), "proj": P.T.ravel().tolist(), "_V": V, "_P": P}


def render_job(walk: dict, i: int, W: int = FRAME_W, H: int = FRAME_H) -> dict:
    """A crop_render.html job reproducing frame i's viewpoint (for the overlay check)."""
    eye = walk["pos"][i]
    return {"eye": eye.tolist(), "target": (eye + walk["fwd"][i]).tolist(), "up": walk["up"][i].tolist(),
            "W": W, "H": H, "fov": VFOV_DEG, "box": None}
