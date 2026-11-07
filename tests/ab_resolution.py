"""A/B test: does 2x render resolution improve Gemini labels? Uses 2 Gemini requests (39 objects).

Compares, on the hand-labelled sample, the existing 1x answers (cached from the normal run) with new
answers on 896 px renders (web/data/crops_2x, from: python -m scripts.render_crops --size 896 --out crops_2x).
Run:  ./.venv/bin/python -m tests.ab_resolution
"""
import base64
import json
from pathlib import Path

from pipeline import config as C, gemini_labels as G, ingest, perception
from pipeline.semantic_labels import CLASSES, label_key

gold = [g for g in json.loads((Path(__file__).parent / "gold_labels.json").read_text())["items"] if g["label"]]
splat = ingest.level_scene(ingest.load_splat())
assets = {a["id"]: a for a in perception.extract_assets(splat)["assets"]}
jobs = []
for g in gold:
    f = C.OUT_DIR / "crops_2x" / f"{g['id']}.jpg"
    jobs.append((assets[g["id"]], "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()))
G.label_batches(jobs, CLASSES, label_key, prefix="gemini-2x-v1")

cache = G.D._load_cache()
def acc(prefix):
    hit = n = 0
    for g in gold:
        ans = cache.get(G.cache_key(label_key(assets[g["id"]]), prefix))
        if not ans:
            continue
        n += 1
        hit += ans["equipment_class"]["choice"] == g["label"]
    return hit, n
for name, prefix in (("1x (448 px)", "gemini-v1"), ("2x (896 px)", "gemini-2x-v1")):
    h, n = acc(prefix)
    print(f"{name}: {h}/{n} correct ({h / max(n, 1):.0%}) on raw crop labels")
