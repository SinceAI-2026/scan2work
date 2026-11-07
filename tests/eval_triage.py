"""Blind evaluation of the AI triage: can it find each faulty asset's issue WITHOUT seeing the answer?

Current_State (condition, health, issue code/summary) is never shown to the model. For every non-healthy
Excel asset the triage picks a root cause among that class's historical service cases; we score it against
the hidden Current_State issue_code. Two modes:
  * with events     : telemetry + alarms + work orders (how it would run in a plant). NOTE: in this synthetic
                      pack alarm descriptions carry the issue code one-to-one, so this mode is close to trivial.
  * telemetry only  : no alarms, no work orders - the honest test of the diagnosis.
Uses ~26 Decisions API calls (cached afterwards).   Run:  ./.venv/bin/python -m tests.eval_triage
"""
from __future__ import annotations

import json
import math
from collections import Counter

from pipeline import config as C, decisions_client as D, diagnostics as DG, knowledge, retrieval


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def main():
    twin = json.loads((C.OUT_DIR / "digital_twin.json").read_text())
    pack = knowledge.load_excel_pack()
    cases = pack["cases"]
    case_issue = {c["case_id"]: c["issue_code"] for c in cases}
    retriever = retrieval.load_saved() or retrieval.Retriever(retrieval.build_corpus(pack))
    DG.set_class_baselines(twin["assets"])
    faulty = [a for a in twin["assets"] if a["mapped"] and a["condition_state"] != "healthy"]
    print(f"{len(faulty)} non-healthy Excel assets; Current_State hidden from the model; retrieval: {retriever.backend}\n")

    # naive baseline: the class's most common historical issue
    common = {}
    for c in cases:
        common.setdefault(c["asset_class"], Counter())[c["issue_code"]] += 1

    rows = []
    for mode, events in (("with events", True), ("telemetry only", False)):
        top1 = top3 = 0
        for a in faulty:
            obs = DG._format_observations(a)
            retrieved = retriever.search(DG.evidence_query(a, obs, events), scope=a["asset_class"])
            tri = DG.ai_triage(a, obs, retrieved, cases, include_events=events)
            preds = [case_issue.get(r["value"], r["value"]) for r in (tri or {}).get("root_cause", [])]
            seen = []
            for p in preds:
                if p not in seen:
                    seen.append(p)
            hit1 = bool(seen) and seen[0] == a["issue_code"]
            hit3 = a["issue_code"] in seen[:3]
            top1 += hit1; top3 += hit3
            rows.append((mode, a["tag"], a["issue_code"], seen[:3], hit1))
        D.save_cache()
        n = len(faulty)
        l1, h1 = wilson(top1, n); l3, h3 = wilson(top3, n)
        print(f"{mode:15s} top-1 {top1}/{n} ({top1 / n:.0%}, 95% CI {l1:.0%}-{h1:.0%})   "
              f"top-3 {top3}/{n} ({top3 / n:.0%}, 95% CI {l3:.0%}-{h3:.0%})")
    base = sum(common[a["asset_class"]].most_common(1)[0][0] == a["issue_code"] for a in faulty if a["asset_class"] in common)
    print(f"{'baseline':15s} top-1 {base}/{len(faulty)} (most common historical issue for the class)\n")
    for mode, tag, truth, top, ok in rows:
        print(f"  [{mode:14s}] {tag:7s} truth={truth:20s} predicted={top} {'OK' if ok else ''}")


if __name__ == "__main__":
    main()
