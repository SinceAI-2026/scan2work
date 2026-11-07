"""Photo refinement: fit one object's Gaussians to the walkthrough frames that see it.

    .venv/bin/python -m pipeline.object_refine OBJ-008        # refine one object and print the losses

This is 3DGS training restricted to one object and started from the scan. It runs PyTorch on the CPU and calls
no AI model. The scan holds a few hundred to a few thousand wide splats per object (pump P-001 has 625 for
20 x 25 x 23 cm), so the photos have to supply the detail.

1. Start. The object's scan splats plus the faint splats of the same surface that the viewer drops
   (opacity < 0.065, Scan2Service's "attached" splats). Opacity is boosted to 1 - (1 - o)^2, splats wider than
   4 cm are clamped, and each splat is split into children of about 5 mm drawn from its own Gaussian.
2. Frames. Up to 16 sharp walkthrough frames that see the object, each cropped to it. Every crop pixel is
   labelled from a depth buffer of the whole room: object (fit it), behind the object (it must stay clear) or
   occluded (ignored).
3. Fit. Adam on positions, sizes, rotations, colours and opacities through a differentiable EWA rasteriser with
   the viewer's projection. Each frame also learns a small camera and exposure correction, because the COLMAP
   poses are a few millimetres to centimetres off. Priors keep colour, size and opacity near the scan's where the
   photos say little.
4. Keep. Splats that end up transparent are dropped.

The photos bound the result: 1280x720 frames at 1 to 3 m (1.5 to 4 mm per pixel), motion blur, and only the
sides the walkthrough saw. tests/eval_refine.py scores it against held-out frames.
"""
from __future__ import annotations

import math
import os
import time

import numpy as np
import torch
from PIL import Image, ImageFilter
from scipy.spatial import cKDTree

from . import config as C
from . import walk as WK
from .ingest import FLOOR_Y_PLY, _compute_cov6_world
from .asset_export import GSPL, read_ply_header

SH_C0 = 0.28209479177387814

DROP_ALPHA = 0.05
MAX_START_SIGMA_M = 0.04
CHILD_SIGMA_M = 0.005
MAX_CHILDREN = 32
BUDGET = 24_000
ATTACH_M = 0.03

MAX_FRAMES = 16
FRAME_MAX_DIST_M = 3.5
ZBUF_W, ZBUF_H = 320, 180
COARSE_PX = 256  # the object is fitted at most this many pixels across, then at FINE_PX for the last FINE_FRAC
FINE_PX = 512
FINE_FRAC = 0.3
MARGIN_PX = 16
MAX_RADIUS_PX = 12

ITERS = 200
LR = {"mean": 4e-4, "log_sigma": 6e-3, "quat": 2e-3, "color": 3e-2, "opacity": 3e-2, "pose": 2e-4, "expo": 2e-3}
DRIFT_M = 0.02
PRIOR = 0.5
MAX_SIGMA_M = 0.03

torch.set_num_threads(os.cpu_count() or 4)  # CPU beats MPS here: 66 s vs 375 s for 100 iterations of OBJ-204

_walk = None
_sharp: dict[int, float] = {}
_zbuf: dict[int, np.ndarray] = {}
_raw = None


# ---------------------------------------------------------------- the scan side
def cov_matrix(cov6: np.ndarray) -> np.ndarray:
    m = np.empty((len(cov6), 3, 3), np.float64)
    m[:, 0, 0], m[:, 0, 1], m[:, 0, 2] = cov6[:, 0], cov6[:, 1], cov6[:, 2]
    m[:, 1, 0], m[:, 1, 1], m[:, 1, 2] = cov6[:, 1], cov6[:, 3], cov6[:, 4]
    m[:, 2, 0], m[:, 2, 1], m[:, 2, 2] = cov6[:, 2], cov6[:, 4], cov6[:, 5]
    return m


