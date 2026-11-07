"""Asset library for the Equipment Room: cut one object out of the scan and keep it as files.

    .venv/bin/python -m pipeline.asset_export OBJ-206 [OBJ-001 ...]   # save objects to the library
    .venv/bin/python -m pipeline.asset_export --remove OBJ-206         # take one out again

server.py calls the same save() when Save to library is chosen in the Take-apart view. The separation follows
Scan2Service's viewer/asset_export.py on this twin's own segmentation: the per-splat object ids in
web/data/splat_instance.bin, aligned with web/data/boiler_room_points.bin. An object keeps the splats its box
claimed, minus the floor (<= 6 cm) and floaters (4th-nearest neighbour over 4x the object's median gap).
A saved object gets web/data/library/<id>/:

  <id>.ply         its rows of ../Equipment Room.ply with every property (SH degree 3), the scan as it is
  <id>_model.ply   the model refined against the walkthrough frames (object_refine.py), standard 3DGS PLY
  <id>.gspl        the model in the viewer's 40-byte GSPL format (levelled frame, y up, metres)
  <id>.scan.gspl   the scan splats in the same format
  <id>.glb         an alpha-shape mesh of the splat centres
  thumb.jpg        a 3/4 render of the model

The entry in web/data/library/library.json is written last. No AI calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial import Delaunay, QhullError, cKDTree

from . import config as C
from .ingest import FLOOR_Y_PLY

DATA = C.OUT_DIR
FLOOR_CUT_M = 0.06  # splats this close to the floor are floor, not object
OUTLIER_X = 4.0  # a splat whose 4th-nearest neighbour is this many median gaps away is a floater
ALPHA_M = 0.06  # tetrahedra wider than this (circumradius, m) are gaps, not surface
MESH_MAX_POINTS = 20_000
THUMB_PX = 256
THUMB_BG = (17, 22, 28)
EXPORT_KEYS = ("label", "class", "class_label", "tag", "asset_id", "condition_state", "health_score", "issue_code",
               "is_target", "movable", "mapped", "photo", "center", "size", "yaw", "label_source")
GSPL = np.dtype([("xyz", "<f4", (3,)), ("rgba", "u1", (4,)), ("cov6", "<f4", (6,))])


def read_ply_header(path: Path):
    """(vertex count, data offset, numpy row dtype) of a binary little-endian PLY of float properties."""
    with open(path, "rb") as f:
        head = b""
        while not head.endswith(b"end_header\n"):
            line = f.readline()
            if not line:
                raise ValueError(f"{path}: no end_header")
            head += line
    names, n = [], 0
    for line in head.decode("ascii", "replace").splitlines():
        if line.startswith("element vertex"):
            n = int(line.split()[-1])
        elif line.startswith("property"):
            kind, name = line.split()[1:3]
            if kind != "float":
                raise ValueError(f"{path}: property {name} is {kind}, expected float")
            names.append(name)
    return n, len(head), np.dtype([(k, "<f4") for k in names])


def source_rows(ply: Path, n_bin: int, bin_xyz: np.ndarray) -> np.ndarray:
    """Row in the source PLY of every splat in boiler_room_points.bin: the same filters ingest.load_splat,
    level_scene and run.export_splat_binary apply, in the same order. Checked against the binary's positions."""
    n, off, dt = read_ply_header(ply)
    avail = (ply.stat().st_size - off) // dt.itemsize
    raw = np.memmap(ply, dtype=dt, mode="r", offset=off, shape=(min(n, avail),))
    xyz = np.stack([raw["x"], raw["y"], raw["z"]], 1)
    sc = np.stack([raw[f"scale_{i}"] for i in range(3)], 1)
    rot = np.stack([raw[f"rot_{i}"] for i in range(4)], 1)
    dc = np.stack([raw[f"f_dc_{i}"] for i in range(3)], 1)
    op_logit = np.asarray(raw["opacity"])
    ok = (np.isfinite(xyz).all(1) & np.isfinite(sc).all(1) & np.isfinite(rot).all(1) & np.isfinite(dc).all(1)
          & np.isfinite(op_logit) & (np.abs(xyz) < C.COORD_LIMIT_M).all(1) & (np.linalg.norm(xyz, axis=1) > 1e-4))
    rows = np.nonzero(ok)[0]
    p = xyz[rows].astype(np.float64)
    lev = np.stack([p[:, 0], FLOOR_Y_PLY - p[:, 1], -p[:, 2]], 1).astype(np.float32)
    scale = np.exp(np.clip(sc[rows], -9.5, -1.2).astype(np.float64)).astype(np.float32).mean(1)
    op = (1.0 / (1.0 + np.exp(-np.clip(op_logit[rows], -20.0, 20.0)))).astype(np.float32)
    keep = ((op > 0.065) & (scale < 0.45)
            & (lev[:, 0] >= -4.75) & (lev[:, 0] <= 4.15) & (lev[:, 1] >= -0.12) & (lev[:, 1] <= 2.22)
            & (lev[:, 2] >= -3.55) & (lev[:, 2] <= 3.75))
    rows = rows[keep]
    if len(rows) != n_bin or not np.allclose(lev[keep][:: 997], bin_xyz[:: 997], atol=1e-5):
        raise RuntimeError("boiler_room_points.bin does not match the source PLY: rebuild with pipeline.run first")
    return rows


