"""Copy the in-room Take apart data from the MVP into web/data/take_apart/ (git-ignored, like the rest of web/data).

Take apart ported from Mikael Lehtonen's scan2service (web/parts.js) via SinceAI-2026/mvp; UX after Rasmus Mäkinen's
pump explode.

Reads ~/sinceai-mvp/demo/web/public/data/s2s (cards.json and assets/hf3d/parts/: unit models split into named parts, in
raw PLY coordinates of Equipment Room.ply) and writes web/data/take_apart/index.json plus the split GLBs. Devices are
matched by Excel tag (the same Valmet workbook): a unit belongs to the tag of its first scan2service object (the pump
casing); a cards.json "standin" (P-001 shown as the P-005 unit) carries only its own device's issue in the viewer.

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
DEVICES = {"P-001"}  # the devices checked in this viewer so far (a unit model can serve more tags)


def build(cards: dict, parts_index: dict) -> dict:
    by_id = cards["cards"]
    models, devices, used = {}, {}, set()
    for entry in parts_index["models"].values():
        name = Path(entry["file"]).name
        models[name] = {**entry, "file": name}
        used.update(entry["ids"])
        tag = (by_id.get(entry["ids"][0]) or {}).get("tag")
        if tag:
            devices[tag] = {"model": name, "card": entry["ids"][0], "how": "tag"}
    for m in cards.get("match", {}).values():
        if m.get("how") == "standin" and m.get("for") and m["for"] not in devices:
            model = next((k for k, e in models.items() if m["s2s"] in e["ids"]), None)
            if model:
                devices[m["for"]] = {"model": model, "card": m["s2s"], "how": "standin"}
    devices = {tag: d for tag, d in devices.items() if tag in DEVICES}
    unit_cards = {i: {"tag": by_id[i].get("tag"), "name": by_id[i].get("name"), "center": by_id[i]["obb"]["center"]}
                  for i in sorted(used) if i in by_id}
    return {"version": 1, "source": f"{SRC} (scan2service cards + parts, raw PLY coordinates)",
            "frame": cards.get("frame"), "devices": devices, "cards": unit_cards, "models": models}


def main():
    cards = json.loads((SRC / "cards.json").read_text())
    parts_index = json.loads((SRC / "assets/hf3d/parts/index.json").read_text())
    doc = build(cards, parts_index)
    OUT.mkdir(parents=True, exist_ok=True)
    for name in doc["models"]:
        shutil.copyfile(SRC / "assets/hf3d/parts" / name, OUT / name)
    (OUT / "index.json").write_text(json.dumps(doc, indent=1))
    for tag, d in sorted(doc["devices"].items()):
        print(f"  {tag}: {d['model']} ({d['how']}, card {d['card']})")
    print(f"wrote {OUT.relative_to(ROOT)}/index.json and {len(doc['models'])} models")


if __name__ == "__main__":
    main()