def start_splats(xyz, rgb, alpha, cov6, rng, budget=BUDGET):
    """Drop haze, boost opacity, clamp wide splats and split each into children drawn from its own Gaussian.
    Returns positions, colours, opacities, per-axis variances and axes of the children."""
    keep = alpha >= DROP_ALPHA
    if keep.sum() < 50:
        keep[:] = True
    xyz, rgb, alpha = xyz[keep], rgb[keep], 1.0 - (1.0 - alpha[keep]) ** 2
    vals, vecs = np.linalg.eigh(cov_matrix(cov6[keep]))
    sig = np.sqrt(np.clip(vals, 1e-9, MAX_START_SIGMA_M ** 2))
    want = np.clip(np.ceil(sig[:, 2] * sig[:, 1] / CHILD_SIGMA_M ** 2), 1, MAX_CHILDREN)
    if want.sum() > budget:
        want = np.clip(np.floor(want * budget / want.sum()), 1, MAX_CHILDREN)
    n = want.astype(int)
    parent = np.repeat(np.arange(len(xyz)), n)
    child = np.minimum(np.maximum(sig / np.sqrt(n)[:, None] * 1.6, np.minimum(sig, CHILD_SIGMA_M)), sig)
    child[n == 1] = sig[n == 1]
    spread = np.sqrt(np.clip(sig ** 2 - child ** 2, 0, None))[parent] * 0.7
    offset = rng.standard_normal((len(parent), 3)) * spread
    pos = xyz[parent] + np.einsum("nij,nj->ni", vecs[parent], offset)
    return pos, rgb[parent], alpha[parent], child[parent] ** 2, vecs[parent]


def _raw_scan():
    """Levelled position, opacity and widest scale of every valid row of the source scan, cached per file."""
    global _raw
    mtime = C.PLY_PATH.stat().st_mtime
    if _raw is None or _raw[0] != mtime:
        _, off, dt = read_ply_header(C.PLY_PATH)
        raw = np.memmap(C.PLY_PATH, dtype=dt, mode="r", offset=off, shape=((C.PLY_PATH.stat().st_size - off) // dt.itemsize,))
        xyz = np.stack([raw["x"], raw["y"], raw["z"]], 1)
        rows = np.nonzero(np.isfinite(xyz).all(1) & (np.abs(xyz) < C.COORD_LIMIT_M).all(1) & (np.linalg.norm(xyz, axis=1) > 1e-4))[0]
        lev = np.stack([xyz[rows, 0], FLOOR_Y_PLY - xyz[rows, 1], -xyz[rows, 2]], 1).astype(np.float32)
        op = 1 / (1 + np.exp(-np.clip(np.asarray(raw["opacity"])[rows], -20, 20)))
        sc = np.exp(np.clip(np.stack([raw[f"scale_{k}"] for k in range(3)], 1)[rows], -9.5, -1.2)).max(1)
        _raw = (mtime, lev, op.astype(np.float32), sc.astype(np.float32), rows, raw)
    return _raw


def faint_splats(cutter, idx):
    """Source rows the viewer dropped as too faint that lie on this object: inside its bounds (+2 cm) and within
    ATTACH_M of one of its splats. Returns xyz, rgb, alpha, cov6 in the levelled frame, or None."""
    _, lev, op, sc, rows, raw = _raw_scan()
    obj = cutter.packed["xyz"][idx]
    lo, hi = obj.min(0) - 0.02, obj.max(0) + 0.02
    cand = np.nonzero((lev >= lo).all(1) & (lev <= hi).all(1) & (op > 0.01) & (sc < 0.08))[0]
    cand = cand[~np.isin(rows[cand], cutter.rows)]
    if len(cand):
        cand = cand[np.isfinite(cKDTree(obj).query(lev[cand], distance_upper_bound=ATTACH_M)[0])]
    if not len(cand):
        return None
    rec = raw[rows[cand]]
    scales = np.exp(np.clip(np.stack([rec[f"scale_{k}"] for k in range(3)], 1), -9.5, -1.2)).astype(np.float32)
    rots = np.stack([rec["rot_1"], rec["rot_2"], rec["rot_3"], rec["rot_0"]], 1).astype(np.float32)
    rgb = np.clip(0.5 + SH_C0 * np.stack([rec[f"f_dc_{k}"] for k in range(3)], 1), 0, 1)
    return lev[cand].astype(np.float64), rgb, op[cand].astype(np.float64), _compute_cov6_world(scales, rots).astype(np.float64)


# ---------------------------------------------------------------- the walkthrough frames
def walk_frames():
    global _walk
    if _walk is None:
        _walk = WK.load() or {}
    return _walk


def _sharpness(i):
    if i not in _sharp:
        g = Image.open(walk_frames()["files"][i]).convert("L").resize((320, 180))
        lap = np.asarray(g.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], 1, 128)), float) - 128
        _sharp[i] = float(lap.var())
    return _sharp[i]