def own_splats(xyz: np.ndarray, idx: np.ndarray):
    """The object's splats minus the floor and floaters: (kept, n_floor, n_floaters)."""
    keep = idx[xyz[idx, 1] > FLOOR_CUT_M]
    n_floor = len(idx) - len(keep)
    if len(keep) < 50:
        return keep, n_floor, 0
    d = cKDTree(xyz[keep]).query(xyz[keep], k=5)[0][:, 4]
    ok = d <= OUTLIER_X * max(float(np.median(d)), 1e-6)
    return keep[ok], n_floor, int((~ok).sum())


def write_ply_rows(src: Path, rows: np.ndarray, dst: Path) -> None:
    n, off, dt = read_ply_header(src)
    raw = np.memmap(src, dtype=dt, mode="r", offset=off, shape=((src.stat().st_size - off) // dt.itemsize,))
    head = (f"ply\nformat binary_little_endian 1.0\ncomment Field Atlas object cut from {src.name}\n"
            f"element vertex {len(rows)}\n" + "".join(f"property float {k}\n" for k in dt.names) + "end_header\n")
    tmp = dst.with_name(dst.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(head.encode())
        f.write(np.ascontiguousarray(raw[np.sort(rows)]).tobytes())
    tmp.replace(dst)


def write_gspl(packed: np.ndarray, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(struct.pack("<4sIII", b"GSPL", 3, len(packed), GSPL.itemsize))
        f.write(packed.tobytes())
    tmp.replace(dst)


def alpha_mesh(points: np.ndarray, alpha: float = ALPHA_M) -> np.ndarray:
    """Boundary triangles of the Delaunay tetrahedra with circumradius < alpha, wound outwards (Scan2Service)."""
    try:
        tet = Delaunay(points).simplices if len(points) >= 5 else np.zeros((0, 4), int)
    except QhullError:
        tet = np.zeros((0, 4), int)
    a, b, c, d = (points[tet[:, i]].astype(np.float64) for i in range(4))
    u, v, w = b - a, c - a, d - a
    num = ((u * u).sum(1)[:, None] * np.cross(v, w) + (v * v).sum(1)[:, None] * np.cross(w, u)
           + (w * w).sum(1)[:, None] * np.cross(u, v))
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.linalg.norm(num / (2 * (u * np.cross(v, w)).sum(1))[:, None], axis=1)
    tet = tet[r < alpha]
    faces = np.concatenate([tet[:, [0, 1, 2]], tet[:, [0, 1, 3]], tet[:, [0, 2, 3]], tet[:, [1, 2, 3]]])
    opposite = np.concatenate([tet[:, 3], tet[:, 2], tet[:, 1], tet[:, 0]])
    _, first, count = np.unique(np.sort(faces, axis=1), axis=0, return_index=True, return_counts=True)
    faces, opposite = faces[first[count == 1]], opposite[first[count == 1]]
    p0, p1, p2 = (points[faces[:, i]] for i in range(3))
    inward = (np.cross(p1 - p0, p2 - p0) * (points[opposite] - p0)).sum(1) > 0
    faces[inward] = faces[inward][:, [0, 2, 1]]
    return faces


def write_glb(path: Path, vertices: np.ndarray, faces: np.ndarray, srgb: np.ndarray) -> None:
    """Minimal glTF 2.0 binary: one double-sided mesh with positions and vertex colours."""
    lin = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    pos = np.ascontiguousarray(vertices, np.float32)
    blobs = [pos.tobytes(), np.ascontiguousarray(faces, np.uint32).tobytes(), np.ascontiguousarray(lin, np.float32).tobytes()]
    views, off = [], 0
    for blob, target in zip(blobs, (34962, 34963, 34962)):
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(blob), "target": target})
        off += -(-len(blob) // 4) * 4
    gltf = {"asset": {"version": "2.0", "generator": "Field Atlas asset_export"}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 2}, "indices": 1, "material": 0}]}],
            "materials": [{"doubleSided": True, "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.9}}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(pos), "type": "VEC3",
                           "min": pos.min(0).tolist(), "max": pos.max(0).tolist()},
                          {"bufferView": 1, "componentType": 5125, "count": int(faces.size), "type": "SCALAR"},
                          {"bufferView": 2, "componentType": 5126, "count": len(pos), "type": "VEC3"}],
            "bufferViews": views, "buffers": [{"byteLength": off}]}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    data = b"".join(blob + b"\0" * (-len(blob) % 4) for blob in blobs)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(data)) + struct.pack("<II", len(js), 0x4E4F534A)
                    + js + struct.pack("<II", len(data), 0x004E4942) + data)
    tmp.replace(path)


