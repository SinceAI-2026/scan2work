"""Step 4: name the 3D instances by fusing five signals, in a fixed cascade.

0. Merge: look-alike instances that touch and that views show as one (pipeline/similarity.py) are
   one object; look-alikes the views keep apart are twins and stay separate.
1. Junk: an instance whose crops with surroundings look most like a pipe, wall or sticker, clearly
   (JUNK_MIN), is dropped. (Room structure was already dropped by its geometry in cluster.py.)
2. Plausibility gate: classes its size rules out (pipeline/plausibility.py) are off the table.
3. Appearance: the best remaining asset class by SigLIP 2 on the masked crops alone (the object on
   grey; pipeline/appearance.py), over the classes and the other things (junk is step 1's question),
   if it scores at least CONFIDENT and beats every non-class label. The crops with surroundings
   serve the junk check (around a valve they mostly show pipe) and break ties: when the masked
   crops name nothing, a class the surroundings name clearly is taken if the masked crops rank it
   in their top two (a pump on grey reads as a bucket; in its place it reads as a pump).
4. Detector: an object still without a class takes the class main's OWLv2 detections back in at
   least half its views (pipeline/detector.py), if its size fits and SigLIP ranks that class in its
   top two, so the detector can tip a close call but not overrule what it looks like.
5. Hold back: a class that fewer than half the object's views agree on (pipeline/consistency.py)
   and the detector doesn't back either is withdrawn: "unclassified", with the reason shown.

Confidence is how sure the object is of an asset class: for a named one, the mean of the signals
that speak to its class (appearance strength, size fit, view agreement, detector support); for an
unclassified one, how close its best class came (low). "separation" is a different question: how
surely it is one separate thing (the share of its Gaussians most of its views put inside it).

Reads data/pipeline/instances.{json,npz}, views.json, embeddings.npz, evidence.npz and main's
detections. Writes objects.json (the viewer's format plus confidence, separation, reasons, signals),
objects.npz (Gaussians as PLY rows, SigLIP and DINOv2 embeddings, for catalog.py) and rejected.json.
"""
import json
from collections import Counter

import numpy as np

import appearance
import consistency
import detector
import plausibility
import similarity
from appearance import CLASSES, JUNK, LABELS
from geometry import FLIP, fit_box, load_splat, repo

pipeline = repo / "data" / "pipeline"

UNCLASSIFIED = "unclassified"
JUNK_MIN = 0.5  # junk must be the top label with at least this probability to drop an instance
CONFIDENT = 0.2  # class probability (softmax over 31 labels, chance ~0.03) to name an instance; main uses the same
APPEARANCE_FULL = 0.5  # a class probability this high counts as full appearance confidence
TOP_CLASSES = 2  # steps 3-4: a tie-breaking or detector class must be among SigLIP's top this many classes
VIEWS = 5  # views kept per merged object (pipeline/embed.py's count)
SAVED_CROPS = 4  # crops listed per object (pipeline/embed.py saves this many per instance)

config = json.loads((repo / "pipeline" / "classes.json").read_text())
excel = {c["name"]: c["excel_class"] for c in config["classes"]}


def unit(v):
    return v / np.linalg.norm(v)


def load():
    instances = json.loads((pipeline / "instances.json").read_text())
    stored = np.load(pipeline / "instances.npz")
    rows = [stored["rows"][a:b] for a, b in zip(stored["offsets"][:-1], stored["offsets"][1:])]
    views = json.loads((pipeline / "views.json").read_text())
    emb = np.load(pipeline / "embeddings.npz")
    offsets = emb["view_offsets"]
    scales = emb["siglip_scales"].astype(np.float32)  # view x scale; scale 0 is the masked crop
    masked = scales[:, 0]
    context = scales[:, 1:].mean(1)
    context /= np.linalg.norm(context, axis=1, keepdims=True)
    split = lambda a: [a[i:j] for i, j in zip(offsets[:-1], offsets[1:])]
    evidence = np.load(pipeline / "evidence.npz")
    inside = [(evidence["inside"][a:b] > 0.5 * evidence["looks"][a:b]) for a, b in
              zip(evidence["offsets"][:-1], evidence["offsets"][1:])]
    return instances, rows, views, split(masked), split(context), split(emb["siglip_views"]), emb["dino"], inside


