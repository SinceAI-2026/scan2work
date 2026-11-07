"""Copy the in-room Take apart and AI 3D data from the MVP into web/data/take_apart/ (git-ignored, like web/data).

Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
pump explode.

Reads ~/sinceai-mvp/demo/web/public/data/s2s (cards.json, assets/hf3d/: Higgsfield models, and assets/hf3d/parts/: unit
models split into named parts, all in raw PLY coordinates of Equipment Room.ply) and writes
web/data/take_apart/index.json, parts/*.glb and ai3d/*.glb. Devices are matched by Excel tag (the same Valmet workbook):

- devices: Take apart. A split unit belongs to the tag of its first scan2service object (the pump casing); a cards.json
  "standin" (P-001 shown as the P-005 unit) carries only its own device's issue in the viewer.
- ai3d: every tag whose card's main view is a Higgsfield model (ai_multiview). A unit shared by a pump and its motor is
  listed once, at the pump; a stand-in gets its own copy.

    .venv/bin/python -m scripts.import_take_apart [path/to/s2s]
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "sinceai-mvp/demo/web/public/data/s2s"
OUT = ROOT / "web" / "data" / "take_apart"
DEVICES = {"P-001"}  # the Take apart devices checked in this viewer so far (a unit model can serve more tags)
AI_KINDS = ("ai_multiview", "ai_trellis")


def build(cards: dict, parts_index: dict) -> dict:
    by_id = cards["cards"]
    standins = [m for m in cards.get("match", {}).values() if m.get("how") == "standin" and m.get("for")]
    models, devices, used = {}, {}, set()
    for entry in parts_index["models"].values():
        name = Path(entry["file"]).name
        models[name] = {**entry, "file": f"parts/{name}"}
        used.update(entry["ids"])
        tag = (by_id.get(entry["ids"][0]) or {}).get("tag")
        if tag:
            devices[tag] = {"model": name, "card": entry["ids"][0], "how": "tag"}
    for m in standins:
        model = next((k for k, e in models.items() if m["s2s"] in e["ids"]), None)
        if model and m["for"] not in devices:
            devices[m["for"]] = {"model": model, "card": m["s2s"], "how": "standin"}
    devices = {tag: d for tag, d in devices.items() if tag in DEVICES}

    # AI 3D: the card's Higgsfield model; pumps first so a pump + motor unit lands on the pump
    ai3d, owner = {}, {}
    rank = {"PROCESS_PUMP": 0, "ELECTRIC_MOTOR": 2}
    for cid, c in sorted(by_id.items(), key=lambda kv: (rank.get(kv[1].get("class"), 1), kv[0])):
        view = next((v for v in c.get("views_3d", []) if v.get("kind") in AI_KINDS and v.get("url")), None)
        if not c.get("tag") or not view:
            continue
        file = Path(view["url"]).name
        if file in owner:
            continue  # the unit is drawn at its pump already
        owner[file] = c["tag"]
        ai3d[c["tag"]] = {"file": f"ai3d/{file}", "card": cid, "how": "tag", "label": view.get("label"), "src": view["url"]}
    for m in standins:
        src = next((e for e in ai3d.values() if e["card"] == m["s2s"]), None)
        if src and m["for"] not in ai3d:
            ai3d[m["for"]] = {**src, "how": "standin"}
    used.update(e["card"] for e in ai3d.values())
    unit_cards = {i: {"tag": by_id[i].get("tag"), "name": by_id[i].get("name"), "center": by_id[i]["obb"]["center"]}
                  for i in sorted(used) if i in by_id}
    return {"version": 2, "source": "SinceAI-2026/mvp demo/web/public/data/s2s (scan2service cards, raw PLY coordinates)",
            "frame": cards.get("frame"), "devices": devices, "ai3d": ai3d, "cards": unit_cards, "models": models}


def main():
    cards = json.loads((SRC / "cards.json").read_text())
    parts_index = json.loads((SRC / "assets/hf3d/parts/index.json").read_text())
    doc = build(cards, parts_index)
    for sub in ("parts", "ai3d"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    for name in doc["models"]:
        shutil.copyfile(SRC / "assets/hf3d/parts" / name, OUT / "parts" / name)
    prefix = cards.get("url_prefix_replaced", "data/assets/")
    for e in doc["ai3d"].values():
        shutil.copyfile(SRC / "assets" / e.pop("src")[len(prefix):], OUT / e["file"])
    (OUT / "index.json").write_text(json.dumps(doc, indent=1))
    for tag, d in sorted(doc["devices"].items()):
        print(f"  take apart {tag}: {d['model']} ({d['how']}, card {d['card']})")
    tags = [t + ("*" if e["how"] == "standin" else "") for t, e in sorted(doc["ai3d"].items())]
    print(f"  ai 3d (* stand-in): {', '.join(tags)}")
    print(f"wrote {OUT.relative_to(ROOT)}/index.json, {len(doc['models'])} split models, {len(doc['ai3d'])} AI 3D models")


if __name__ == "__main__":
    main()