def thumbnail(packed: np.ndarray, dst: Path, px: int = THUMB_PX) -> None:
    """3/4 view of the splats, nearest wins, rendered at 2x and downsampled."""
    p = packed["xyz"].astype(np.float64)
    p = p - (p.min(0) + p.max(0)) / 2
    yaw, pitch = np.radians(35), np.radians(22)
    cy, sy, cp, sp = np.cos(yaw), np.sin(yaw), np.cos(pitch), np.sin(pitch)
    x = p[:, 0] * cy - p[:, 2] * sy
    z0 = p[:, 0] * sy + p[:, 2] * cy
    y = p[:, 1] * cp - z0 * sp
    depth = p[:, 1] * sp + z0 * cp
    big = px * 2
    extent = max(np.ptp(x), np.ptp(y), 1e-3) * 1.12
    u = ((x / extent + 0.5) * big).astype(int)
    v = ((0.5 - y / extent) * big).astype(int)
    sigma = np.sqrt(np.clip(packed["cov6"][:, [0, 3, 5]].mean(1), 0, None)) / extent * big
    radius = np.ceil(np.clip(1.6 * sigma, 1.5, 14)).astype(int)
    solid = packed["rgba"][:, 3] >= 40  # faint haze splats would only grey the picture
    cand = []  # every pixel each splat's disc covers, with its depth: the nearest one wins (z-buffer)
    for r in np.unique(radius[solid]):
        sel = np.nonzero(solid & (radius == r))[0]
        dx, dy = np.mgrid[-r:r + 1, -r:r + 1]
        disc = dx * dx + dy * dy <= r * r
        dx, dy = dx[disc], dy[disc]
        uu = (u[sel, None] + dx).ravel()
        vv = (v[sel, None] + dy).ravel()
        cand.append((uu, vv, np.repeat(sel, len(dx))))
    img = np.empty((big, big, 3), np.uint8)
    img[:] = THUMB_BG
    if cand:
        uu, vv, who = (np.concatenate(c) for c in zip(*cand))
        ok = (uu >= 0) & (uu < big) & (vv >= 0) & (vv < big)
        uu, vv, who = uu[ok], vv[ok], who[ok]
        pix = vv * big + uu
        first = np.lexsort((depth[who], pix))
        pix, who = pix[first], who[first]
        lead = np.r_[True, pix[1:] != pix[:-1]]
        img.reshape(-1, 3)[pix[lead]] = packed["rgba"][who[lead], :3]
    Image.fromarray(img).resize((px, px), Image.LANCZOS).save(dst, quality=88)


