"""Label-free comparison of two object sets (e.g. main's 2D-first objects and this branch's 3D-first ones).

  python tools/evaluate.py geometry NAME OBJECTS_JSON   # duplicates, membership consensus, coverage
  python tools/evaluate.py names NAME OBJECTS_JSON      # name agreement across views (SigLIP 2)
  python tools/evaluate.py table NAME...                # the comparison table from the saved results

OBJECTS_JSON's directory must hold its objects.npz and the masks its views index: amg/ (this
branch) or detections/ with lifted.json (main). Both sets are measured the same way:
- duplicates: pairs sharing >= SAME of the smaller one's Gaussians, or whose boxes overlap that much;
- membership consensus: per object, the share of its Gaussians that most of its own views (frames
  with its masks that see the Gaussian) put inside its mask; median over objects;
- coverage: share of the non-structure Gaussians (not floor, ceiling or the walls' shell) in some object;
- name agreement: per object, the share of its saved views whose own top SigLIP 2 label (same
  prompts as pipeline/appearance.py) maps to its class; median over objects;
- counts, Excel-class count, confidence (or score) histogram.
Results go to data/pipeline/eval/<NAME>.json.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from geometry import FLIP, MIN_OPACITY, load_splat, project, repo, to_colmap  # noqa: E402

out_dir = repo / "data" / "pipeline" / "eval"
SAME = 0.5  # overlap share of the smaller object that makes a pair duplicates
STRUCTURE_BAND = 0.1  # m around the floor and ceiling heights
SHELL = 0.12  # m inside the room's outer planes (0.5/99.5 percentiles in x and z): the walls
MASK_HEIGHT, MASK_WIDTH = 270, 480  # both pipelines store masks at half of 960x540
BINS = [0, 0.2, 0.4, 0.6, 0.8, 1.0001]


def load(path):
    path = Path(path)
    objects = json.loads(path.read_text())
    stored = np.load(path.parent / "objects.npz")
    rows = [stored["rows"][a:b] for a, b in zip(stored["offsets"][:-1], stored["offsets"][1:])]
    return objects, rows


def mask_views(path, objects):
    """Per object {frame: [mask indices]} over all its masks, and the masks' directory."""
    path = Path(path)
    if (path.parent / "instances.json").exists():
        instances = {i["id"]: i for i in json.loads((path.parent / "instances.json").read_text())}
        views = []
        for o in objects:
            frames = {}
            for i in o["merged_from"]:
                for frame, indices in instances[i]["views"].items():
                    frames.setdefault(frame, []).extend(indices)
            views.append(frames)
        return views, path.parent / "amg"
    lifted = {o["id"]: o for o in json.loads((path.parent / "lifted.json").read_text())}
    views = []
    for o in objects:
        frames = {}
        for i in o["merged_from"]:
            for d in lifted[i]["detections"]:
                frames.setdefault(d[0], []).append(d[4])
        views.append(frames)
    return views, path.parent / "detections"


def box_points(o, n=6):
    """A grid of points filling an object's upright, yawed box."""
    grid = np.stack(np.meshgrid(*[np.linspace(-0.5, 0.5, n)] * 3, indexing="ij"), -1).reshape(-1, 3) * o["size"]
    yaw = o["yaw"]
    turn = np.array([[np.cos(yaw), np.sin(yaw)], [-np.sin(yaw), np.cos(yaw)]])
    xz = grid[:, [0, 2]] @ turn
    return np.stack([xz[:, 0], grid[:, 1], xz[:, 1]], 1) + o["center"]


def in_box(o, points):
    yaw = o["yaw"]
    turn = np.array([[np.cos(yaw), np.sin(yaw)], [-np.sin(yaw), np.cos(yaw)]])
    local = (points - o["center"])[:, [0, 2]] @ turn.T
    half = np.array(o["size"]) / 2 + 1e-6
    return (np.abs(local[:, 0]) <= half[0]) & (np.abs(local[:, 1]) <= half[2]) & (np.abs(points[:, 1] - o["center"][1]) <= half[1])


