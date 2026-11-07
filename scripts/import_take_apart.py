"""Copy the in-room Take apart and AI 3D data from the MVP into web/data/take_apart/ (git-ignored, like web/data).

Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
pump explode.

Reads ~/sinceai-mvp/demo/web/public/data/s2s (cards.json, assets/hf3d/: Higgsfield models, and assets/hf3d/parts/: unit
models split into named parts) and writes web/data/take_apart/index.json, parts/*.glb and ai3d/*.glb. The models were
fitted in raw PLY coordinates of Equipment Room.ply, the same scan this viewer levels, so each one stands exactly where
its object is: the viewer matches models to its own objects by POSITION (an object whose centre lies inside the model's
box), never by tag (the two repos tagged the Excel devices onto the scan independently). Each split unit and part gets
its raw box here, from the GLB's own POSITION bounds.

- models: split units for Take apart (UNITS lists the ones checked in this viewer).
- ai3d: every Higgsfield model (ai_multiview) of a scan2service device card, once per file.

    .venv/bin/python -m scripts.import_take_apart [path/to/s2s]
"""
from __future__ import annotations

import json
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "sinceai-mvp/demo/web/public/data/s2s"
OUT = ROOT / "web" / "data" / "take_apart"
UNITS = {"B019p+A069_r2.glb"}  # inline pump 2 (motor + casing): this viewer's M-005 and the casing below it
AI_KINDS = ("ai_multiview", "ai_trellis")


def glb_boxes(path: Path) -> dict:
    """{node name: [min, max]} over each top-level node's meshes, plus "*" for the whole model (raw PLY)."""
    b = path.read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + n])
    nodes = j["nodes"]
    if any(k in nd for nd in nodes for k in ("matrix", "translation", "rotation", "scale")):
        raise ValueError(f"{path.name}: node transforms are not handled")

    def box(i):
        lo, hi = [float("inf")] * 3, [float("-inf")] * 3
        for k in [i, *nodes[i].get("children", [])]:
            if "mesh" in nodes[k]:
                for p in j["meshes"][nodes[k]["mesh"]]["primitives"]:
                    a = j["accessors"][p["attributes"]["POSITION"]]
                    lo, hi = [min(x, y) for x, y in zip(lo, a["min"])], [max(x, y) for x, y in zip(hi, a["max"])]
        return [[round(v, 5) for v in lo], [round(v, 5) for v in hi]]

    out = {nodes[i].get("name"): box(i) for i in j["scenes"][j.get("scene", 0)]["nodes"]}
    out["*"] = [[min(bx[0][k] for bx in out.values()) for k in range(3)], [max(bx[1][k] for bx in out.values()) for k in range(3)]]
    return out


def main():
    cards = json.loads((SRC / "cards.json").read_text())
    parts_index = json.loads((SRC / "assets/hf3d/parts/index.json").read_text())
    for sub in ("parts", "ai3d"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    models = {}
    for entry in parts_index["models"].values():
        name = Path(entry["file"]).name
        if name not in UNITS:
            continue
        shutil.copyfile(SRC / "assets/hf3d/parts" / name, OUT / "parts" / name)
        boxes = glb_boxes(OUT / "parts" / name)
        parts = [{**p, "box": boxes[p["node"]]} for p in entry["parts"] if p["node"] in boxes]
        models[name] = {**entry, "file": f"parts/{name}", "box": boxes["*"], "parts": parts}
    prefix = cards.get("url_prefix_replaced", "data/assets/")
    ai3d = {}
    for cid, c in sorted(cards["cards"].items()):
        view = next((v for v in c.get("views_3d", []) if v.get("kind") in AI_KINDS and v.get("url")), None)
        if not view or not c.get("tag"):  # devices only (an Excel tag in scan2service), not tanks or loose items
            continue
        name = Path(view["url"]).name
        if name in ai3d:
            ai3d[name]["cards"].append(cid)
            continue
        shutil.copyfile(SRC / "assets" / view["url"][len(prefix):], OUT / "ai3d" / name)
        ai3d[name] = {"file": f"ai3d/{name}", "cards": [cid], "label": view.get("label")}  # boxes: the viewer, on load
    doc = {"version": 3, "source": "SinceAI-2026/mvp demo/web/public/data/s2s (scan2service, raw PLY coordinates)",
           "frame": cards.get("frame"), "models": models, "ai3d": ai3d}
    (OUT / "index.json").write_text(json.dumps(doc, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}/index.json: Take apart {', '.join(models)}; {len(ai3d)} AI 3D models")


if __name__ == "__main__":
    main()