class SceneCutter:
    """The scan, the viewer's splats and their object ids, loaded once; cut(index) separates one object."""

    def __init__(self):
        self.twin_mtime = (DATA / "digital_twin.json").stat().st_mtime
        twin = json.loads((DATA / "digital_twin.json").read_text())
        self.assets = twin["assets"]
        self.exact = twin.get("scene", {}).get("membership_mode") == "exact-source-rows"
        self.source = DATA / twin["scene"]["source_ply"] if self.exact else C.PLY_PATH
        raw = (DATA / "boiler_room_points.bin").read_bytes()
        n_bin, stride = struct.unpack_from("<II", raw, 8)
        if stride != GSPL.itemsize:
            raise RuntimeError(f"unexpected splat stride {stride}")
        self.packed = np.frombuffer(raw, dtype=GSPL, count=n_bin, offset=16)
        inst_raw = (DATA / "splat_instance.bin").read_bytes()
        n_inst = struct.unpack_from("<I", inst_raw, 8)[0]
        if n_inst != n_bin:
            raise RuntimeError("splat_instance.bin and boiler_room_points.bin disagree: rebuild with pipeline.run")
        inst = np.frombuffer(inst_raw, dtype="<u2", count=n_inst, offset=12)
        self.order = np.argsort(inst, kind="stable")
        self.bounds = np.searchsorted(inst[self.order], np.arange(len(self.assets) + 1))
        if self.exact:
            digest = hashlib.sha256()
            with self.source.open('rb') as stream:
                for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                    digest.update(block)
            if digest.hexdigest() != twin['scene']['source_sha256']:
                raise RuntimeError('Imported source PLY SHA256 changed; exact export refused')
            self.rows = np.load(DATA / twin["scene"]["source_rows_file"], allow_pickle=False)
            if len(self.rows) != n_bin or len(np.unique(self.rows)) != n_bin:
                raise RuntimeError("Imported source row map is missing, duplicated or misaligned")
            n, off, dt = read_ply_header(self.source)
            if np.any(self.rows >= n):
                raise RuntimeError("Imported source row index out of bounds")
            native = np.memmap(self.source, dtype=dt, mode="r", offset=off, shape=(n,))
            xyz = np.stack([native[k][self.rows] for k in ('x','y','z')], 1)
            xyz = xyz * [1,-1,-1] + [0,FLOOR_Y_PLY,0]
            if not np.allclose(xyz, self.packed['xyz'], atol=1e-5):
                raise RuntimeError("Imported source row order does not match viewer geometry")
            with np.load(DATA / twin['scene']['members_file'], allow_pickle=False) as membership:
                if membership['object_ids'].tolist() != [a['id'] for a in self.assets]:
                    raise RuntimeError('Imported membership object order changed')
                for i in range(len(self.assets)):
                    expected = membership['source_rows'][membership['offsets'][i]:membership['offsets'][i+1]]
                    found = self.rows[self.order[self.bounds[i]:self.bounds[i+1]]]
                    if not np.array_equal(np.sort(expected), np.sort(found)):
                        raise RuntimeError(f"Imported ownership mismatch for {self.assets[i]['id']}")
        else:
            self.rows = source_rows(self.source, n_bin, self.packed["xyz"])
        self.by_id = {a["id"]: i for i, a in enumerate(self.assets)}

    def stale(self) -> bool:
        return (DATA / "digital_twin.json").stat().st_mtime != self.twin_mtime

    def _object(self, oid: str):
        if oid not in self.by_id:
            raise KeyError(f"no object {oid!r} in digital_twin.json")
        i = self.by_id[oid]
        if self.exact:
            idx = np.sort(self.order[self.bounds[i]:self.bounds[i + 1]])
            if len(idx) != self.assets[i].get('exact_owned_gaussians', self.assets[i]['n_gaussians']):
                raise ValueError("Exact imported object ownership is incomplete")
            return i, idx, 0, 0
        idx, n_floor, n_float = own_splats(self.packed["xyz"], np.sort(self.order[self.bounds[i]:self.bounds[i + 1]]))
        if len(idx) < 20:
            raise ValueError(f"{oid} has only {len(idx)} splats of its own: nothing to keep")
        return i, idx, n_floor, n_float

    def cut(self, oid: str, out: Path) -> dict:
        """Write the object's files into out and return its library entry (without saved_at). The model is
        refined against the walkthrough frames; with no frame that sees the object it is the scan splats."""
        i, idx, n_floor, n_float = self._object(oid)
        a = self.assets[i]
        out.mkdir(parents=True, exist_ok=True)
        packed = self.packed[idx]
        if self.exact:
            # Never expand exact source ownership using a box, remove floor samples,
            # or synthesize an apparent reconstruction when exporting an imported region.
            write_ply_rows(self.source, self.rows[idx], out / f"{oid}.ply")
            write_gspl(packed, out / f"{oid}.scan.gspl")
            write_gspl(packed, out / f"{oid}.gspl")
            np.save(out / 'source_rows.npy', self.rows[idx], allow_pickle=False)
            thumbnail(packed, out / 'thumb.jpg')
            p = packed['xyz']; lo, hi = p.min(0), p.max(0)
            return {"id": oid, "index": i, **{k:a.get(k) for k in EXPORT_KEYS},
                    "splats":len(idx), "left_to_room":{"floor":0,"floaters":0},
                    "centroid":p.mean(0).tolist(),"bounds":{"min":lo.tolist(),"max":hi.tolist()},
                    "radius":float(np.linalg.norm(hi-lo)/2), "membership_mode":"exact-source-rows",
                    "source_sha256":json.loads((DATA/'digital_twin.json').read_text())['scene']['source_sha256'],
                    "provenance":a.get('provenance'),"review_status":a.get('review_status'),"scale_verified":False,
                    "model":{"status":"exact-source-export","refined":False,"note":"Original Gaussian rows; no new geometry or image-based reconstruction"},
                    "files":{"ply":f'{oid}/{oid}.ply',"gspl":f'{oid}/{oid}.gspl',"scan_gspl":f'{oid}/{oid}.scan.gspl',
                             "source_rows":f'{oid}/source_rows.npy',"thumb":f'{oid}/thumb.jpg',"model_ply":None,"glb":None},
                    "source":self.source.name}
        from . import object_refine  # legacy opt-in pipeline only
        model, stats = object_refine.refine(self, oid)
        if model is None:
            model = packed
        write_ply_rows(self.source, self.rows[idx], out / f"{oid}.ply")
        write_gspl(packed, out / f"{oid}.scan.gspl")
        write_gspl(model, out / f"{oid}.gspl")
        object_refine.write_3dgs_ply(model, out / f"{oid}_model.ply")
        thumbnail(model, out / "thumb.jpg")
        p = packed["xyz"].astype(np.float64)
        lo, hi = p.min(0), p.max(0)
        e = {"id": oid, "index": i, **{k: a.get(k) for k in EXPORT_KEYS},
             "splats": int(len(idx)), "left_to_room": {"floor": n_floor, "floaters": n_float},
             "centroid": np.round(p.mean(0), 4).tolist(),
             "bounds": {"min": np.round(lo, 4).tolist(), "max": np.round(hi, 4).tolist()},
             "radius": round(float(np.linalg.norm(hi - lo) / 2), 4),
             "model": stats,
             "files": {"ply": f"{oid}/{oid}.ply", "model_ply": f"{oid}/{oid}_model.ply", "gspl": f"{oid}/{oid}.gspl",
                       "scan_gspl": f"{oid}/{oid}.scan.gspl", "glb": None, "thumb": f"{oid}/thumb.jpg"},
             "source": C.PLY_PATH.name}
        pick = np.arange(len(p)) if len(p) <= MESH_MAX_POINTS else np.sort(
            np.random.default_rng(0).choice(len(p), MESH_MAX_POINTS, replace=False))
        faces = alpha_mesh(p[pick])
        if len(faces):
            vert, inv = np.unique(faces, return_inverse=True)
            write_glb(out / f"{oid}.glb", p[pick][vert], inv.reshape(-1, 3), packed["rgba"][pick][vert][:, :3] / 255.0)
            e["files"]["glb"] = f"{oid}/{oid}.glb"
            e["mesh_faces"] = int(len(faces))
        return e


