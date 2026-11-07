"""Step 10b: a linear probe on the frozen features, trained on an expert's labels (label.html).

Features per object: its DINOv2 embedding (mean over its views, objects.npz) + its SigLIP 2 masked-crop
embedding (scale 0, mean over its views, embeddings.npz) + the log of its box sides, sorted. Each
column is standardised over all objects. Model: multinomial logistic regression with an L2 penalty
(the CLIP paper's linear-probe evaluation, Radford et al. 2021), fitted with L-BFGS; scikit-learn is
not in the venv and this is 20 lines. "bad piece" (a mix or a fragment) is not a class: a separate
yes/no model flags it once BAD_MIN examples exist.

With no usable labels, objects.json stays fuse.py's zero-shot result. With labels, every object gets
the classifier's class, confidence (its probability) and reason; labelled objects take their label.
fuse.py's own fields are kept under "zero_shot" so a rerun always starts from them.

Also writes the active-learning queue (Settles 2009, margin sampling: smallest gap between the top
two probabilities first; with no model, the zero-shot confidence) and a stratified k-fold report.

  python pipeline/classifier.py [--labels data/pipeline/labels.json [data/pipeline/labels-claude.json ...]]

Labels file: a JSON object, object id -> {"label", "source", "ts"}:
  label   one of pump, motor, valve, pressure transmitter, vibration sensor, other, bad piece
  source  who labelled it: "expert" for a human (label.html), anything else for a stand-in,
          e.g. "claude-review"
  ts      ISO 8601 time of labelling
  optional "merged_from": the object's merged_from when labelled; a label whose object changed since
  (fuse.py reran and ids moved) is ignored.
  {"obj-012": {"label": "valve", "source": "expert", "ts": "2026-11-07T14:02:11Z"}}
Several files merge; when two label one object, the expert's wins, else the later file's.

Reads data/pipeline/objects.{json,npz}, embeddings.npz, instances.json and the labels files. Writes
objects.json, label_queue.json and eval/classifier.json (with the files and sources used).
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

repo = Path(__file__).resolve().parent.parent
pipeline = repo / "data" / "pipeline"
config = json.loads((repo / "pipeline" / "classes.json").read_text())
excel = {c["name"]: c["excel_class"] for c in config["classes"]}

OTHER, BAD, UNCLASSIFIED = "other", "bad piece", "unclassified"
EXPERT = "expert"  # a human's source; it beats every other source
CLASSES = [c["name"] for c in config["classes"]] + [OTHER]
C = 1.0  # inverse L2 strength, scikit-learn's default; not tuned
BAD_MIN = 8  # bad-piece labels before the flag model is trained
FOLDS = 5
LOOK_ALIKE = 0.8  # DINOv2 cosine (pipeline/similarity.py): a queued look-alike waits until the rest are queued
ZERO_SHOT = ["class", "label", "scores", "score", "confidence", "reasons", "excel_class"]  # fields this step rewrites


def unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def features(objects):
    """One row per object, standardised columns; and the DINOv2 part (for diversity)."""
    stored = np.load(pipeline / "objects.npz")
    emb = np.load(pipeline / "embeddings.npz")
    offsets = emb["view_offsets"]
    masked = emb["siglip_scales"][:, 0].astype(np.float32)  # scale 0: the object on grey
    index = {inst["id"]: k for k, inst in enumerate(json.loads((pipeline / "instances.json").read_text()))}
    siglip = unit(np.stack([
        np.concatenate([masked[offsets[index[i]]:offsets[index[i] + 1]] for i in o["merged_from"]]).mean(0)
        for o in objects]))
    dino = unit(stored["dino"])
    size = np.log(np.maximum(np.sort([o["size"] for o in objects], axis=1), 1e-3))
    x = np.concatenate([dino, siglip, size], 1)
    return (x - x.mean(0)) / (x.std(0) + 1e-8), dino


def fit(x, y, n_classes):
    """Multinomial logistic regression, L2 penalty |W|^2 / 2C on the weights (not the bias)."""
    onehot = np.eye(n_classes)[y]
    d = x.shape[1]

    def loss(theta):
        w, b = theta[:d * n_classes].reshape(d, n_classes), theta[d * n_classes:]
        p = softmax(x @ w + b)
        grad = p - onehot
        value = -np.sum(onehot * np.log(p + 1e-12)) + np.sum(w * w) / (2 * C)
        return value, np.concatenate([(x.T @ grad + w / C).ravel(), grad.sum(0)])

    theta = minimize(loss, np.zeros(d * n_classes + n_classes), jac=True, method="L-BFGS-B").x
    return theta[:d * n_classes].reshape(d, n_classes), theta[d * n_classes:]


def softmax(z):
    p = np.exp(z - z.max(1, keepdims=True))
    return p / p.sum(1, keepdims=True)


def predict(model, x):
    return softmax(x @ model[0] + model[1])


def margins(p):
    top = np.sort(p, 1)
    return top[:, -1] - top[:, -2]


def folds(y, k, seed=0):
    """Stratified fold number per sample: each class's samples dealt round-robin after a shuffle."""
    rng, fold = np.random.default_rng(seed), np.zeros(len(y), int)
    for c in np.unique(y):
        members = rng.permutation(np.flatnonzero(y == c))
        fold[members] = np.arange(len(members)) % k
    return fold


