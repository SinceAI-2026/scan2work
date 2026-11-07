"""Which images label best - splat renders, real walkthrough photos, both, or a 4-view collage?

On the hand-labelled sample (tests/gold_labels.json) compares Gemini answers on
  render : splat render with the 3D box (cached from the normal run)
  photo  : real video-frame crop with the projected 3D box
  both   : render + photo of the same object in one prompt
  collage: 2x2 of render + 2 photos from different angles + wide context photo, with an industrial glossary
Answers are cached under the keys the pipeline uses, so they are not paid for twice.
Note: the gold labels were judged from renders, which may slightly favour renders.
Run:  ./.venv/bin/python -m tests.ab_photos      then set VALMET_LABEL_IMAGES to the winner.
"""
import json
from pathlib import Path

from pipeline import config as C, gemini_labels as G, ingest, perception, walk_photos as WP
from pipeline.semantic_labels import CLASSES, label_key, rendered_crop

gold = [g for g in json.loads((Path(__file__).parent / "gold_labels.json").read_text())["items"] if g["label"]]
splat = ingest.level_scene(ingest.load_splat())
assets = {a["id"]: a for a in perception.extract_assets(splat)["assets"]}
sample = [assets[g["id"]] for g in gold]
WP.build(splat, sample, label_key, out_dir=WP.LABEL_DIR, set_field=False)
photo = {a["id"]: u for a, u in ((a, WP.data_url(a, label_key)) for a in sample) if u}
render = {a["id"]: u for a, u in ((a, rendered_crop(a)) for a in sample) if u}
G.label_batches([(a, photo[a["id"]]) for a in sample if a["id"] in photo], CLASSES, label_key,
                prefix=G.PHOTO_PREFIX, kind="photo", batch=C.GEMINI_PHOTO_BATCH)
G.label_batches([(a, [render[a["id"]], photo[a["id"]]]) for a in sample if a["id"] in photo and a["id"] in render],
                CLASSES, label_key, prefix=G.BOTH_PREFIX, kind="both", batch=C.GEMINI_PHOTO_BATCH)
coll = WP.build_collages(splat, sample, label_key, rendered_crop)
G.label_batches([(a, coll[a["id"]]) for a in sample if a["id"] in coll], CLASSES, label_key,
                prefix=G.COLLAGE_PREFIX, kind="collage", batch=C.GEMINI_COLLAGE_BATCH)

cache = G.D._load_cache()
modes = {"render": G.RENDER_PREFIX, "photo": G.PHOTO_PREFIX, "both": G.BOTH_PREFIX, "collage": G.COLLAGE_PREFIX}
hits, n, rows = {m: 0 for m in modes}, 0, []
for g in gold:
    a = assets[g["id"]]
    ans = {m: (cache.get(G.cache_key(label_key(a), p)) or {}).get("equipment_class", {}).get("choice") for m, p in modes.items()}
    if not all(ans.values()):                       # compare only objects every mode answered
        continue
    n += 1
    for m in modes:
        hits[m] += ans[m] == g["label"]
    if len(set(ans.values())) > 1:
        rows.append(f"  {g['id']}: gold={g['label']:15s} " + " ".join(f"{m}={ans[m]:15s}" for m in modes))
for m in modes:
    print(f"{m:7s}: {hits[m]}/{n} ({hits[m] / max(n, 1):.0%})")
print("disagreements:\n" + "\n".join(rows))
best = max(modes, key=lambda m: (hits[m], m == "render"))
print(f"\nBest: {best}.  Run the pipeline with:  VALMET_LABEL_IMAGES={best} bash scripts/check.sh")
