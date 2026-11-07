"""Geometry plausibility signal: could an instance of this size and shape be a thing of that class?

Each class has a range for its longest side in pipeline/classes.json (size_m, with the basis in
size_why); boxes come from the splat, so a little bleed is expected and a size within SLACK of
the range is borderline rather than out. Open-vocabulary 3D detectors use the same kind of prior to
keep or rescore boxes (OpenSight, arXiv 2312.08876; GLRD, arXiv 2503.20682, with a tolerance
margin; their sizes come from an LLM, ours are typical dimensions written into classes.json).
Shape: where classes.json gives one, a minimum thinnest side and a maximum longest/middle ratio,
so a pipe or a rod isn't a pump or a valve. No height rule: here pumps hang on the circulator wall
and valves sit on overhead pipes, so height above the floor says little (a "big pumps stand on the
floor" rule rejected three wall pumps).
"""
import json

import numpy as np

from geometry import repo
from verdict import Signal

SLACK = 1.3  # a longest side within this factor outside the range is borderline, not out

config = json.loads((repo / "pipeline" / "classes.json").read_text())
RANGES = {c["name"]: c["size_m"] for c in config["classes"]}
SHAPES = {c["name"]: c.get("shape", {}) for c in config["classes"]}  # no slack: these limits are already loose


def judge(box, name):
    """fits (1), borderline (0.5) or out (0) for `name`, with the reason."""
    longest = max(box["size"])
    low, high = RANGES[name]
    thinnest, middle = sorted(box["size"])[:2]
    shape = SHAPES[name]
    if thinnest < shape.get("min_side_m", 0):
        return Signal("out", 0.0, f"{thinnest:.2f} m thin")
    if longest > shape.get("max_elongation", np.inf) * max(middle, 1e-3):
        return Signal("out", 0.0, f"a rod ({longest:.2f} x {middle:.2f} m)")
    if low <= longest <= high:
        return Signal("fits", 1.0, "size fits")
    if low / SLACK <= longest <= high * SLACK:
        return Signal("borderline", 0.5, f"{longest:.2f} m is at the edge of {low}-{high} m")
    return Signal("out", 0.0, f"{longest:.2f} m {'long' if longest > high else 'small'}")
