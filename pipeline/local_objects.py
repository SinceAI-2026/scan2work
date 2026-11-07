"""Stage 2 (local option) - objects from the open-model pipeline in local_models/, instead of perception.py.

Selected with VALMET_SEGMENTER=local (config.SEGMENTER). local_models/ segments the room with SAM 2.1 on the
walkthrough video, stitches the masks into 3D objects by view consensus and names them with SigLIP 2, DINOv2,
OWLv2 and a linear probe, all open weights that run on this machine (local_models/README.md). It writes
objects.json and objects.npz; this module turns them into the twin's asset list, so the rest of the pipeline
(Excel mapping, navigation, diagnostics, viewer) is the same for both segmenters.

Reads C.LOCAL_OBJECTS_DIR/objects.{json,npz}: per object an id, a class (one of the client's asset classes or
"unclassified"), a free-text label, a confidence, and its Gaussians as PLY rows (npz rows + offsets). Each
object's box is refitted here on the levelled splat with perception's box fitter, so both segmenters' boxes
are made the same way. No hosted API is called.
"""
from __future__ import annotations

import json

import numpy as np

from . import config as C
from .perception import _compute_obb_2d
from .semantic_labels import CLASSES

# local_models' asset classes (pipeline/classes.json) -> this pipeline's class keys (semantic_labels.CLASSES)
ASSET_CLASS = {"pump": "pump", "valve": "control_valve", "motor": "electric_motor",
               "pressure transmitter": "instrument", "vibration sensor": "instrument"}
# local_models' free-text labels (classes.json other_prompts) -> class keys; anything else is "other"
LABEL_CLASS = {
    "pipe": "pipe_horizontal", "pipe elbow": "pipe_fitting", "cable tray": "structure",
    "pressure vessel": "tank_vessel", "expansion vessel": "tank_vessel", "tank": "tank_vessel",
    "electrical cabinet": "electrical_cabinet", "control panel": "electrical_cabinet",
    "radiator": "heat_exchanger", "washing machine": "appliance",
    "bucket": "container", "plastic pail": "container", "spray bottle": "container",
    "roll container": "cart_trolley", "wire cage cart": "cart_trolley", "hand truck": "cart_trolley",
    "sack truck": "cart_trolley", "pallet jack": "cart_trolley",
    "cardboard box": "box_crate", "plastic crate": "box_crate",
    "coil of cable": "hose_cable", "cable reel": "hose_cable",
    "door": "structure", "fire extinguisher": "other",
    # equipment names the zero-shot namer gave but the classifier held back from an asset class (low confidence):
    # the twin shows the name, so the class follows it; class_confidence keeps the doubt
    "valve": "control_valve", "pump": "pump", "motor": "electric_motor", "electric motor": "electric_motor",
    "pressure transmitter": "instrument", "vibration sensor": "instrument",
}


def _class_key(obj: dict) -> str:
    if obj.get("class") in ASSET_CLASS:
        return ASSET_CLASS[obj["class"]]
    key = LABEL_CLASS.get(str(obj.get("label", "")).lower(), "other")
    if key == "pipe_horizontal" and obj["size"][1] > max(obj["size"][0], obj["size"][2]):
        key = "pipe_vertical"
    return key


def load(splat: dict) -> dict:
    """Assets in perception.extract_assets' format (plus label provenance) and the per-splat object index."""
    folder = C.LOCAL_OBJECTS_DIR
    objects = json.loads((folder / "objects.json").read_text())
    stored = np.load(folder / "objects.npz")
    rows, offsets = stored["rows"], stored["offsets"]

    # PLY row -> index in the levelled splat (ingest drops corrupt rows, so they differ)
    ply_rows = splat["ply_rows"]
    to_splat = np.full(int(max(ply_rows.max(), rows.max())) + 1, -1, dtype=np.int64)
    to_splat[ply_rows] = np.arange(len(ply_rows))

    xyz, rgb = splat["xyz"], splat["rgb"]
    splat_asset_idx = np.full(len(xyz), -1, dtype=np.int32)
    assets, skipped = [], 0
    for k, obj in enumerate(objects):
        idx = to_splat[rows[offsets[k]:offsets[k + 1]]]
        idx = idx[idx >= 0]
        if len(idx) < 12:
            skipped += 1
            continue
        key = _class_key(obj)
        cls, class_label, _, movable = CLASSES[key]
        if key == "pipe_horizontal" and float(np.median(xyz[idx, 1])) > C.OVERHEAD_SPLIT_Y:
            cls = "overhead_pipe"
        free_text = str(obj.get("label") or class_label)
        n = len(assets)
        assets.append({
            "id": f"OBJ-{n + 1:03d}",
            "tier": "overhead" if cls == "overhead_pipe" else "equipment",
            "class": cls,
            "geo_class": cls,
            "label": free_text[:1].upper() + free_text[1:],
            "class_label": class_label,
            "class_confidence": round(float(obj.get("confidence", 0.5)), 2),
            "n_gaussians": int(len(idx)),
            "mean_rgb": [round(float(x), 3) for x in rgb[idx].mean(0)],
            "movable": bool(movable),
            "label_source": f"local:{obj.get('label_source') or 'zero_shot'}",
            "local_id": obj["id"],
            "excel_class": obj.get("excel_class"),
            **_compute_obb_2d(xyz[idx]),
        })
        splat_asset_idx[idx] = n

    print(f"   [local] {len(assets)} objects from {folder} ({skipped} with no Gaussians in the levelled splat skipped)")
    return {"assets": assets, "splat_asset_idx": splat_asset_idx,
            "stats": {"segmenter": "local", "source": str(folder.relative_to(C.ROOT) if folder.is_relative_to(C.ROOT) else folder),
                      "objects_read": len(objects), "skipped": skipped}}
