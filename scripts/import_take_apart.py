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
- ai3d: every Higgsfield unit scan2service shows in its room (its own hf3d index), once per file, minus poor fits.

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
UNITS = {"B019p+A069_r2.glb", "B013+B013m.glb", "B156+B156m_r2.glb", "B149+B149m.glb", "A101+G071.glb"}  # split units checked here
# AI 3D: scan2service's own Higgsfield index (read only), every unit it fitted in place
HF3D = Path.home() / "sinceai-2026/data/private/out/equip_v1/assets_export/hf3d"
MIN_IOU = 0.5  # silhouette fit against the real photos; below it the shape is not trusted
ROOM_TEXTURE_PX = 1024  # AI 3D models in the room get their texture at this size (GPU memory on an 8 GB machine);
                        # the split units for Take apart keep their full 2048 px texture


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


def shrink_glb(src: Path, dst: Path, px: int):
    """Copy a GLB with every embedded image scaled down to at most px on its long side (re-encoded as JPEG)."""
    import io
    from PIL import Image
    b = src.read_bytes()
    jlen = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jlen])
    blen = struct.unpack("<I", b[20 + jlen:24 + jlen])[0]
    binary = b[28 + jlen:28 + jlen + blen]
    if len(j.get("buffers", [])) != 1:
        raise ValueError(f"{src.name}: one buffer expected")
    images = {im["bufferView"]: im for im in j.get("images", []) if "bufferView" in im}
    out = bytearray()
    for i, v in enumerate(j["bufferViews"]):
        data = binary[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        if i in images:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if max(img.size) > px:
                img.thumbnail((px, px), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            data = buf.getvalue()
            images[i]["mimeType"] = "image/jpeg"
        out += b"\0" * (-len(out) % 4)
        v["byteOffset"], v["byteLength"] = len(out), len(data)
        out += data
    out += b"\0" * (-len(out) % 4)
    j["buffers"][0]["byteLength"] = len(out)
    js = json.dumps(j, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    dst.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(out))
                    + struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(out), b"BIN\0") + bytes(out))


def pick_ai3d(index: dict) -> tuple[dict, dict]:
    """The units scan2service shows (its objects' units: a pump + motor once, the _r2 redo where there is one), minus
    hidden ones and poor silhouette fits. Returns ({file name: entry}, {unit: why skipped})."""
    units, skipped = index["units"], {}
    for name, why in {**index.get("skipped", {}), **index.get("hidden", {})}.items():
        skipped[name] = why
    for name in units:
        redo = f"{name}_r2"
        if redo in units and name not in skipped:
            skipped[name] = f"superseded by {redo}"
    out = {}
    for name in sorted({o["unit"] for o in index["objects"].values()}):
        u = units[name]
        if name in index.get("hidden", {}):
            continue
        if (u.get("silhouette_iou") or 0) < MIN_IOU:
            skipped[name] = f"silhouette IoU {u.get('silhouette_iou')} < {MIN_IOU}"
            continue
        file = Path(u["file"]).name
        out[file] = {"file": f"ai3d/{file}", "unit": name, "ids": u.get("parts", []), "label": u.get("label"),
                     "silhouette_iou": u.get("silhouette_iou"), "src": file}
    for name, u in units.items():  # redos the index has but does not show (hidden or below the bar)
        if name not in skipped and Path(u["file"]).name not in out and name not in {o["unit"] for o in index["objects"].values()}:
            skipped[name] = "not shown by scan2service" + (f" (IoU {u.get('silhouette_iou')})" if u.get("silhouette_iou") else "")
    return out, skipped


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
    ai3d, skipped = pick_ai3d(json.loads((HF3D / "index.json").read_text()))
    for e in ai3d.values():
        shrink_glb(HF3D / e.pop("src"), OUT / e["file"], ROOM_TEXTURE_PX)
    doc = {"version": 3, "source": "SinceAI-2026/mvp demo/web/public/data/s2s (scan2service, raw PLY coordinates)",
           "frame": cards.get("frame"), "models": models, "ai3d": ai3d}
    (OUT / "index.json").write_text(json.dumps(doc, indent=1))
    print(f"wrote {OUT.relative_to(ROOT)}/index.json: Take apart {', '.join(models)}; AI 3D {', '.join(ai3d)}")
    for name, why in skipped.items():
        print(f"  skipped {name}: {why}")


if __name__ == "__main__":
    main()