LIBRARY = DATA / "library"
LIBRARY_JSON = LIBRARY / "library.json"
_lock = threading.Lock()
_cutter: SceneCutter | None = None


def read_library() -> list[dict]:
    try:
        return json.loads(LIBRARY_JSON.read_text()).get("saved", [])
    except (OSError, ValueError):
        return []


def _write_library(saved: list[dict]) -> None:
    LIBRARY.mkdir(parents=True, exist_ok=True)
    doc = {"version": 2, "project": "Field Atlas · Equipment Room", "source": C.PLY_PATH.name,
           "frame": {"ply": "raw PLY coordinates of the source, every property kept",
                     "gspl_glb_bounds": "levelled frame: x, y = FLOOR_Y_PLY - y_ply, z = -z_ply (y up, floor at 0, m)",
                     "floor_y_ply": FLOOR_Y_PLY},
           "membership": ("splats inside the object's oriented box (+2 cm, the smallest box wins; pipeline.run "
                          "fill_object_membership), minus the floor (<= 6 cm) and floaters (4th-nearest neighbour "
                          "over 4x the object's median gap), which stay with the room"),
           "credits": "separation after Scan2Service viewer/asset_export.py (SinceAI-2026/scan2service)",
           "saved": saved}
    if any(s.get('membership_mode') == 'exact-source-rows' for s in saved):
        doc.update(source='source_scan.ply', membership='Exact imported original source rows. No floor removal, expansion, filling or refinement.')
        if any(s.get('membership_mode') != 'exact-source-rows' for s in saved):
            doc['membership'] = ('Mixed provenance: entries marked exact-source-rows preserve their dataset ownership without refinement; '
                                 'archived entries retain their original extraction/refinement provenance. Check each entry.')
        doc['frame']['gspl_glb_bounds'] = 'Levelled frame; source units and physical scale unverified. Native PLY preserves original frame and attributes.'
    tmp = LIBRARY_JSON.with_name(LIBRARY_JSON.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(LIBRARY_JSON)


def _scene() -> SceneCutter:
    global _cutter
    if _cutter is None or _cutter.stale():
        _cutter = SceneCutter()
    return _cutter


def save(oid: str) -> dict:
    """Cut oid out of the scan into the library (again, if it is there already) and record it."""
    with _lock:
        cutter = _scene()
        saved = read_library()
        prev = next((s for s in saved if s["id"] == oid), None)
        entry = cutter.cut(oid, LIBRARY / oid)
        entry["saved_at"] = prev["saved_at"] if prev else time.strftime("%Y-%m-%dT%H:%M:%S")
        _write_library([s for s in saved if s["id"] != oid] + [entry])
        return entry


def remove(oid: str) -> list[dict]:
    """Take oid out of the library and delete its files."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", oid):  # never a path: "..", "/" or "" must not reach rmtree
        raise ValueError(f"not an object id: {oid!r}")
    with _lock:
        saved = [s for s in read_library() if s["id"] != oid]
        _write_library(saved)
        folder = LIBRARY / oid
        if folder.parent == LIBRARY and folder.is_dir():
            shutil.rmtree(folder)
        return saved


def main() -> None:
    ap = argparse.ArgumentParser(description="Save extracted objects to the Field Atlas asset library")
    ap.add_argument("ids", nargs="+", help="object ids, e.g. OBJ-206")
    ap.add_argument("--remove", action="store_true", help="take these objects out of the library")
    args = ap.parse_args()
    for oid in args.ids:
        t0 = time.time()
        if args.remove:
            remove(oid)
            print(f"removed {oid}", flush=True)
        else:
            e = save(oid)
            print(f"saved {oid} ({e['label']}): {e['splats']:,} splats, mesh {e.get('mesh_faces', 0):,} faces, "
                  f"{time.time() - t0:.1f} s -> {LIBRARY / oid}", flush=True)


if __name__ == "__main__":
    main()
