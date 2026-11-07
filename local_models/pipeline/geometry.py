"""Shared by the pipeline steps: the splat in the viewer's world frame, the posed video frames and
which Gaussians each frame sees, an object's box, and its masks in a video frame."""
import json
import os
from pathlib import Path

import numpy as np
from plyfile import PlyData

repo = Path(__file__).resolve().parent.parent
# Valmet's fixed splat: data/equipment-room.ply, else the file the main pipeline reads (../../Equipment Room.ply,
# next to the repo), or LOCAL_MODELS_PLY.
SPLAT = next((p for p in (Path(os.environ.get("LOCAL_MODELS_PLY", "")), repo / "data" / "equipment-room.ply",
                          repo.parent.parent / "Equipment Room.ply") if p.name and p.is_file()), repo / "data" / "equipment-room.ply")
SFM = repo / "data" / "sfm"
MASKS = repo / "data" / "pipeline" / "amg"  # pipeline/amg.py: SAM 2.1 masks per frame, half resolution
FLIP = np.diag([1.0, -1.0, -1.0])  # splat -> viewer world, matches index.html
MIN_OPACITY = 0.3  # Gaussians fainter than this are floaters and take no part in segmentation
DEPTH_CELL = 4  # mask pixels per depth-buffer cell; coarse so gaps between Gaussians don't leak
DEPTH_TOLERANCE = 0.05  # relative depth behind the nearest Gaussian that still counts as visible
NEAR = 0.05  # COLMAP units; points closer to the camera than this are behind it


def load_splat():
    """The PLY's vertex element and every Gaussian's centre and opacity (index = PLY row)."""
    ply = PlyData.read(SPLAT)["vertex"]
    opacity = 1 / (1 + np.exp(-np.asarray(ply["opacity"])))
    centres = np.stack([ply["x"], ply["y"], ply["z"]], 1).astype(np.float64)
    return ply, centres, opacity


def posed_frames():
    """Video frame name (e.g. '0042') -> its COLMAP image, from the model that also holds the splat renders."""
    import pycolmap  # here, not at the top: its OpenMP clashes with torch's in the steps that embed

    models = [pycolmap.Reconstruction(d) for d in (SFM / "sparse").iterdir()]
    rec = max(models, key=lambda model: sum(im.name.startswith("renders/") for im in model.images.values()))
    return {im.name[:-4]: im for im in rec.images.values() if not im.name.startswith("renders/")}


def to_colmap(splat_points):
    """Splat coordinates -> COLMAP's (inverse of splat = s r colmap + t, tools/align.py)."""
    alignment = json.loads((SFM / "alignment.json").read_text())
    s, r, t = alignment["scale"], np.array(alignment["rotation"]), np.array(alignment["translation"])
    return (splat_points - t) @ r / s


def project(image, points, height, width):
    """Pixel (u, v) of every COLMAP point at the given resolution, and the indices of those that are
    in the frame and not hidden behind nearer Gaussians (a coarse depth buffer)."""
    cam = points @ image.cam_from_world().rotation.matrix().T + image.cam_from_world().translation
    front = cam[:, 2] > NEAR
    pixels = np.full((len(cam), 2), -1.0)
    pixels[front] = image.camera.img_from_cam(cam[front]) / (image.camera.width / width)
    u, v = np.floor(pixels).astype(int).T
    idx = np.flatnonzero(front & (u >= 0) & (v >= 0) & (u < width) & (v < height))
    if not len(idx):  # looking at a part of the room the splat is missing
        return u, v, idx
    cells = (v[idx] // DEPTH_CELL) * (width // DEPTH_CELL + 1) + u[idx] // DEPTH_CELL
    nearest = np.full(cells.max() + 1, np.inf)
    np.minimum.at(nearest, cells, cam[idx, 2])
    return u, v, idx[cam[idx, 2] <= nearest[cells] * (1 + DEPTH_TOLERANCE)]


def frame_masks(frame, folder=MASKS):
    """All of a frame's SAM masks, (n, height, width) bool at half resolution."""
    data = np.load(folder / f"{frame}.npz")
    return np.unpackbits(data["masks"], axis=-1)[..., : data["mask_shape"][2]].astype(bool)


def object_mask(frame, index, folder=MASKS):
    """Full-resolution mask of an object in a video frame: one mask index or the union of several."""
    mask = frame_masks(frame, folder)[np.atleast_1d(index)].any(0)
    return mask.repeat(2, 0).repeat(2, 1)


def fit_box(points):
    """Upright box around world points: centre, size and yaw, robust to a few stray Gaussians."""
    low, high = np.percentile(points, 5, axis=0), np.percentile(points, 95, axis=0)
    flat = points[:, [0, 2]] - points[:, [0, 2]].mean(0)
    axis = np.linalg.eigh(flat.T @ flat)[1][:, -1]
    yaw = float(np.arctan2(axis[1], axis[0]))
    turn = np.array([[np.cos(yaw), np.sin(yaw)], [-np.sin(yaw), np.cos(yaw)]])
    local = flat @ turn.T
    along_low, along_high = np.percentile(local, 5, axis=0), np.percentile(local, 95, axis=0)
    centre_xz = points[:, [0, 2]].mean(0) + ((along_low + along_high) / 2) @ turn
    return {
        "center": [round(float(centre_xz[0]), 3), round(float((low[1] + high[1]) / 2), 3), round(float(centre_xz[1]), 3)],
        "size": [round(float(along_high[0] - along_low[0]), 3), round(float(high[1] - low[1]), 3), round(float(along_high[1] - along_low[1]), 3)],
        "yaw": round(yaw, 4),
    }