def cross_validate(x, y):
    """Out-of-fold predictions over the classes with >= 2 labels; None if that leaves < 2 classes."""
    counts = np.bincount(y, minlength=len(CLASSES))
    keep = counts[y] >= 2
    present = np.flatnonzero(counts >= 2)
    if len(present) < 2:
        return None
    k = min(FOLDS, counts[present].min())
    x, y = x[keep], np.searchsorted(present, y[keep])
    fold, predicted = folds(y, k), np.zeros(len(y), int)
    for f in range(k):
        model = fit(x[fold != f], y[fold != f], len(present))
        predicted[fold == f] = predict(model, x[fold == f]).argmax(1)
    names = [CLASSES[c] for c in present]
    confusion = np.zeros((len(names), len(names)), int)
    np.add.at(confusion, (y, predicted), 1)
    per_class = {n: {"labels": int(confusion[i].sum()),
                     "precision": round(float(confusion[i, i] / max(confusion[:, i].sum(), 1)), 3),
                     "recall": round(float(confusion[i, i] / max(confusion[i].sum(), 1)), 3)}
                 for i, n in enumerate(names)}
    return {"folds": int(k), "labels": int(len(y)), "accuracy": round(float((predicted == y).mean()), 3),
            "per_class": per_class, "confusion": {"classes": names, "rows_true_cols_predicted": confusion.tolist()}}


def zero_shot(o):
    """fuse.py's fields for an object, whatever this step wrote over them."""
    return {**o, **o.get("zero_shot", {})}


def zero_shot_class(o):
    c = zero_shot(o)["class"]
    return OTHER if c == UNCLASSIFIED else c


def load_labels(paths, objects):
    """{object index: (label, source)} over the files, the expert's winning; labels whose object
    no longer exists in this form (fuse.py reran) are skipped."""
    by_id = {o["id"]: k for k, o in enumerate(objects)}
    labels, stale = {}, 0
    for path in paths:
        if not path.exists() or not path.read_text().strip():
            continue
        for oid, entry in json.loads(path.read_text()).items():
            k = by_id.get(oid)
            if entry["label"] not in CLASSES + [BAD]:
                raise SystemExit(f"{path}: {oid} has unknown label {entry['label']!r}")
            if k is None or (entry.get("merged_from") or objects[k]["merged_from"]) != objects[k]["merged_from"]:
                stale += 1
            elif k not in labels or labels[k][1] != EXPERT:
                labels[k] = (entry["label"], entry.get("source", "unknown"))
    if stale:
        print(f"ignored {stale} labels whose objects changed since labelling")
    return labels


def queue(uncertainty, dino, skip):
    """Most uncertain first; an object that looks like one already queued waits until the rest are in."""
    picked, waiting = [], []
    for k in np.argsort(-uncertainty, kind="stable"):
        if k in skip:
            continue
        (waiting if picked and (dino[picked] @ dino[k]).max() >= LOOK_ALIKE else picked).append(int(k))
    return picked + waiting


def write_class(o, name, confidence, scores, reasons):
    o.setdefault("zero_shot", {f: o[f] for f in ZERO_SHOT})
    asset = name in excel
    o.update({"class": name if asset else UNCLASSIFIED, "label": name if asset else o["zero_shot"]["label"],
              "scores": scores, "score": confidence, "confidence": confidence, "reasons": reasons,
              "excel_class": excel.get(name)})


