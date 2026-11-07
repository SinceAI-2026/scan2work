"""Re-name pipe pieces in data/pipeline/objects.json without re-running the pipeline.

fuse.py named objects before "pipe", "pipe elbow" and "cable tray" were labels (they were junk phrases),
so long thin pipe pieces came out as "door", "roll container", "vibration sensor"... This scores each
object's stored SigLIP 2 embedding (objects.npz, the unit mean of its view embeddings) against the
current labels and re-names an object only when all of these hold:
  - its best label is a pipe label,
  - it is not one of the client's asset classes (excel_class is null),
  - it is shaped like a pipe piece: longest side >= ELONGATION x the middle one and >= MIN_LONG_M,
    thinnest <= MAX_THIN_M,
  - its current label is not an equipment name (pump, valve, motor, gauge): those were reviewed and win.

It is an approximation of fuse.py's naming (one mean vector, not the mean of per-view softmaxes), which
is why the shape gate is required. Writes both `label` and `zero_shot.label`, so classifier.py keeps it.

  python tools/relabel_pipes.py            # dry run: the table of what would change
  python tools/relabel_pipes.py --write    # rewrite objects.json
"""
import json
import sys
from pathlib import Path

import numpy as np

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "pipeline"))
import appearance  # noqa: E402

PIPE_LABELS = {"pipe", "pipe elbow", "cable tray"}
ELONGATION = 3.0
MAX_THIN_M = 0.2
MIN_LONG_M = 0.3
KEEP = {"pump", "valve", "motor", "pressure transmitter"}


def pipe_shaped(size):
    small, middle, large = sorted(size)
    return large >= ELONGATION * middle and large >= MIN_LONG_M and small <= MAX_THIN_M


def main():
    write = "--write" in sys.argv
    path = repo / "data" / "pipeline" / "objects.json"
    objects = json.loads(path.read_text())
    emb = np.load(repo / "data" / "pipeline" / "objects.npz")["embeddings"]
    texts = appearance.label_texts()
    probs = appearance.view_probabilities(emb, texts, with_junk=False)
    labels = appearance.LABELS

    changed = []
    for i, o in enumerate(objects):
        best = labels[int(np.argmax(probs[i]))]
        if best not in PIPE_LABELS or o.get("excel_class") or o["label"] in KEEP or not pipe_shaped(o["size"]):
            continue
        p_pipe = float(sum(probs[i][labels.index(l)] for l in PIPE_LABELS))
        changed.append((o["id"], o["label"], best, p_pipe, [round(s, 2) for s in o["size"]]))
        if write:
            o["label"] = best
            o["label_source"] = "pipe-relabel"
            o.setdefault("zero_shot", {})["label"] = best
            o["reasons"] = list(o.get("reasons", [])) + [f"re-named {best} (SigLIP {p_pipe:.2f}, pipe-shaped)"]

    print(f"{len(changed)} of {len(objects)} objects re-named")
    for oid, old, new, p, size in changed:
        print(f"  {oid}  {old:>20} -> {new:<11} p={p:.2f}  size={size}")
    shaped = sum(pipe_shaped(o["size"]) and not o.get("excel_class") for o in objects)
    print(f"({shaped} non-asset objects are pipe-shaped)")
    if write:
        path.write_text(json.dumps(objects, indent=1))
        print("wrote", path)


if __name__ == "__main__":
    main()