def duplicates(objects, rows):
    pairs = set()
    sets = [set(r.tolist()) for r in rows]
    for a in range(len(objects)):
        for b in range(a + 1, len(objects)):
            oa, ob = objects[a], objects[b]
            reach = (np.linalg.norm(oa["size"]) + np.linalg.norm(ob["size"])) / 2
            if np.linalg.norm(np.subtract(oa["center"], ob["center"])) > reach:
                continue
            if len(sets[a] & sets[b]) >= SAME * min(len(sets[a]), len(sets[b])):
                pairs.add((a, b))
                continue
            small, large = (oa, ob) if np.prod(oa["size"]) <= np.prod(ob["size"]) else (ob, oa)
            if in_box(large, box_points(small)).mean() >= SAME:
                pairs.add((a, b))
    return pairs


def geometry(name, path):
    objects, rows = load(path)
    views, masks_dir = mask_views(path, objects)
    _, centres, opacity = load_splat()
    kept = np.flatnonzero(opacity > MIN_OPACITY)
    world = centres[kept] @ FLIP.T
    kept_index = np.full(len(centres), -1)
    kept_index[kept] = np.arange(len(kept))
    from geometry import posed_frames
    posed = posed_frames()
    points = to_colmap(centres[kept])

    looks = [np.zeros(len(r), int) for r in rows]
    inside = [np.zeros(len(r), int) for r in rows]
    by_frame = {}
    for k, v in enumerate(views):
        for frame, indices in v.items():
            by_frame.setdefault(frame, []).append((k, indices))
    for frame, entries in by_frame.items():
        if frame not in posed:
            continue
        u, v, visible = project(posed[frame], points, MASK_HEIGHT, MASK_WIDTH)
        seen = np.zeros(len(kept), bool)
        seen[visible] = True
        data = np.load(masks_dir / f"{frame}.npz")
        stack = np.unpackbits(data["masks"], axis=-1)[..., :MASK_WIDTH].astype(bool)
        for k, indices in entries:
            g = kept_index[rows[k]]
            ok = (g >= 0)
            ok[ok] &= seen[g[ok]]
            looks[k] += ok
            mask = stack[indices].any(0)
            inside[k][ok] += mask[v[g[ok]], u[g[ok]]]
    consensus = [float(np.mean(i[l > 0] / l[l > 0] > 0.5)) if (l > 0).any() else np.nan for l, i in zip(looks, inside)]

    structure = (np.abs(world[:, 1] - json.loads((repo / "data" / "pipeline" / "nav.json").read_text())["floor"]) < STRUCTURE_BAND)
    structure |= np.abs(world[:, 1] - json.loads((repo / "data" / "pipeline" / "nav.json").read_text())["ceiling"]) < STRUCTURE_BAND
    for axis in (0, 2):
        low, high = np.percentile(world[:, axis], [0.5, 99.5])
        structure |= (world[:, axis] < low + SHELL) | (world[:, axis] > high - SHELL)
    covered = np.zeros(len(kept), bool)
    all_rows = kept_index[np.concatenate(rows)]
    covered[all_rows[all_rows >= 0]] = True

    pairs = duplicates(objects, rows)
    confidence = [o.get("confidence", o.get("score")) for o in objects]
    result = {
        "objects": len(objects),
        "excel_classed": sum(bool(o.get("excel_class")) for o in objects),
        "by_class": {c: sum(o["class"] == c for o in objects) for c in sorted({o["class"] for o in objects})},
        "duplicate_pairs": len(pairs),
        "objects_in_duplicate_pairs": len({x for p in pairs for x in p}),
        "membership_consensus_median": round(float(np.nanmedian(consensus)), 3),
        "membership_consensus_p25": round(float(np.nanpercentile(consensus, 25)), 3),
        "coverage_non_structure": round(float(covered[~structure].mean()), 4),
        "confidence_field": "confidence" if "confidence" in objects[0] else "score",
        "confidence_histogram": np.histogram(confidence, BINS)[0].tolist(),
    }
    save(name, result)