def _project(i, pts):
    """Normalised image coordinates (0..1) and depth of pts in frame i."""
    cam = WK.camera(walk_frames(), i)
    V, P = cam["_V"], cam["_P"]
    c = pts @ V[:3, :3].T + V[:3, 3]
    d = -c[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        return (P[0, 0] * c[:, 0] / d + 1) * 0.5, (1 - P[1, 1] * c[:, 1] / d) * 0.5, d


def visible(i, pts, room_xyz):
    """Which pts frame i sees, against a coarse depth buffer of the whole room."""
    if i not in _zbuf:
        u, v, d = _project(i, room_xyz)
        ok = (d > 0.05) & (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
        buf = np.full(ZBUF_W * ZBUF_H, np.inf, np.float32)
        np.minimum.at(buf, (v[ok] * ZBUF_H).astype(int) * ZBUF_W + (u[ok] * ZBUF_W).astype(int), d[ok].astype(np.float32))
        pad = np.pad(buf.reshape(ZBUF_H, ZBUF_W), 1, constant_values=np.inf)  # a gap between sparse splats is no window
        _zbuf[i] = np.min([pad[dy:dy + ZBUF_H, dx:dx + ZBUF_W] for dy in range(3) for dx in range(3)], axis=0)
        if len(_zbuf) > 40:
            _zbuf.pop(next(iter(_zbuf)))
    u, v, d = _project(i, pts)
    zu = np.clip((u * ZBUF_W).astype(int), 0, ZBUF_W - 1)
    zv = np.clip((v * ZBUF_H).astype(int), 0, ZBUF_H - 1)
    return (d > 0.1) & (u >= 0) & (u < 1) & (v >= 0) & (v < 1) & (d <= _zbuf[i][zv, zu] + 0.03 + 0.01 * d)


def pick_frames(pts, room_xyz, k=MAX_FRAMES, exclude=(), min_angle_deg=0.5):
    """Up to k frames that see at least a quarter of the object, ranked by visible share x sharpness / distance."""
    walk = walk_frames()
    if not walk:
        return []
    centre = pts.mean(0)
    dist = np.linalg.norm(walk["pos"] - centre, axis=1)
    to_obj = (centre - walk["pos"]) / np.maximum(dist[:, None], 1e-6)
    facing = (walk["fwd"] * to_obj).sum(1)
    sub = pts[:: max(1, len(pts) // 400)]
    scored = []
    for i in np.nonzero((dist < FRAME_MAX_DIST_M) & (facing > math.cos(math.radians(25))))[0]:
        if int(i) in exclude:
            continue
        frac = visible(int(i), sub, room_xyz).mean()
        if frac >= 0.25:
            scored.append((frac * _sharpness(int(i)) / max(dist[i], 0.4), int(i)))
    chosen = []
    for _, i in sorted(scored, reverse=True):
        if all(np.dot(to_obj[i], to_obj[j]) < math.cos(math.radians(min_angle_deg)) for j in chosen):
            chosen.append(i)
        if len(chosen) == k:
            break
    return chosen


def _instance_ids(cutter):
    if not hasattr(cutter, "_inst"):
        inst = np.full(len(cutter.packed), -1, np.int32)
        for i in range(len(cutter.assets)):
            inst[cutter.order[cutter.bounds[i]:cutter.bounds[i + 1]]] = i
        cutter._inst = inst
    return cutter._inst


def _view(cutter, obj_index, obj_xyz, fi, target_px):
    """One training view of frame fi, scaled so the object is at most target_px across (never above the photo's
    own resolution): camera, cropped photo and a label per pixel (1 object, 0 behind it, -1 ignored)."""
    walk = walk_frames()
    V = WK.camera(walk, fi)["_V"]
    c = obj_xyz @ V[:3, :3].T + V[:3, 3]
    z = -c[:, 2]
    front = z > 0.1
    if front.sum() < 10:
        return None
    f_full = 0.5 * WK.FRAME_H / math.tan(math.radians(WK.VFOV_DEG) / 2)
    extent = max(np.ptp(np.clip(f_full * c[front, 0] / z[front], -WK.FRAME_W, WK.FRAME_W)),
                 np.ptp(np.clip(f_full * c[front, 1] / z[front], -WK.FRAME_H, WK.FRAME_H)), 1.0)
    scale = min(1.0, target_px / extent)
    W, H = int(WK.FRAME_W * scale), int(WK.FRAME_H * scale)
    f = f_full * H / WK.FRAME_H

    def project(p):
        q = p @ V[:3, :3].T + V[:3, 3]
        d = -q[:, 2]
        return W / 2 + f * q[:, 0] / d, H / 2 - f * q[:, 1] / d, d

    u, v, _ = project(obj_xyz)
    m = MARGIN_PX * scale * 2
    x0, x1 = int(max(0, u[front].min() - m)), int(min(W, u[front].max() + m))
    y0, y1 = int(max(0, v[front].min() - m)), int(min(H, v[front].max() + m))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    cw, ch = x1 - x0, y1 - y0
    ru, rv, rz = project(cutter.packed["xyz"].astype(np.float64))
    inside = (rz > 0.05) & (ru >= x0) & (ru < x1) & (rv >= y0) & (rv < y1)
    px = (rv[inside].astype(int) - y0) * cw + (ru[inside].astype(int) - x0)
    order = np.lexsort((rz[inside], px))
    nearest = order[np.r_[True, px[order][1:] != px[order][:-1]]]
    depth = np.full(cw * ch, np.inf)
    owner = np.full(cw * ch, -2)
    depth[px[nearest]] = rz[inside][nearest]
    owner[px[nearest]] = _instance_ids(cutter)[np.nonzero(inside)[0][nearest]]
    pd = np.pad(depth.reshape(ch, cw), 2, constant_values=np.inf)  # sparse splats leave holes: nearest within 2 px
    po = np.pad(owner.reshape(ch, cw), 2, constant_values=-2)
    sd = np.stack([pd[dy:dy + ch, dx:dx + cw] for dy in range(5) for dx in range(5)])
    so = np.stack([po[dy:dy + ch, dx:dx + cw] for dy in range(5) for dx in range(5)])
    k = sd.argmin(0)[None]
    depth, owner = np.take_along_axis(sd, k, 0)[0], np.take_along_axis(so, k, 0)[0]
    label = np.full((ch, cw), -1, np.int8)
    label[owner == obj_index] = 1
    label[(owner != obj_index) & (depth > z[front].max() + 0.05)] = 0
    if (label == 1).sum() < 30:
        return None
    img = Image.open(walk["files"][fi]).convert("RGB")
    if scale != 1:
        img = img.resize((W, H), Image.LANCZOS)
    return {"fi": fi, "V": V, "f": f, "W": W, "H": H, "x0": x0, "y0": y0, "cw": cw, "ch": ch,
            "photo": torch.tensor(np.asarray(img, np.float32)[y0:y1, x0:x1] / 255.0), "label": torch.tensor(label)}


# ---------------------------------------------------------------- differentiable EWA rasteriser
def _quat_to_rot(q):
    q = q / q.norm(dim=1, keepdim=True)
    w, x, y, z = q.unbind(1)
    return torch.stack([
        1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], 1).view(-1, 3, 3)


def _axis_angle(r):
    th = r.norm() + 1e-12
    k = r / th
    K = torch.zeros(3, 3)
    K[0, 1], K[0, 2], K[1, 0], K[1, 2], K[2, 0], K[2, 1] = -k[2], k[1], k[2], -k[0], -k[1], k[0]
    return torch.eye(3) + torch.sin(th) * K + (1 - torch.cos(th)) * K @ K


def render(P, view, pose):
    """Premultiplied colour (h, w, 3) and coverage (h, w) of the splats P in one view's crop."""
    V = torch.tensor(view["V"], dtype=torch.float32)
    dR = _axis_angle(pose[:3])
    R, t = dR @ V[:3, :3], dR @ V[:3, 3] + pose[3:]
    c = P["mean"] @ R.T + t
    z = -c[:, 2]
    f = view["f"]
    u = view["W"] / 2 + f * c[:, 0] / z - view["x0"]
    v = view["H"] / 2 - f * c[:, 1] / z - view["y0"]
    M = _quat_to_rot(P["quat"]) * torch.exp(P["log_sigma"]).clamp(max=MAX_SIGMA_M)[:, None, :]
    J = torch.zeros(len(z), 2, 3)
    J[:, 0, 0], J[:, 0, 2] = f / z, f * c[:, 0] / z ** 2
    J[:, 1, 1], J[:, 1, 2] = -f / z, -f * c[:, 1] / z ** 2
    T = J @ R[None]
    S = T @ (M @ M.transpose(1, 2)) @ T.transpose(1, 2) + 0.3 * torch.eye(2)[None]
    det = S[:, 0, 0] * S[:, 1, 1] - S[:, 0, 1] ** 2
    ia, ib, ic = S[:, 1, 1] / det, -S[:, 0, 1] / det, S[:, 0, 0] / det
    cw, ch = view["cw"], view["ch"]
    with torch.no_grad():  # which pixels each splat touches (indices only, no gradient)
        rad = torch.ceil(2.5 * torch.sqrt(torch.maximum(S[:, 0, 0], S[:, 1, 1]))).clamp(1, MAX_RADIUS_PX).int()
        vis = (z > 0.05) & (u > -rad) & (u < cw + rad) & (v > -rad) & (v < ch + rad)
        sid, pxs, pys = [], [], []
        for r in torch.unique(rad[vis]).tolist():
            s = torch.nonzero(vis & (rad == r)).squeeze(1)
            dy, dx = torch.meshgrid(torch.arange(-r, r + 1), torch.arange(-r, r + 1), indexing="ij")
            px = torch.round(u[s]).int()[:, None] + dx.reshape(1, -1).int()
            py = torch.round(v[s]).int()[:, None] + dy.reshape(1, -1).int()
            m = (px >= 0) & (px < cw) & (py >= 0) & (py < ch)
            sid.append(s[:, None].expand_as(px)[m])
            pxs.append(px[m])
            pys.append(py[m])
        if not sid:
            return torch.zeros(ch, cw, 3), torch.zeros(ch, cw)
        sid, pxs, pys = torch.cat(sid).long(), torch.cat(pxs).long(), torch.cat(pys).long()
    ex, ey = pxs.float() - u[sid], pys.float() - v[sid]
    a = torch.clamp(torch.sigmoid(P["opacity"])[sid] * torch.exp(-0.5 * (ia[sid] * ex * ex + 2 * ib[sid] * ex * ey + ic[sid] * ey * ey)), max=0.99)
    with torch.no_grad():  # front-to-back order per pixel
        live = a > 1 / 255
        sid, pix = sid[live], (pys * cw + pxs)[live]
        rank = torch.empty_like(z, dtype=torch.long)
        rank[torch.argsort(z.detach())] = torch.arange(len(z))
        o = torch.argsort(pix * len(z) + rank[sid])
        pix = pix[o]
        start = torch.ones_like(pix, dtype=torch.bool)
        start[1:] = pix[1:] != pix[:-1]
        seg = torch.cumsum(start.long(), 0) - 1
        first = torch.nonzero(start).squeeze(1)
    a, sid = a[live][o], sid[o]
    la = torch.log1p(-a)
    cs = torch.cumsum(la, 0)
    w = a * torch.exp(cs - la - (cs[first] - la[first])[seg])  # alpha x transmittance of the splats in front
    img = torch.zeros(ch * cw, 3).index_add(0, pix, torch.sigmoid(P["color"])[sid] * w[:, None])
    return img.view(ch, cw, 3), torch.zeros(ch * cw).index_add(0, pix, w).view(ch, cw)


def _dssim(a, b):
    k = torch.ones(3, 1, 7, 7) / 49
    x, y = a.permute(2, 0, 1)[None], b.permute(2, 0, 1)[None]
    f = lambda t: torch.nn.functional.conv2d(t, k, padding=3, groups=3)
    mx, my = f(x), f(y)
    vx, vy, cxy = f(x * x) - mx ** 2, f(y * y) - my ** 2, f(x * y) - mx * my
    return 1 - (((2 * mx * my + 1e-4) * (2 * cxy + 9e-4)) / ((mx ** 2 + my ** 2 + 1e-4) * (vx + vy + 9e-4))).mean()


def _quat_from_matrix(R):
    """(n, 3, 3) proper rotations to (n, 4) quaternions w, x, y, z."""
    q = np.empty((len(R), 4))
    q[:, 0] = np.sqrt(np.clip(1 + np.trace(R, axis1=1, axis2=2), 0, None)) / 2
    q[:, 1] = np.sqrt(np.clip(1 + R[:, 0, 0] - R[:, 1, 1] - R[:, 2, 2], 0, None)) / 2 * np.sign(R[:, 2, 1] - R[:, 1, 2] + 1e-12)
    q[:, 2] = np.sqrt(np.clip(1 - R[:, 0, 0] + R[:, 1, 1] - R[:, 2, 2], 0, None)) / 2 * np.sign(R[:, 0, 2] - R[:, 2, 0] + 1e-12)
    q[:, 3] = np.sqrt(np.clip(1 - R[:, 0, 0] - R[:, 1, 1] + R[:, 2, 2], 0, None)) / 2 * np.sign(R[:, 1, 0] - R[:, 0, 1] + 1e-12)
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def to_params(pos, rgb, alpha, var, axes, eps=0.02, grad=True):
    """Splat arrays (per-axis variances along the columns of axes) as the tensors render() and the fit use."""
    axes = axes.copy()
    axes[np.linalg.det(axes) < 0, :, 0] *= -1
    a, c = np.clip(alpha, eps, 1 - eps), np.clip(rgb, eps, 1 - eps)
    t = lambda x: torch.tensor(x, dtype=torch.float32, requires_grad=grad)
    return {"mean": t(pos), "log_sigma": t(np.log(np.sqrt(np.clip(var, 1e-10, None)))), "quat": t(_quat_from_matrix(axes)),
            "color": t(np.log(c / (1 - c))), "opacity": t(np.log(a / (1 - a)))}


def render_gspl(packed, V, W, H):
    """A GSPL model rendered whole into a W x H image from world-to-camera matrix V: (colour, coverage) arrays."""
    var, axes = np.linalg.eigh(cov_matrix(packed["cov6"].astype(np.float64)))
    P = to_params(packed["xyz"].astype(np.float64), packed["rgba"][:, :3] / 255.0, packed["rgba"][:, 3] / 255.0,
                  var, axes, eps=1e-3, grad=False)
    view = {"V": V, "f": 0.5 * H / math.tan(math.radians(WK.VFOV_DEG) / 2), "W": W, "H": H, "x0": 0, "y0": 0, "cw": W, "ch": H}
    with torch.no_grad():
        img, cover = render(P, view, torch.zeros(6))
    return img.numpy().astype(np.float64), cover.numpy().astype(np.float64)


# ---------------------------------------------------------------- the fit
def refine(cutter, oid: str, exclude_frames=(), iters: int = ITERS, seed: int = 0, log=None):
    """(refined GSPL rows, stats) for one object, or (None, stats with "error") when no frame sees it."""
    t0 = time.time()
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    i, idx, _, _ = cutter._object(oid)
    packed = cutter.packed[idx]
    parts = [(packed["xyz"].astype(np.float64), packed["rgba"][:, :3] / 255.0, packed["rgba"][:, 3] / 255.0,
              packed["cov6"].astype(np.float64))]
    faint = faint_splats(cutter, idx)
    if faint is not None:
        parts.append(faint)
    pos, rgb, alpha, var, axes = start_splats(*(np.concatenate(p) for p in zip(*parts)), rng)
    stats = {"scan_splats": int(len(idx)), "faint_added": 0 if faint is None else int(len(faint[0])),
             "start_splats": int(len(pos)), "frames": []}
    views = []  # (coarse, fine) per frame, so a frame keeps one pose and exposure correction at both scales
    for fi in pick_frames(pos, cutter.packed["xyz"], exclude=set(exclude_frames)):
        coarse = _view(cutter, i, pos, fi, COARSE_PX)
        if coarse is not None:
            views.append((coarse, _view(cutter, i, pos, fi, FINE_PX) or coarse))
    if not views:
        return None, stats | {"error": "no walkthrough frame sees this object clearly"}
    walk = walk_frames()
    stats["frames"] = [{"frame": walk["files"][c["fi"]].name, "video_t_s": round(float(walk["t"][c["fi"]]), 2)} for c, _ in views]
    P = to_params(pos, rgb, alpha, var, axes)
    anchor = P["mean"].detach().clone()
    init = {k: P[k].detach().clone() for k in ("color", "log_sigma", "opacity")}
    pose = torch.zeros(len(views), 6, requires_grad=True)
    expo = torch.zeros(len(views), 6, requires_grad=True)  # log gain (3) and bias (3) per frame
    opt = torch.optim.Adam([{"params": [P[k]], "lr": LR[k]} for k in P]
                           + [{"params": [pose], "lr": LR["pose"]}, {"params": [expo], "lr": LR["expo"]}])
    fine_from = int(iters * (1 - FINE_FRAC))
    losses = []
    for it in range(iters):
        k = int(rng.integers(len(views)))
        view = views[k][it >= fine_from]
        img, cover = render(P, view, pose[k])
        photo = (view["photo"] - expo[k, 3:]) / torch.exp(expo[k, :3])
        obj, behind = view["label"] == 1, view["label"] == 0
        comp = torch.where(obj[..., None], img + (1 - cover[..., None]) * photo.mean((0, 1)), photo)
        loss = (0.8 * ((img - photo * cover[..., None]).abs()[obj].mean() + 0.5 * (1 - cover[obj]).mean())
                + 0.2 * _dssim(comp, photo) + 0.3 * (cover[behind].mean() if bool(behind.any()) else 0.0))
        prior = ((torch.sigmoid(P["color"]) - torch.sigmoid(init["color"])).pow(2).mean()
                 + (torch.sigmoid(P["opacity"]) - torch.sigmoid(init["opacity"])).pow(2).mean()
                 + (0.2 * (P["log_sigma"] - init["log_sigma"])).pow(2).mean())
        drift = torch.relu((P["mean"] - anchor).norm(dim=1) - DRIFT_M).pow(2).mean()
        loss = loss + 10 * drift + PRIOR * prior + 1e-2 * (pose.pow(2).sum() + expo.pow(2).sum())
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(float(loss.detach()))
        if log and (it % 50 == 0 or it == iters - 1):
            log(f"  it {it:4d}  loss {np.mean(losses[-20:]):.4f}  ({time.time() - t0:.0f} s)")
    with torch.no_grad():
        keep = torch.sigmoid(P["opacity"]) > 0.02
        M = _quat_to_rot(P["quat"][keep]) * torch.exp(P["log_sigma"][keep]).clamp(max=MAX_SIGMA_M)[:, None, :]
        cov = (M @ M.transpose(1, 2)).numpy()
        out = np.zeros(int(keep.sum()), GSPL)
        out["xyz"] = P["mean"][keep].numpy()
        out["rgba"][:, :3] = np.clip(torch.sigmoid(P["color"][keep]).numpy() * 255, 0, 255)
        out["rgba"][:, 3] = np.clip(torch.sigmoid(P["opacity"][keep]).numpy() * 255, 4, 255)
        out["cov6"] = np.stack([cov[:, 0, 0], cov[:, 0, 1], cov[:, 0, 2], cov[:, 1, 1], cov[:, 1, 2], cov[:, 2, 2]], 1)
        pose_mm = float(pose[:, 3:].norm(dim=1).max() * 1000)
    return out, stats | {"iters": iters, "splats": int(len(out)), "loss_start": round(float(np.mean(losses[:20])), 4),
                         "loss_end": round(float(np.mean(losses[-20:])), 4),
                         "pose_correction_max_mm": round(pose_mm, 1), "seconds": round(time.time() - t0, 1)}


def write_3dgs_ply(packed: np.ndarray, path) -> None:
    """A GSPL model as a standard 3DGS PLY (SH degree 0) in the scan's raw frame: raw = (x, FLOOR_Y_PLY - y, -z)."""
    D = np.diag([1.0, -1.0, -1.0])
    xyz = packed["xyz"].astype(np.float64) * [1, -1, -1] + [0, FLOOR_Y_PLY, 0]
    vals, vecs = np.linalg.eigh(D @ cov_matrix(packed["cov6"].astype(np.float64)) @ D)
    vecs[np.linalg.det(vecs) < 0, :, 0] *= -1
    q = _quat_from_matrix(vecs)
    rgb = packed["rgba"][:, :3] / 255.0
    a = np.clip(packed["rgba"][:, 3] / 255.0, 1e-3, 1 - 1e-3)
    names = ["x", "y", "z", "scale_0", "scale_1", "scale_2", "rot_1", "rot_2", "rot_3", "rot_0",
             "f_dc_0", "f_dc_1", "f_dc_2", "opacity"]
    cols = [*xyz.T, *np.log(np.sqrt(np.clip(vals, 1e-12, None))).T, q[:, 1], q[:, 2], q[:, 3], q[:, 0],
            *((rgb - 0.5) / SH_C0).T, np.log(a / (1 - a))]
    head = ("ply\nformat binary_little_endian 1.0\ncomment Scan2Work object refined from the walkthrough\n"
            f"element vertex {len(packed)}\n" + "".join(f"property float {k}\n" for k in names) + "end_header\n")
    with open(path, "wb") as f:
        f.write(head.encode())
        f.write(np.stack(cols, 1).astype("<f4").tobytes())


if __name__ == "__main__":
    import sys
    from .asset_export import SceneCutter
    cut = SceneCutter()
    for oid in sys.argv[1:] or ["OBJ-008"]:
        _, st = refine(cut, oid, log=print)
        print(oid, st)
