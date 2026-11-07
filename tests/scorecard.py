"""Requirements scorecard: checks digital_twin.json against the Valmet challenge brief.

Run:  python -m tests.scorecard        (from valmet-twin/, after python -m pipeline.run)
Each check prints PASS/FAIL with the measured number; exit code 1 if any hard check fails.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd

from pipeline import config as C

TWIN = json.loads((C.OUT_DIR / "digital_twin.json").read_text())
X = pd.ExcelFile(C.XLSX_PATH)
results = []


def check(section, name, ok, detail, hard=True):
    results.append((section, name, bool(ok), detail, hard))


assets = TWIN["assets"]
nav = TWIN["navigation"]
by_id = {a["id"]: a for a in assets}

# ---------------------------------------------------------------- 1. extraction density & framing (30%)
eq = [a for a in assets if a["tier"] == "equipment"]
check("1 extraction", "framed assets >= 300", len(assets) >= 300, f"{len(assets)} assets")


def footprint(a):
    cx, _, cz = a["center"]; sx, _, sz = a["size"]; c, s = math.cos(a["yaw"]), math.sin(a["yaw"])
    return cx, cz, sx / 2, sz / 2, c, s


def iou3(a, b):
    lo = np.maximum(a["aabb_min"], b["aabb_min"]); hi = np.minimum(a["aabb_max"], b["aabb_max"])
    inter = np.prod(np.clip(hi - lo, 0, None))
    va = np.prod(np.subtract(a["aabb_max"], a["aabb_min"])); vb = np.prod(np.subtract(b["aabb_max"], b["aabb_min"]))
    return inter / max(1e-9, va + vb - inter)


dups = 0
for i in range(len(assets)):
    for j in range(i + 1, len(assets)):
        if iou3(assets[i], assets[j]) > 0.5:
            dups += 1
check("1 extraction", "duplicate boxes (IoU>0.5) < 2%", dups / len(assets) < 0.02, f"{dups} pairs")
tiny = sum(1 for a in assets if max(a["size"]) < 0.06)
check("1 extraction", "no degenerate boxes (<6 cm)", tiny == 0, f"{tiny} tiny")
unclassified = sum(1 for a in assets if a["class"] in ("industrial_asset",))
check("1 extraction", "unclassified labels < 10%", unclassified / len(assets) < 0.10, f"{unclassified} unclassified")
classes = Counter(a["class"] for a in assets)
check("1 extraction", "class diversity >= 8", len(classes) >= 8, f"{len(classes)} classes: {dict(classes.most_common(6))}")
src = Counter(a.get("label_source", "?") for a in eq)
model_labelled = src.get("decisions_api", 0) + src.get("gemini", 0) + sum(n for k, n in src.items() if k.startswith("local:"))
check("1 extraction", "AI-verified labels on equipment", model_labelled > 0.5 * len(eq),
      f"{dict(src)}", hard=False)

# label accuracy against the hand-labelled sample (tests/gold_labels.json)
from pathlib import Path as _P
from pipeline.semantic_labels import CLASSES as _CL
_gold = json.loads((_P(__file__).parent / "gold_labels.json").read_text())["items"]
_EQUIV = {"horizontal_pipe": {"horizontal_pipe", "overhead_pipe"}}
SEGMENTER = TWIN["scene"].get("segmenter", "heuristic")
def _pred(g):
    if SEGMENTER != "heuristic":   # another segmenter's ids mean nothing here: the object at the sample's centre
        inside = [a for a in assets if all(lo - 0.03 <= c <= hi + 0.03 for c, lo, hi in zip(g["center"], a["aabb_min"], a["aabb_max"]))]
        return min(inside, key=lambda a: np.prod(a["size"])) if inside else None
    for a in assets:
        if a["id"] == g["id"] or g["id"] in a.get("merged_from", []) or g["id"] in a.get("merged_fragments", []):
            return a
    return None  # dropped as clutter
judged = [g for g in _gold if g["label"]]
hit = rule_hit = 0
for g in judged:
    want = _CL[g["label"]][0]
    ok_set = _EQUIV.get(want, {want})
    a = _pred(g)
    got = "clutter" if a is None else a.get("scene_class", a["class"])   # what the scan/AI says, not the Excel class
    hit += got in ok_set
    rule_hit += g["rule_label"] in ok_set
acc = hit / len(judged)
check("1 extraction", "label accuracy on hand-labelled sample >= 70%", acc >= 0.70,
      f"{acc:.0%} ({hit}/{len(judged)}, {SEGMENTER} segmenter); colour-rule baseline {rule_hit / len(judged):.0%}", hard=False)
mov_gold = [g for g in judged if g["movable"]]
mov_hit = sum(bool((_pred(g) or {}).get("movable")) for g in mov_gold)
check("1 extraction", "movable objects detected in sample", mov_hit == len(mov_gold), f"{mov_hit}/{len(mov_gold)}", hard=False)

det = TWIN["scene"].get("semantic_labelling", {}).get("detection", {})
if det and "skipped" not in det:
    n_conf = sum(1 for a in assets if a.get("detection_views"))
    _dev_cls = {"control_valve", "circulation_pump", "electric_motor", "pressure_sensor", "expansion_vessel",
                "electrical_cabinet", "heat_exchanger", "appliance"}
    _devs = [a for a in assets if a.get("scene_class", a["class"]) in _dev_cls]
    _dconf = sum(1 for a in _devs if a.get("detection_views"))
    check("1 extraction", "devices independently confirmed by detection in real video frames (>= 50%)",
          _devs and _dconf >= 0.5 * len(_devs),
          f"{_dconf}/{len(_devs)} devices confirmed; {n_conf}/{len(assets)} objects overall; "
          f"{det.get('new_objects_from_detection', 0)} new objects added from detection", hard=False)

# ---------------------------------------------------------------- 2. metadata mapping from Excel
master = X.parse("Asset_Master")
mapped = [a for a in assets if a["mapped"]]
check("2 mapping", "all 30 Excel assets bound to distinct 3D objects",
      len({a["asset_id"] for a in mapped}) == len(master) and len({a["id"] for a in mapped}) == len(master),
      f"{len(mapped)} mapped")
targets = [a for a in assets if a["is_target"]]
check("2 mapping", "exactly one faulty target, degraded/warning", len(targets) == 1 and targets[0]["condition_state"] != "healthy",
      f"{[(t['tag'], t['condition_state']) for t in targets]}")
devices = [a for a in assets if a.get("instrumented") is not False]
excel_src = sum(1 for a in devices if a["mapped"] or a.get("excel_profile_source"))
check("2 mapping", "every device's status/telemetry sourced from Excel", excel_src == len(devices),
      f"{excel_src}/{len(devices)} devices Excel-sourced, {len(assets) - len(devices)} movable objects (not instrumented)")
DEVICE_LIKE = {"control_valve", "circulation_pump", "electric_motor", "pressure_sensor", "electrical_cabinet",
               "heat_exchanger", "expansion_vessel", "pipe_fitting", "flow_marker", "process_unit"}
_bound = [a for a in mapped if a.get("scene_class")]
_ok = [a for a in _bound if a["scene_class"] in DEVICE_LIKE and a.get("scene_label_source") != "geometric_rules"]
check("2 mapping", "Excel devices sit on objects the AI labelled as equipment (>= 80%)",
      _bound and len(_ok) >= 0.8 * len(_bound),
      f"{len(_ok)}/{len(_bound)} AI-verified device objects; off: "
      f"{sorted((a['tag'], a['scene_class'], a.get('scene_label_source')) for a in _bound if a not in _ok)[:5]}", hard=False)
movable = [a for a in assets if a.get("movable")]
check("2 mapping", "movable objects carry no fake telemetry", all(not a["telemetry"]["latest"] for a in movable),
      f"{len(movable)} movable objects")
tel = X.parse("Telemetry_Sample_3d")
bad_vals = 0
for a in mapped:
    rows = tel[tel.asset_id == a["asset_id"]]
    for k, v in a["telemetry"]["latest"].items():
        if k in rows and v is not None and not np.isclose(rows[k].dropna().values, v, atol=1e-3).any():
            bad_vals += 1
check("2 mapping", "mapped telemetry values exist in Excel", bad_vals == 0, f"{bad_vals} mismatched values")

# ---------------------------------------------------------------- 3. navigation (25%)
g = nav["grid_meta"]
walk = np.array(nav["walkable_grid"], dtype=bool)
route = nav["primary_route"]
wps = route["waypoints"]


def cell(x, z):
    return int(math.floor((x - g["x_min"]) / g["res"])), int(math.floor((z - g["z_min"]) / g["res"]))


off = 0
pts = np.array([[w["position"][0], w["position"][2]] for w in wps])
dense = []
for p, q in zip(pts[:-1], pts[1:]):
    n = max(2, int(np.linalg.norm(q - p) / 0.01))
    dense += [p + (q - p) * t for t in np.linspace(0, 1, n)]
for x, z in dense:
    i, j = cell(x, z)
    if not (0 <= i < walk.shape[0] and 0 <= j < walk.shape[1] and walk[i, j]):
        off += 1
check("3 navigation", "path stays on walkable floor (1 cm sampling)", off == 0, f"{off}/{len(dense)} samples off-floor")
check("3 navigation", "min clearance >= robot radius", route["min_clearance_m"] >= g["robot_radius_m"],
      f"{route['min_clearance_m']:.3f} m vs {g['robot_radius_m']}")

hits = set()
for a in assets:
    if a["aabb_min"][1] > C.OBST_MAX_H or a["aabb_max"][1] < C.OBST_MIN_H or a["is_target"]:
        continue
    cx, cz, hx, hz, c, s = footprint(a)
    d = np.asarray(dense) - [cx, cz]
    lx, lz = d[:, 0] * c + d[:, 1] * s, -d[:, 0] * s + d[:, 1] * c
    if ((abs(lx) < hx) & (abs(lz) < hz)).any():
        hits.add(a["id"])
check("3 navigation", "path never passes through an equipment box", not hits, f"{len(hits)} boxes crossed {sorted(hits)[:6]}")
tgt = targets[0]
end = pts[-1]
dist_end = math.hypot(end[0] - tgt["center"][0], end[1] - tgt["center"][2])
check("3 navigation", "ends at inspection stand-off near target (<1.2 m)", dist_end < 1.2, f"{dist_end:.2f} m")
turns = []
dd = np.asarray(dense)
seg = np.r_[0, np.cumsum(np.linalg.norm(np.diff(dd, axis=0), axis=1))]
res = np.array([np.interp(np.arange(0, seg[-1], 0.10), seg, dd[:, k]) for k in range(2)]).T  # 10 cm resample
for a_, b_, c_ in zip(res[:-2], res[1:-1], res[2:]):
    v1, v2 = b_ - a_, c_ - b_
    if np.linalg.norm(v1) > 1e-6 and np.linalg.norm(v2) > 1e-6:
        turns.append(math.degrees(math.acos(np.clip(v1 @ v2 / np.linalg.norm(v1) / np.linalg.norm(v2), -1, 1))))
check("3 navigation", "smooth walk (max heading change per 10 cm < 45 deg)", max(turns) < 45, f"max {max(turns):.0f} deg", hard=False)
check("3 navigation", "routes to every mapped asset", len(nav["routes_by_asset"]) >= len(master),
      f"{len(nav['routes_by_asset'])} routes")

# proximity correctness
R = g["proximity_radius_m"]
wrong = 0
for w in wps[:: max(1, len(wps) // 15)]:
    px, pz = w["position"][0], w["position"][2]
    truth = {a["id"] for a in assets if math.hypot(a["center"][0] - px, a["center"][2] - pz) <= R}
    shown = {n["id"] for n in w["nearby_assets"]}
    wrong += len(truth ^ shown)
check("3 navigation", "proximity lists match geometry", wrong == 0, f"{wrong} mismatches over sampled waypoints", hard=False)
check("3 navigation", "nearby entries carry status", all("condition_state" in n for w in wps for n in w["nearby_assets"]), "status fields")

# real-world check: the challenge video's camera path (COLMAP-aligned by Linards, SinceAI-2026/linards-project)
from pipeline import walk as WK  # noqa: E402
_w = WK.load() if WK.POSES.exists() else None
if _w is not None:
    _cl = np.array(nav["clearance_grid_m"]); _ij = np.floor((_w["pos"][:, [0, 2]] - [g["x_min"], g["z_min"]]) / g["res"]).astype(int)
    _ij = np.clip(_ij, 0, np.array(_cl.shape) - 1)
    _solid = float((_cl[_ij[:, 0], _ij[:, 1]] <= 0).mean())
    _robot = float(np.array(nav["walkable_grid"], bool)[_ij[:, 0], _ij[:, 1]].mean())
    check("3 navigation", "obstacle map agrees with the real walkthrough (<5% of human path marked solid)", _solid < 0.05,
          f"{_solid:.1%} of {len(_ij)} video poses on solid cells (hand-held camera reaching over equipment); "
          f"{_robot:.0%} inside the robot-safe area", hard=False)

# ---------------------------------------------------------------- 4. AI grounding (20%)
ids = set()
for sheet, col in [("Alarms_Events", "event_id"), ("Maintenance_History", "work_order_id"),
                   ("Historical_Service_Cases", "case_id"), ("Knowledge_Docs", "doc_id"),
                   ("RAG_Corpus_Index", "doc_id"), ("Data_Quality", "issue_id")]:
    ids |= set(X.parse(sheet)[col].astype(str))
tel_cols = set(tel.columns)
PAT = re.compile(r"\b(EVT-\d+|WO-\d+|CASE-\d+|DQ-\d+|DOC-[A-Z0-9_\-]+|TELEM:[A-Z0-9\-]+:[a-z_]+)\b")
missing = Counter()
cited = 0
for a in assets:
    for m in PAT.findall(json.dumps(a["diagnostics"])):
        cited += 1
        if m.startswith("TELEM:"):
            _, aid, metric = m.split(":")
            if metric not in tel_cols or aid not in set(tel.asset_id):
                missing[m] += 1
        elif m not in ids:
            missing[m] += 1
check("4 AI grounding", "every cited evidence ID exists in the Excel pack", not missing, f"{cited} citations, unknown: {dict(missing.most_common(5))}")
d = tgt["diagnostics"]
check("4 AI grounding", "target has typed AI triage (root cause, severity, LOTO, data trust)", bool(d.get("ai_triage")), str(bool(d.get("ai_triage"))))
check("4 AI grounding", "target has >=3 safe verification steps incl. LOTO doc",
      len(d["verification_steps"]) >= 3 and any("SAFETY" in s["citation"] for s in d["verification_steps"]), f"{len(d['verification_steps'])} steps")
check("4 AI grounding", "retrieval returns target's own case first", d["retrieved_evidence"] and d["retrieved_evidence"][0]["id"] in
      {c["case_id"] for c in tgt["matched_cases"]} | {f"DOC-{tgt['asset_id']}"}, f"{[r['id'] for r in d['retrieved_evidence'][:3]]}")
nonhealthy_on_path = {n["id"] for w in wps for n in w["nearby_assets"] if n["condition_state"] not in ("healthy", "movable", "not_instrumented")}
check("4 AI grounding", "assets along path have status explanations", all(by_id[i]["diagnostics"].get("executive_summary") for i in nonhealthy_on_path),
      f"{len(nonhealthy_on_path)} non-healthy assets passed")

# ---------------------------------------------------------------- report
fails = 0
for sec in sorted({r[0] for r in results}):
    print(f"\n== {sec}")
    for _, name, ok, detail, hard in [r for r in results if r[0] == sec]:
        tag = "PASS" if ok else ("FAIL" if hard else "WARN")
        fails += (not ok) and hard
        print(f"  [{tag}] {name}: {detail}")
print(f"\n{sum(r[2] for r in results)}/{len(results)} checks pass, {fails} hard failures")
sys.exit(1 if fails else 0)
