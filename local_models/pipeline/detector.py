"""Detector signal: a second opinion on the name from OWLv2 detections.

pipeline/detect.py boxes the detector prompts of classes.json in every posed frame with OWLv2 and
gives each box a SAM 2.1 mask (data/pipeline/detections/<frame>.npz, same half-resolution format as
amg/). Detections only vote on names here; they don't cut objects out. An instance's view supports a class when a detection of that class (its counts_as) and
the instance's masks in that frame mostly overlap each other: the intersection is at least OVERLAP
of both masks, so a valve box on a big blob or a big box around a small part doesn't count.
"""
from collections import Counter

import numpy as np

from appearance import CLASSES, config
from geometry import frame_masks, repo
from verdict import Signal

DETECTIONS = repo / "data" / "pipeline" / "detections"  # pipeline/detect.py's output
OVERLAP = 0.5  # share of each mask the other one covers
SUPPORTS = 0.5  # share of an object's views that must support a class for the detector to back it
PROMPT_CLASS = [d.get("counts_as") for d in config["detector_prompts"]]  # detection label -> class or None


def view_support(instances):
    """Per instance: {frame: set of classes an overlapping detection supports there}."""
    by_frame = {}
    for k, inst in enumerate(instances):
        for frame, indices in inst["views"].items():
            by_frame.setdefault(frame, []).append((k, indices))
    support = [{} for _ in instances]
    for frame, members in by_frame.items():
        path = DETECTIONS / f"{frame}.npz"
        if not path.exists():
            continue
        labels = np.load(path)["labels"]
        wanted = [i for i, label in enumerate(labels) if PROMPT_CLASS[int(label)]]
        if not wanted:
            for k, _ in members:
                support[k][frame] = set()
            continue
        detections = frame_masks(frame, DETECTIONS)[wanted]
        mine = frame_masks(frame)
        for k, indices in members:
            mask = mine[indices].any(0)
            inter = (detections & mask).sum((1, 2))
            ok = (inter >= OVERLAP * mask.sum()) & (inter >= OVERLAP * detections.sum((1, 2)))
            support[k][frame] = {PROMPT_CLASS[int(labels[wanted[i]])] for i in np.flatnonzero(ok)}
    return support


def judge(frames):
    """frames: {frame: supported classes} for one object. The class most of its views back, if any."""
    counts = Counter(c for classes in frames.values() for c in classes)
    if not counts:
        return Signal("none", 0.0, f"detector: nothing in {len(frames)} views")
    name, views = counts.most_common(1)[0]
    share = views / max(len(frames), 1)
    return Signal(name if share >= SUPPORTS else "weak", share, f"detector: {name} in {views}/{len(frames)} views")


def backs(signal, name):
    return signal.verdict == name and name in CLASSES