def main(labels_paths):
    objects = json.loads((pipeline / "objects.json").read_text())
    for o in objects:  # start from fuse.py's result
        o.update(o.pop("zero_shot", {}))
        o.pop("bad_piece", None)
        o.pop("label_source", None)
    x, dino = features(objects)
    labelled = load_labels(labels_paths, objects)
    labels = {k: v for k, (v, _) in labelled.items()}
    sources = {}
    for _, source in labelled.values():
        sources[source] = sources.get(source, 0) + 1
    by = ", ".join(sources)
    classed = {k: CLASSES.index(v) for k, v in labels.items() if v in CLASSES}
    idx = np.array(sorted(classed), int)
    y = np.array([classed[k] for k in idx], int)
    present = np.unique(y)
    report = {"labels_files": [str(p) for p in labels_paths], "sources": sources, "labels": len(labels), "by_label": {n: sum(v == n for v in labels.values()) for n in CLASSES + [BAD]}}

    if len(present) >= 2:
        model = fit(x[idx], np.searchsorted(present, y), len(present))
        p = predict(model, x)
        uncertainty = 1 - margins(p)
        for o, row in zip(objects, p):
            best = int(row.argmax())
            name = CLASSES[present[best]]
            top = np.argsort(-row)[:3]
            write_class(o, name, round(float(row[best]), 3), {CLASSES[present[t]]: round(float(row[t]), 3) for t in top},
                        [f"classifier: {name} {row[best]:.2f}, trained on {len(idx)} labels ({by})"])
        report["model"] = {"classes": [CLASSES[c] for c in present], "trained_on": len(idx)}
        report["cross_validation"] = cross_validate(x[idx], y)
    else:  # zero-shot: how sure fuse.py is of its own answer (an asset class, or none)
        conf = np.array([o["confidence"] for o in objects])
        uncertainty = np.where([o["class"] == UNCLASSIFIED for o in objects], conf, 1 - conf)
        report["model"] = None

    bad = [k for k, v in labels.items() if v == BAD]
    if len(bad) >= BAD_MIN and len(labels) > len(bad):
        flagged = np.array(sorted(labels), int)
        flag = predict(fit(x[flagged], np.array([labels[k] == BAD for k in flagged], int), 2), x)[:, 1]
        for o, pb in zip(objects, flag):
            o.setdefault("zero_shot", {f: o[f] for f in ZERO_SHOT})
            o["bad_piece"] = round(float(pb), 3)
            if pb >= 0.5:
                o["reasons"] = o["reasons"] + [f"classifier: bad piece {pb:.2f}"]
        report["bad_piece_model"] = {"trained_on": len(flagged), "bad": len(bad)}

    for k, (v, source) in labelled.items():  # a label overrides the model
        o = objects[k]
        write_class(o, v if v != BAD else OTHER, 1.0, {v: 1.0}, [f"labelled by {source}" + (": bad piece" if v == BAD else "")])
        o["label_source"] = source
        if v == BAD:
            o["bad_piece"] = 1.0

    # The expert reviews stand-in labels too: only the expert's own leave the queue.
    order = queue(uncertainty, dino, {k for k, (_, source) in labelled.items() if source == EXPERT})
    (pipeline / "label_queue.json").write_text(json.dumps([objects[k]["id"] for k in order]))
    (pipeline / "objects.json").write_text(json.dumps(objects, indent=1))
    (pipeline / "eval").mkdir(exist_ok=True)
    (pipeline / "eval" / "classifier.json").write_text(json.dumps(report, indent=1))

    cv = report.get("cross_validation")
    print(f"{len(labels)} labels from {by or 'no files'} {report['by_label']}; model: {report['model']}")
    if cv:
        print(f"{cv['folds']}-fold CV on {cv['labels']} labels: accuracy {cv['accuracy']:.3f}")
        names = cv["confusion"]["classes"]
        print(f"{'':22}{'prec':>6}{'rec':>6}   confusion (rows true, cols predicted)")
        for n, row in zip(names, cv["confusion"]["rows_true_cols_predicted"]):
            print(f"{n:22}{cv['per_class'][n]['precision']:6.2f}{cv['per_class'][n]['recall']:6.2f}   {row}")
    return report, [objects[k]["id"] for k in order]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", type=Path, nargs="+", default=[pipeline / "labels.json"],
                        help="labels files (default data/pipeline/labels.json); the expert's label wins")
    main(parser.parse_args().labels)
