"""Fit the COLMAP reconstruction onto the splat and write the video's camera path.

The renders from tools/render_views.py have known splat poses and COLMAP poses, so their
camera centres give the similarity between the two frames. Writes walk.json: one camera per
video frame, in the viewer's world frame (the splat flipped to y-up).
"""
import json
from pathlib import Path

import numpy as np
import pycolmap
from scipy.spatial.transform import Rotation

repo = Path(__file__).resolve().parent.parent
sfm = repo / "data" / "sfm"
FPS = 3  # frames per second extracted by ffmpeg into data/sfm/images
FLIP = np.diag([1.0, -1.0, -1.0])  # splat <-> viewer world, matches room.quaternion in index.html
OUTLIER = 3  # drop renders whose fit error is this many times the median, then refit


def umeyama(src, dst):
    """Similarity (s, R, t) minimising |s R src + t - dst|."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    u, d, vt = np.linalg.svd(b.T @ a / len(src))
    e = np.eye(3)
    e[2, 2] = np.sign(np.linalg.det(u @ vt))
    r = u @ e @ vt
    s = (d * np.diag(e)).sum() / (a**2).sum(1).mean()
    return s, r, mu_d - s * r @ mu_s


def is_render(image):
    return image.name.startswith("renders/")


# COLMAP can split the video into several models; use the one holding the most renders.
models = [pycolmap.Reconstruction(d) for d in (sfm / "sparse").iterdir()]
rec = max(models, key=lambda model: sum(map(is_render, model.images.values())))

known = {pose["name"]: FLIP @ pose["position"] for pose in json.loads((sfm / "renders.json").read_text())}
renders = [im for im in rec.images.values() if is_render(im)]
colmap_centres = np.array([im.cam_from_world().inverse().translation for im in renders])
splat_centres = np.array([known[im.name] for im in renders])

s, r, t = umeyama(colmap_centres, splat_centres)
error = np.linalg.norm(s * colmap_centres @ r.T + t - splat_centres, axis=1)
inliers = error < OUTLIER * np.median(error)
s, r, t = umeyama(colmap_centres[inliers], splat_centres[inliers])
error = np.linalg.norm(s * colmap_centres[inliers] @ r.T + t - splat_centres[inliers], axis=1)
print(f"{inliers.sum()}/{len(renders)} renders, scale {s:.4f}, centre error median {np.median(error):.3f} max {error.max():.3f}")
# splat = s * r @ colmap + t, for pipeline/geometry.py
(sfm / "alignment.json").write_text(json.dumps({"scale": s, "rotation": r.tolist(), "translation": t.tolist()}))

frames = []
for im in sorted((im for im in rec.images.values() if not is_render(im)), key=lambda im: im.name):
    pose = im.cam_from_world().inverse()
    position = FLIP @ (s * r @ pose.translation + t)
    # COLMAP cameras look down +z with y down; three.js cameras look down -z with y up.
    rotation = FLIP @ r @ pose.rotation.matrix() @ FLIP
    frames.append({
        "t": round((int(Path(im.name).stem) - 1) / FPS, 3),
        "position": np.round(position, 4).tolist(),
        "quaternion": np.round(Rotation.from_matrix(rotation).as_quat(), 5).tolist(),
    })
(repo / "walk.json").write_text(json.dumps(frames))
print(len(frames), "video frames, from", frames[0]["t"], "to", frames[-1]["t"], "s")
