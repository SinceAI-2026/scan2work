"""Reviewed labels over explicit source rows, separate from canonical ownership.

This small overlay never rewrites a twin, expands a mask at runtime, binds an Excel
record or removes a whole neighbouring object. Unsupported datasets fail closed.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

import numpy as np


WASHER = json.loads((Path(__file__).parent / "annotations/washing-machine.json").read_text())


@lru_cache(maxsize=32)
def _file_digest(path: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    stat = path.stat()
    return _file_digest(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _resolve(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("Manual annotation source file is missing or outside the active dataset")
    return path


def load_annotations(data_dir: Path, twin: dict, *, spec: dict = WASHER) -> dict:
    """Return a read-only projection; only this recorded render/source order applies."""
    result = {"schema_version": 1, "annotations": [], "canonical_assets_modified": False,
              "source_sha256": twin.get("scene", {}).get("source_sha256"),
              "notice": "Manual labels use separate explicit source-row masks; read each boundary notice. No workbook readings."}
    scene = twin.get("scene", {})
    if scene.get("dataset_kind") != "wael-original-baseline" or scene.get("source_sha256") != spec["source_sha256"]:
        return {**result, "available": False, "reason": "No manual annotations registered for this dataset"}
    root = Path(data_dir).resolve()
    try:
        for name, expected in spec["files"].items():
            if sha256(_resolve(root, name)) != expected:
                raise ValueError("Manual annotation render/source identity mismatch")
        rows = np.load(_resolve(root, "splat_source_rows.npy"), allow_pickle=False)
        if rows.ndim != 1 or rows.dtype.kind not in "ui" or len(rows) != spec["rendered_rows"]:
            raise ValueError("Manual annotation source row map is invalid")
        requested = np.asarray(spec["raw_source_rows"], dtype="<u4")
        if len(requested) != spec["source_rows"] or len(np.unique(requested)) != len(requested):
            raise ValueError("Manual annotation source rows are duplicated or incomplete")
        render_indices = np.flatnonzero(np.isin(rows, requested))
        if len(render_indices) != len(requested):
            raise ValueError("Manual annotation membership is incomplete in this render")
        selected = rows[render_indices]
        if len(np.unique(selected)) != len(selected):
            raise ValueError("Manual annotation source rows are duplicated")
        annotation = copy.deepcopy(spec["annotation"])
        annotation.update(id=spec["id"], label=spec["label"], name=spec["label"], **{"class": spec["class"]},
            kind="manual_annotation", mapped=False, condition_state="not_instrumented",
            render_indices=render_indices.tolist(), rendered_rows=spec["rendered_rows"],
            source_sha256=spec["source_sha256"], source_regions=annotation.get("source_regions", [spec["region_id"]]),
            source_row_count=len(selected), identity_status="manually_reviewed", scale_verified=False,
            provenance={**annotation.get("provenance", {}),
                        "native_source_rows_sha256": hashlib.sha256(np.sort(selected).astype("<u4").tobytes()).hexdigest()},
            export_url=f"/api/manual-annotations/{spec['id']}/native.ply")
        return {**result, "available": True, "rendered_rows": spec["rendered_rows"], "annotations": [annotation]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**result, "available": False, "reason": str(exc)}


def export_native(data_dir: Path, twin: dict, annotation_id: str, *, spec: dict = WASHER) -> bytes:
    """Download original Gaussian records; no library or mutable dataset writes."""
    if annotation_id != spec["id"]:
        raise ValueError("Unknown manual annotation")
    projection = load_annotations(data_dir, twin, spec=spec)
    if not projection["annotations"]:
        raise ValueError(projection.get("reason", "Manual annotation unavailable"))
    root = Path(data_dir).resolve()
    source = _resolve(root, twin["scene"]["source_ply"])
    if sha256(source) != spec["source_sha256"]:
        raise ValueError("Native scan checksum mismatch; export refused")
    # Read the native float-property header without importing configured inference modules.
    with source.open("rb") as handle:
        lines = []
        for _ in range(1024):
            line = handle.readline()
            if not line or len(line) > 4096:
                raise ValueError("Invalid native PLY header")
            lines.append(line)
            if line == b"end_header\n":
                break
        else:
            raise ValueError("Native PLY header too long")
        header = b"".join(lines)
    if b"format binary_little_endian 1.0\n" not in header:
        raise ValueError("Unsupported native PLY format")
    count_line = next((line for line in lines if line.startswith(b"element vertex ")), None)
    if not count_line:
        raise ValueError("Native PLY has no vertices")
    count = int(count_line.split()[2]); properties = [line.split() for line in lines if line.startswith(b"property ")]
    if not properties or any(len(p) != 3 or p[1] != b"float" for p in properties):
        raise ValueError("Unsupported native PLY properties")
    stride = 4 * len(properties)
    if source.stat().st_size != len(header) + count * stride:
        raise ValueError("Native PLY size mismatch")
    mapping = np.load(_resolve(root, "splat_source_rows.npy"), allow_pickle=False)
    selected = np.sort(mapping[projection["annotations"][0]["render_indices"]])
    if len(selected) and int(selected[-1]) >= count:
        raise ValueError("Manual annotation row outside native scan")
    native = np.memmap(source, dtype=f"V{stride}", mode="r", offset=len(header), shape=(count,))
    output_header = re.sub(rb"(?m)^element vertex \d+$", b"element vertex " + str(len(selected)).encode(), header)
    return output_header + native[selected].tobytes()