def merge(instances, dino):
    """Groups of instance indices to fuse into one object, and per instance its twins."""
    index = {inst["id"]: k for k, inst in enumerate(instances)}
    parent = list(range(len(instances)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    twins = {k: [] for k in range(len(instances))}
    for k, inst in enumerate(instances):
        for other, touch, observers, supporters in inst["touching"]:
            j = index[other]
            if j <= k:
                continue
            verdict = similarity.merge_verdict(float(dino[k] @ dino[j]), touch, observers, supporters)
            if verdict == "merge":
                parent[root(k)] = root(j)
            elif verdict == "twin":
                twins[k].append(other)
                twins[j].append(inst["id"])
    groups = {}
    for k in range(len(instances)):
        groups.setdefault(root(k), []).append(k)
    return list(groups.values()), twins


def name(masked, context, box):
    """Steps 1-3 for one object: (class or None, junk?, plausibility per class, the reasons so far)."""
    if appearance.judge(context).verdict == JUNK and context[-1] >= JUNK_MIN:
        return None, True, {}, [f"with surroundings looks like junk ({context[-1]:.2f}) → dropped"]
    looks = appearance.judge(masked)
    fit = {c: plausibility.judge(box, c) for c in CLASSES}
    allowed = [k for k, c in enumerate(CLASSES) if fit[c].verdict != "out"]
    others = masked[len(CLASSES):-1].max()
    if allowed:
        best = max(allowed, key=lambda k: masked[k])
        if masked[best] >= CONFIDENT and masked[best] >= others:
            return CLASSES[best], False, fit, [f"looks like {CLASSES[best]} ({masked[best]:.2f})", fit[CLASSES[best]].reason]
        # Tie-breaker: the crops with surroundings may name a class the masked ones rank in their top two.
        best = max(allowed, key=lambda k: context[k])
        ranked = sorted(allowed, key=lambda k: -masked[k])[:TOP_CLASSES]
        if context[best] >= CONFIDENT and context[best] >= context[len(CLASSES):-1].max() and best in ranked:
            return CLASSES[best], False, fit, [f"with surroundings looks like {CLASSES[best]} ({context[best]:.2f}), "
                                               f"masked ranks it #{ranked.index(best) + 1}", fit[CLASSES[best]].reason]
    if looks.verdict in CLASSES and fit[looks.verdict].score == 0:
        return None, False, fit, [f"{looks.reason} but {fit[looks.verdict].reason} → unclassified"]
    return None, False, fit, [looks.reason]


def views_of(m):
    """Per-view probabilities of the crops that named the object (masked unless the surroundings broke a tie)."""
    return m["context_views"] if m["reasons"][0].startswith("with surroundings") else m["per_view"]


def second_opinion(m, signal, said):
    """Steps 4-5: take `said` if the signal backs it, its size fits and SigLIP ranks it in its top two."""
    if m["class"] or said not in CLASSES:
        return False
    ranked = [CLASSES[k] for k in np.argsort(-m["probabilities"][:len(CLASSES)])]
    if m["fit"][said].score > 0 and said in ranked[:TOP_CLASSES]:
        m["class"] = said
        m["reasons"] = [signal.reason, f"SigLIP ranks {said} #{ranked.index(said) + 1}", m["fit"][said].reason]
        return True
    return False


def main():
    instances, rows, views, masked_views, context_views, view_embeddings, dino, inside = load()
    texts = appearance.label_texts()
    support = detector.view_support(instances)
    _, centres, _ = load_splat()
    world = centres @ FLIP.T

    groups, twins = merge(instances, dino)
    print(f"merge: {len(instances)} instances -> {len(groups)} objects")
    merged = []
    for group in sorted(groups, key=lambda g: -sum(len(rows[k]) for k in g)):
        group = sorted(group, key=lambda k: -len(rows[k]))
        object_rows = np.unique(np.concatenate([rows[k] for k in group]))
        object_views = [v for k in group for v in views[k]][:VIEWS]  # the biggest member's best views first
        vectors = np.concatenate([view_embeddings[k] for k in group])
        per_view = appearance.view_probabilities(np.concatenate([masked_views[k] for k in group]), texts, with_junk=False)
        context = appearance.view_probabilities(np.concatenate([context_views[k] for k in group]), texts)
        frames = {}
        for k in group:
            for frame, classes in support[k].items():
                frames.setdefault(frame, set()).update(classes)
        merged.append({
            "members": group, "rows": object_rows, "views": object_views, "per_view": per_view,
            "probabilities": per_view.mean(0), "context": context.mean(0), "context_views": context, "siglip": unit(vectors.mean(0)),
            "detector": detector.judge(frames),
            "separation": float(np.concatenate([inside[k] for k in group]).mean()),
            "dino": unit(np.mean([dino[k] for k in group], 0)), "box": fit_box(world[object_rows]),
        })

    # Steps 1-4.
    for m in merged:
        m["class"], m["junk"], m["fit"], m["reasons"] = name(m["probabilities"], m["context"], m["box"])
        if not m["junk"]:
            second_opinion(m, m["detector"], m["detector"].verdict)
    kept = [m for m in merged if not m["junk"]]

    # Step 5: hold back names few views agree on and the detector doesn't back.
    for m in kept:
        if m["class"]:
            agree = consistency.judge(views_of(m), m["class"])
            if agree.verdict == "disagree" and not detector.backs(m["detector"], m["class"]):
                m["reasons"] = [f"held back: {m['reasons'][0]}, but {agree.reason}, {m['detector'].reason} → unclassified"]
                m["class"] = None

    # Final class, confidence; write.
    objects, object_rows, siglip, dinos = [], [], [], []
    for i, m in enumerate(kept):
        final = m["class"] or UNCLASSIFIED
        looks = appearance.judge(m["probabilities"])
        agree = consistency.judge(views_of(m), final)
        if final in CLASSES:
            strength = min(1.0, m["probabilities"][CLASSES.index(final)] / APPEARANCE_FULL)
            parts = [strength, m["fit"][final].score, agree.score]
            if m["detector"].verdict == final:
                parts.append(m["detector"].score)
        else:  # how sure it is of an asset class: how close its best class came
            parts = [min(1.0, m["probabilities"][: len(CLASSES)].max() / APPEARANCE_FULL)]
        oid = f"obj-{len(objects) + 1:03d}"
        first = instances[m["members"][0]]["id"]
        crops = [f"data/pipeline/crops/{first}-{k}.jpg" for k in range(min(SAVED_CROPS, len(views[m["members"][0]])))]
        object_twins = sorted({t for k in m["members"] for t in twins[k]})
        objects.append({
            "id": oid,
            "class": final,
            "label": final if final in CLASSES else looks.verdict,  # what it looks most like
            "scores": {LABELS[k]: round(float(m["probabilities"][k]), 3)
                       for k in np.argsort(-m["probabilities"])[:3] if m["probabilities"][k] >= 0.05},
            "score": round(float(np.mean(parts)), 3),  # = confidence; the viewers read score
            "confidence": round(float(np.mean(parts)), 3),  # how sure it is of an asset class
            "separation": round(m["separation"], 3),  # how surely it is one separate thing (membership consensus)
            "reasons": m["reasons"] + ([agree.reason] if final in CLASSES else []) + [m["detector"].reason],
            "signals": {
                "appearance": looks.json(),
                "plausibility": m["fit"][final].json() if final in CLASSES else None,
                "detector": m["detector"].json(),
                "consistency": agree.json(),
                "twins": object_twins,  # look-alikes touching it that the views keep apart
            },
            "excel_class": excel.get(final),
            "detections": int(sum(instances[k]["masks"] for k in m["members"])),
            "frames": sorted({f for k in m["members"] for f in instances[k]["frames"]}),
            "gaussians": int(len(m["rows"])),
            "merged_from": [instances[k]["id"] for k in m["members"]],
            **m["box"],
            "crop": crops[0],
            "crops": crops,
            "views": m["views"],  # [frame, box in frame pixels, SAM mask indices]
            "best_frame": m["views"][0][0],
        })
        object_rows.append(m["rows"])
        siglip.append(m["siglip"])
        dinos.append(m["dino"])
    rejected = [{"merged_from": [instances[k]["id"] for k in m["members"]], "reasons": m["reasons"], **m["box"]}
                for m in merged if m["junk"]]

    (pipeline / "objects.json").write_text(json.dumps(objects, indent=1))
    (pipeline / "rejected.json").write_text(json.dumps(rejected, indent=1))
    np.savez_compressed(
        pipeline / "objects.npz",
        rows=np.concatenate(object_rows).astype(np.int32),
        offsets=np.cumsum([0] + [len(r) for r in object_rows]),
        embeddings=np.stack(siglip).astype(np.float32),
        dino=np.stack(dinos).astype(np.float32),
    )
    by = Counter(o["class"] for o in objects)
    print(f"{len(objects)} objects, {len(rejected)} junk,",
          sum("detector:" in o["reasons"][0] for o in objects if o["excel_class"]), "by the detector,",
          sum("held back" in o["reasons"][0] for o in objects), "held back:", dict(by))


if __name__ == "__main__":
    main()