def names(name, path):
    from transformers import AutoModel, AutoProcessor

    sys.path.insert(0, str(repo / "pipeline"))
    import appearance
    import embed

    objects, _ = load(path)
    folder = Path(path).parent / "amg"
    folder = folder if folder.exists() else Path(path).parent / "detections"  # main's OWLv2 + SAM masks
    model = processor = None
    if not (Path(path).parent / "embeddings.npz").exists():
        model = AutoModel.from_pretrained(embed.NAMER).to(embed.DEVICE).eval()
        processor = AutoProcessor.from_pretrained(embed.NAMER)
    texts = appearance.label_texts()
    saved = 4  # main keeps 4 views per object; compare on the same number
    jobs = [(k, frame, indices) for k, o in enumerate(objects) for frame, _, indices in o["views"][:saved]]
    owner = [k for k, _, _ in jobs]
    stored = Path(path).parent / "embeddings.npz"
    if stored.exists():  # this branch: embed.py already embedded these exact views (same crops, same model)
        vectors, owner = stored_views(objects, Path(path).parent, saved)
    else:
        vectors = []
        for start in range(0, len(jobs), embed.BATCH):
            images = [image for _, frame, indices in jobs[start:start + embed.BATCH] for image in embed.crops(frame, indices, folder)[0]]
            vectors.append(embed.embed(model, processor, images, embed.siglip_features))
        vectors = np.concatenate(vectors).reshape(len(jobs), len(embed.SCALES), -1).mean(1)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    tops = [appearance.LABELS[int(np.argmax(p))] for p in appearance.view_probabilities(vectors, texts)]
    agreement = []
    for k, o in enumerate(objects):
        mine = [t for t, owner_k in zip(tops, owner) if owner_k == k]
        if mine:
            agree = [t == o["class"] if o["class"] in appearance.CLASSES else t not in appearance.CLASSES for t in mine]
            agreement.append(float(np.mean(agree)))
    agreement = np.array(agreement)
    classed = np.array([o["class"] in appearance.CLASSES for o in objects if o["views"]])
    save(name, {
        "name_agreement_median": round(float(np.median(agreement)), 3),
        "name_agreement_median_classed": round(float(np.median(agreement[classed])), 3) if classed.any() else None,
        "name_agreement_all_views_agree": round(float(np.mean(agreement == 1)), 3),
    })


def stored_views(objects, folder, saved):
    """Per-view SigLIP embeddings from embed.py for each object's first `saved` views (its biggest instance's)."""
    instances = [i["id"] for i in json.loads((folder / "instances.json").read_text())]
    stored = np.load(folder / "embeddings.npz")
    offsets = stored["view_offsets"]
    rows, owner = [], []
    for n, o in enumerate(objects):
        k = instances.index(o["merged_from"][0])
        mine = list(range(offsets[k], offsets[k + 1]))[:saved]
        rows += mine
        owner += [n] * len(mine)
    return stored["siglip_views"][rows], owner


def save(name, result):
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.json"
    merged = json.loads(target.read_text()) if target.exists() else {}
    merged.update(result)
    target.write_text(json.dumps(merged, indent=1))
    print(name, json.dumps(result))


def table(names_):
    results = {n: json.loads((out_dir / f"{n}.json").read_text()) for n in names_}
    keys = sorted({k for r in results.values() for k in r}, key=lambda k: list(next(iter(results.values()))).index(k)
                  if k in next(iter(results.values())) else 99)
    print("| metric | " + " | ".join(names_) + " |")
    print("|---|" + "---|" * len(names_))
    for k in keys:
        print(f"| {k} | " + " | ".join(str(results[n].get(k, "")) for n in names_) + " |")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "table":
        table(sys.argv[2:])
    else:
        {"geometry": geometry, "names": names}[mode](sys.argv[2], sys.argv[3])
