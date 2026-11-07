"""Stage 3 - Knowledge ingestion & 3D-to-Excel semantic asset binding.

Loads all sheets from Valmet_SinceAI_2026_Synthetic_Industrial_Metaverse_Builder_Pack.xlsx
and binds the 30 canonical Valmet assets (plus procedural telemetry for all
remaining extracted 3D objects) onto the segmented 3D boiler room clusters.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

from . import config as C


def _clean_val(v):
    if v is None:
        return None
    if isinstance(v, (float, np.floating)) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return round(float(v), 3)
    return v


def load_excel_pack(xlsx_path=C.XLSX_PATH) -> dict:
    xl = pd.ExcelFile(xlsx_path)
    master = xl.parse("Asset_Master")
    anchors = xl.parse("Scene_Anchors")
    state = xl.parse("Current_State")
    telemetry = xl.parse("Telemetry_Sample_3d")
    alarms = xl.parse("Alarms_Events")
    maint = xl.parse("Maintenance_History")
    cases = xl.parse("Historical_Service_Cases")
    docs = xl.parse("Knowledge_Docs")
    rag_index = xl.parse("RAG_Corpus_Index")
    dq = xl.parse("Data_Quality")
    questions = xl.parse("Builder_Questions")
    api_contract = xl.parse("API_Contract")

    # Index Knowledge Docs
    docs_by_id = {}
    for _, r in docs.iterrows():
        docs_by_id[str(r["doc_id"])] = {
            "doc_id": str(r["doc_id"]),
            "title": str(r["title"]),
            "scope": str(r["scope"]),
            "content": str(r["content"]),
        }

    # Index RAG corpus files per asset
    rag_by_asset = {}
    for _, r in rag_index.iterrows():
        aid = str(r["asset_id"])
        rag_by_asset.setdefault(aid, []).append({
            "doc_id": str(r["doc_id"]),
            "file_name": str(r["file_name"]),
            "role": str(r["document_role"]),
        })

    # Index Data Quality issues per asset
    dq_by_asset = {}
    for _, r in dq.iterrows():
        aid = str(r["asset_id"])
        dq_by_asset.setdefault(aid, []).append({
            "issue_id": str(r["issue_id"]),
            "issue_type": str(r["issue_type"]),
            "severity": str(r["severity"]),
            "source_sheet": str(r["source_sheet"]),
            "recommended_handling": str(r["recommended_handling"]),
        })

    # Index Historical Service Cases by (asset_class, issue_code)
    cases_by_key = {}
    all_cases = []
    for _, r in cases.iterrows():
        key = (str(r["asset_class"]), str(r["historical_issue_code"]))
        cases_by_key.setdefault(key, []).append({
            "case_id": str(r["case_id"]),
            "asset_class": str(r["asset_class"]),
            "issue_code": str(r["historical_issue_code"]),
            "interpretation": str(r["historical_interpretation"]),
            "recommended_next_check": str(r["recommended_next_check"]),
            "safe_evidence_workflow": str(r["safe_evidence_workflow"]),
            "severity": str(r["severity"]),
            "knowledge_doc_id": str(r["knowledge_doc_id"]),
        })
        all_cases.append(cases_by_key[key][-1])

    # Index recent Alarms/Events per asset (most recent 6)
    alarms_sorted = alarms.sort_values("timestamp", ascending=False)
    alarms_by_asset = {}
    for aid, grp in alarms_sorted.groupby("asset_id"):
        rows = []
        for _, r in grp.head(6).iterrows():
            rows.append({
                "event_id": str(r["event_id"]),
                "timestamp": _clean_val(r["timestamp"]),
                "event_type": str(r["event_type"]),
                "severity": str(r["severity"]),
                "event_code": str(r["event_code"]),
                "description": str(r["description"]),
                "source": str(r["source"]),
                "status": str(r["status"]),
            })
        alarms_by_asset[str(aid)] = rows

    # Index Maintenance History per asset (most recent 5)
    maint_sorted = maint.sort_values("opened_at", ascending=False)
    maint_by_asset = {}
    for aid, grp in maint_sorted.groupby("asset_id"):
        rows = []
        for _, r in grp.head(5).iterrows():
            rows.append({
                "work_order_id": str(r["work_order_id"]),
                "opened_at": _clean_val(r["opened_at"]),
                "action": str(r["action"]),
                "maintenance_type": str(r["maintenance_type"]),
                "priority": str(r["priority"]),
                "result": str(r["result"]),
                "duration_h": _clean_val(r["duration_h"]),
                "downtime_h": _clean_val(r["downtime_h"]),
                "role": str(r["role"]),
            })
        maint_by_asset[str(aid)] = rows

    # Index Telemetry time-series summary & recent sparkline (last 24 points = 12 hours)
    telemetry_sorted = telemetry.sort_values("timestamp")
    telem_by_asset = {}
    metric_cols = [
        "process_pressure_bar", "process_flow_pct", "position_setpoint_pct",
        "position_actual_pct", "pneumatic_supply_bar", "stiction_index",
        "load_for_opening_pct", "vibration_mm_s", "bearing_temp_c",
        "motor_current_a", "speed_rpm", "signal_quality_pct", "sensor_bias",
    ]
    for aid, grp in telemetry_sorted.groupby("asset_id"):
        tail = grp.tail(24)
        latest_row = tail.iloc[-1]
        latest_metrics = {}
        series = {"timestamps": [_clean_val(t) for t in tail["timestamp"]]}
        stats = {}
        for col in metric_cols:
            vals = grp[col].dropna()
            if len(vals) > 0:
                latest_metrics[col] = _clean_val(latest_row[col])
                series[col] = [_clean_val(v) for v in tail[col]]
                stats[col] = {
                    "latest": round(float(vals.iloc[-1]), 2),
                    "mean": round(float(vals.mean()), 2),
                    "min": round(float(vals.min()), 2),
                    "max": round(float(vals.max()), 2),
                    "std": round(float(vals.std()), 3),
                    "delta_3d": round(float(vals.iloc[-1] - vals.iloc[0]), 2),
                }
        if "position_setpoint_pct" in latest_metrics and "position_actual_pct" in latest_metrics:
            sp = latest_metrics["position_setpoint_pct"] or 0.0
            pv = latest_metrics["position_actual_pct"] or 0.0
            latest_metrics["position_error_pct"] = round(abs(sp - pv), 2)
        telem_by_asset[str(aid)] = {
            "latest": latest_metrics,
            "stats": stats,
            "series": series,
        }

    # Merge Master + Current_State + Scene_Anchors into 30 canonical records
    state_map = {str(r["asset_id"]): r for _, r in state.iterrows()}
    anchor_map = {str(r["asset_id"]): r for _, r in anchors.iterrows()}
    records = {}
    for _, m in master.iterrows():
        aid = str(m["asset_id"])
        st = state_map.get(aid, {})
        an = anchor_map.get(aid, {})
        acls = str(m["asset_class"])
        icode = str(st.get("issue_code", "NORMAL"))
        doc_id = str(st.get("knowledge_doc_id", f"DOC-{acls}-CORE"))
        dq_list = dq_by_asset.get(aid, [])
        model_conf = float(st.get("model_confidence", 0.85))
        map_conf = float(an.get("mapping_confidence", 0.78))
        # Apply trustworthy AI data-quality penalty (Builder_Questions Q-06)
        dq_penalty = min(0.18, 0.04 * len(dq_list) + 0.04 * sum(1 for d in dq_list if d["severity"] == "high"))
        effective_conf = round(max(0.45, model_conf - dq_penalty), 2)

        records[aid] = {
            "asset_id": aid,
            "tag": str(m["tag"]),
            "asset_class": acls,
            "area_id": str(m["area_id"]),
            "area_name": str(m["area_name"]),
            "process_context": str(m["process_context"]),
            "criticality": str(m["criticality"]),
            "installation_date": _clean_val(m["installation_date"]),
            "last_service_date": _clean_val(m["last_service_date"]),
            "service_interval_days": int(m["service_interval_days"]),
            "connectivity_profile": str(m["connectivity_profile"]),
            "scene_anchor_id": str(m["scene_anchor_id"]),
            "qr_id": str(m["qr_id"]),
            "nfc_id": str(m["nfc_id"]),
            "health_score": int(st.get("health_score", 95)),
            "condition_state": str(st.get("condition_state", "healthy")),
            "issue_code": icode,
            "issue_summary": str(st.get("issue_summary", "No significant deviation")),
            "urgency": str(st.get("urgency", "routine")),
            "recommended_next_check": str(st.get("recommended_next_check", "monitor")),
            "model_confidence": round(model_conf, 2),
            "mapping_confidence": round(map_conf, 2),
            "effective_confidence": effective_conf,
            "dq_penalty": round(dq_penalty, 2),
            "knowledge_doc_id": doc_id,
            "knowledge_doc": docs_by_id.get(doc_id),
            "rag_docs": rag_by_asset.get(aid, []),
            "data_quality_issues": dq_list,
            "alarms": alarms_by_asset.get(aid, []),
            "maintenance_history": maint_by_asset.get(aid, []),
            "matched_cases": cases_by_key.get((acls, icode), []),
            "telemetry": telem_by_asset.get(aid, {"latest": {}, "stats": {}, "series": {}}),
        }

    return {
        "records": records,
        "docs": docs_by_id,
        "cases": all_cases,
        "builder_questions": [
            {"id": str(r["question_id"]), "question": str(r["builder_question"]), "focus": str(r["capability_focus"]), "priority": str(r["priority"])}
            for _, r in questions.iterrows()
        ],
        "api_contract": [
            {"method": str(r["method"]), "route": str(r["route"]), "purpose": str(r["purpose"]), "backing_data": str(r["backing_data"])}
            for _, r in api_contract.iterrows()
        ],
    }


# Extracted 3D class -> Excel asset_class whose healthy records supply telemetry profiles
PROFILE_CLASS = {
    "control_valve": "CONTROL_VALVE", "circulation_pump": "PROCESS_PUMP", "electric_motor": "ELECTRIC_MOTOR",
    "pressure_sensor": "PRESSURE_TRANSMITTER", "vibration_sensor": "VIBRATION_SENSOR",
    "expansion_vessel": "PRESSURE_TRANSMITTER", "heat_exchanger": "PRESSURE_TRANSMITTER",
    "vertical_pipe": "PRESSURE_TRANSMITTER", "horizontal_pipe": "PRESSURE_TRANSMITTER",
    "overhead_pipe": "PRESSURE_TRANSMITTER", "pipe_fitting": "PRESSURE_TRANSMITTER",
    "electrical_cabinet": "ELECTRIC_MOTOR",
}

CANONICAL_CLASS_LABELS = {
    "CONTROL_VALVE": ("control_valve", "control valve"),
    "PROCESS_PUMP": ("circulation_pump", "process pump"),
    "ELECTRIC_MOTOR": ("electric_motor", "electric motor"),
    "PRESSURE_TRANSMITTER": ("pressure_sensor", "pressure transmitter"),
    "VIBRATION_SENSOR": ("vibration_sensor", "vibration sensor"),
}


def _assign_camera_view(center: list[float], size: list[float], cls: str) -> dict:
    """Map a 3D asset (cx, cy, cz) in Equipment Room.ply to its matching 2D camera frame (cam1..cam6) and 2D box."""
    cx, cy, cz = center
    sx, sy, sz = size
    bw = float(np.clip(max(sx, sz) * 0.34, 0.08, 0.26))
    bh = float(np.clip(sy * 0.42, 0.11, 0.32))

    # Station 1 (cam1.jpg): North-East Manifold Wall (Z > 1.4, X > 2.0) — AtmosTech cabinet, blue valve CV-003, Grundfos pump, white cabinet
    if cz > 1.4 and cx > 2.0:
        if 2.65 <= cx <= 3.25 and 0.45 <= cy <= 0.92:
            return {
                "station": "s4-a8",
                "score": 0.242,
                "image": "./data/views/cam1.jpg",
                "box": [0.285, 0.505, 0.105, 0.155],
                "prompt": "flanged blue control valve and actuator assembly on boiler manifold",
            }
        if 2.65 <= cx <= 3.25 and cy > 0.92:
            return {
                "station": "s4-a8",
                "score": 0.251,
                "image": "./data/views/cam1.jpg",
                "box": [0.265, 0.385, 0.125, 0.145],
                "prompt": "a circulation pump attached to pipes",
            }
        u = float(np.clip(0.55 - (cx - 2.4) * 0.28, 0.10, 0.86))
        v = float(np.clip(0.84 - cy * 0.42, 0.12, 0.80))
        return {
            "station": "s4-a8",
            "score": 0.198,
            "image": "./data/views/cam1.jpg",
            "box": [round(u - bw / 2, 3), round(v - bh / 2, 3), round(bw, 3), round(bh, 3)],
            "prompt": f"a {cls.replace('_', ' ')} attached to boiler room manifold",
        }

    # Station 2 (cam2.jpg): North-Center Manifold Wall (Z > 1.4, 0.2 <= X <= 2.0) — 4 risers with red inline pumps, blue box actuators & green arrows
    if cz > 1.4 and cx >= 0.2:
        u = float(np.clip(0.82 - (cx - 0.5) * 0.34, 0.14, 0.88))
        v = float(np.clip(0.84 - cy * 0.42, 0.12, 0.80))
        return {
            "station": "s4-a9",
            "score": 0.215,
            "image": "./data/views/cam2.jpg",
            "box": [round(u - bw / 2, 3), round(v - bh / 2, 3), round(bw, 3), round(bh, 3)],
            "prompt": f"a {cls.replace('_', ' ')} attached to vertical insulated pipes",
        }

    # Station 3 (cam3.jpg): North-West Entrance & Gate Valve Riser (Z > 0.6, X < 0.2)
    if cz > 0.6 and cx < 0.2:
        u = float(np.clip(0.45 - cx * 0.18, 0.10, 0.85))
        v = float(np.clip(0.82 - cy * 0.42, 0.12, 0.80))
        return {
            "station": "s2-b3",
            "score": 0.192,
            "image": "./data/views/cam3.jpg",
            "box": [round(u - bw / 2, 3), round(v - bh / 2, 3), round(bw, 3), round(bh, 3)],
            "prompt": f"a {cls.replace('_', ' ')} on primary distribution line",
        }

    # Station 4 (cam4.jpg): South-West Twin Floor Pump Pedestal & Blue Butterfly Valve (Z < -1.1, X < -1.2)
    if cz < -1.1 and cx < -1.2:
        u = float(np.clip(0.58 + (cx + 2.8) * 0.22, 0.12, 0.84))
        v = float(np.clip(0.82 - cy * 0.42, 0.14, 0.78))
        return {
            "station": "s1-c4",
            "score": 0.224,
            "image": "./data/views/cam4.jpg",
            "box": [round(u - bw / 2, 3), round(v - bh / 2, 3), round(bw, 3), round(bh, 3)],
            "prompt": f"industrial {cls.replace('_', ' ')} mounted on floor pedestal",
        }

    # Station 6 (cam6.jpg): West Blue Expansion Tank & Twin Grundfos MAGNA3 Pumps (-1.1 <= Z <= 0.8, X < -1.0)
    if cx < -1.0:
        if cx < -2.35:
            return {
                "station": "s3-d6",
                "score": 0.236,
                "image": "./data/views/cam6.jpg",
                "box": [0.155, 0.125, 0.310, 0.820],
                "prompt": "large blue hydronic expansion tank",
            }
        u = float(np.clip(0.68 + (cx + 1.9) * 0.25, 0.48, 0.88))
        v = float(np.clip(0.82 - cy * 0.44, 0.18, 0.82))
        return {
            "station": "s3-d6",
            "score": 0.229,
            "image": "./data/views/cam6.jpg",
            "box": [round(u - bw / 2, 3), round(v - bh / 2, 3), round(bw, 3), round(bh, 3)],
            "prompt": f"Grundfos {cls.replace('_', ' ')} assembly on hydronic header",
        }

    # Station 5 (cam5.jpg): South-Center Red Reflex Expansion Vessels & Heat Exchanger
    if -0.25 <= cx <= 1.25 and cz < -1.2:
        return {
            "station": "s5-e2",
            "score": 0.248,
            "image": "./data/views/cam5.jpg",
            "box": [0.465, 0.155, 0.245, 0.585],
            "prompt": "red Reflex pressure expansion vessel",
        }
    u = float(np.clip(0.52 + cx * 0.16, 0.10, 0.86))
    v = float(np.clip(0.82 - cy * 0.42, 0.14, 0.80))
    return {
        "station": "s5-e2",
        "score": 0.188,
        "image": "./data/views/cam5.jpg",
        "box": [round(u - bw / 2, 3), round(v - bh / 2, 3), round(bw, 3), round(bh, 3)],
        "prompt": f"a {cls.replace('_', ' ')} in boiler equipment bay",
    }


def _preference_score(obj: dict, excel_rec: dict, used_centers: list) -> float:
    """Score how well a 3D cluster matches a given Excel asset record."""
    cx, cy, cz = obj["center"]
    sx, sy, sz = obj["size"]
    acls = excel_rec["asset_class"]
    aid = excel_rec["asset_id"]
    score = 0.0

    if obj.get("movable") or obj["class"] in NON_DEVICE:  # a chair, bucket or washing machine is never an Excel device
        return -1e6

    # Canonical assets belong on the equipment tier (0.15m <= Y <= 1.72m)
    if obj["tier"] == "equipment":
        score += 40.0
    else:
        score -= 60.0

    # The demo target keeps its hand-verified geometric label so its route/camera anchor stays stable
    ocls = obj.get("geo_class", obj["class"]) if aid == C.TARGET_ASSET_ID else obj["class"]
    if acls == "CONTROL_VALVE":
        if ocls == "control_valve":
            score += 45.0
        elif ocls in ("circulation_pump", "pressure_sensor"):
            score += 10.0
        else:
            score -= 25.0
    elif acls == "PROCESS_PUMP":
        if ocls == "circulation_pump":
            score += 50.0
        elif ocls in ("electric_motor", "process_unit"):
            score += 18.0
        else:
            score -= 25.0
    elif acls == "ELECTRIC_MOTOR":
        if ocls == "electric_motor":
            score += 48.0
        elif ocls == "circulation_pump":
            score += 20.0
        else:
            score -= 25.0
    elif acls == "PRESSURE_TRANSMITTER":
        if ocls in ("pressure_sensor", "flow_marker"):
            score += 45.0
        else:
            score -= 20.0
    elif acls == "VIBRATION_SENSOR":
        if ocls in ("pressure_sensor", "electric_motor", "circulation_pump"):
            score += 40.0
        else:
            score -= 20.0

    # Target ASSET-003 (CV-003): the motorised control valve verified in the real video (C.TARGET_ANCHOR);
    # the object closest to the anchor wins whatever its AI label (the actuator can read as a motor).
    if aid == C.TARGET_ASSET_ID:
        lo, hi = C.TARGET_ZONE
        if all(l <= c <= h for c, l, h in zip((cx, cy, cz), lo, hi)):
            score += 400.0 - 1500.0 * math.dist((cx, cy, cz), C.TARGET_ANCHOR)
        else:
            score -= 400.0

    # Prefer substantial, well-observed equipment clusters (not tiny noise crumbs)
    # Physical size sanity: a transmitter or vibration sensor is hand-sized, a valve is not a room-sized object
    ext = max(sx, sz)
    if acls in ("PRESSURE_TRANSMITTER", "VIBRATION_SENSOR") and (ext > 0.6 or sy > 0.8):
        score -= 120.0
    elif acls == "CONTROL_VALVE" and ext > 1.2:
        score -= 80.0

    # Prefer objects whose class the AI verified (from images or multi-view detection) over colour-rule guesses
    if obj.get("label_source", "geometric_rules") == "geometric_rules":
        score -= 35.0

    score += min(18.0, math.log1p(obj["n_gaussians"]) * 2.8)
    if 0.35 <= cy <= 1.55:
        score += 10.0

    # Spatial separation between canonical assets so HUD callouts don't stack on top of each other
    if used_centers:
        min_d = min(math.hypot(cx - ux, cz - uz) + 0.6 * abs(cy - uy) for ux, uy, uz in used_centers)
        if min_d < 0.25:
            score -= 45.0
        elif min_d < 0.45:
            score -= 12.0
        else:
            score += min(10.0, min_d * 3.0)

    return score


NON_DEVICE = {"appliance", "sink", "shelf_rack"}   # real objects, but not instrumented plant devices


def _movable_record(obj: dict, scan_center, n: int, excel_pack: dict) -> dict:
    """Movable objects (chairs, carts, buckets, crates...) are not instrumented devices: no telemetry,
    tracked as changeable obstacles for the navigating robot."""
    prefix = {"movable_furniture": "MF", "movable_cart": "MC", "movable_container": "MB",
              "movable_crate": "MX", "movable_ladder": "ML", "movable_hose": "MH", "movable_cleaning": "MCL",
              "appliance": "FA", "sink": "FS", "shelf_rack": "FR"}.get(obj["class"], "MO")
    rec = {
        **obj,
        "scan_center": scan_center,
        "camera_view": _assign_camera_view(obj["center"], obj["size"], obj["class"]),
        "mapped": False, "is_target": False, "instrumented": False,
        "asset_id": f"MOV-{obj['id'].split('-')[1]}", "tag": f"{prefix}-{n:03d}",
        "asset_class": "MOVABLE_OBJECT", "area_id": "AREA-BOILER", "area_name": "Equipment Room floor",
        "process_context": "movable_item", "criticality": "n/a - not instrumented",
        "health_score": None, "condition_state": "movable",
        "issue_code": "MOVABLE", "issue_summary": "Movable object; not instrumented. Treated as a changeable obstacle.",
        "urgency": "routine", "recommended_next_check": "verify position before routing",
        "model_confidence": obj.get("class_confidence", 0.7), "mapping_confidence": obj.get("class_confidence", 0.7),
        "effective_confidence": obj.get("class_confidence", 0.7), "dq_penalty": 0.0,
        "qr_id": None, "nfc_id": None, "installation_date": None, "last_service_date": None,
        "connectivity_profile": "none", "knowledge_doc_id": "DOC-3D-MAPPING",
        "knowledge_doc": excel_pack["docs"].get("DOC-3D-MAPPING"),
        "rag_docs": [], "data_quality_issues": [], "alarms": [], "maintenance_history": [], "matched_cases": [],
        "telemetry": {"latest": {}, "stats": {}, "series": {}},
    }
    if not obj.get("movable"):  # fixed facility item (washing machine, sink, shelf): real, but not instrumented
        rec.update({"asset_class": "FACILITY_ITEM", "asset_id": f"FAC-{obj['id'].split('-')[1]}",
                    "process_context": "facility_item", "condition_state": "not_instrumented",
                    "issue_code": "NOT_INSTRUMENTED", "recommended_next_check": "none - not a monitored device",
                    "issue_summary": "Facility item; not an instrumented device. Shown for situational awareness."})
    return rec


def bind_assets_to_scene(extracted_assets: list, excel_pack: dict) -> list:
    """Bind the 30 canonical Excel records to 30 distinct 3D objects, and enrich
    all remaining 3D objects with procedural telemetry & status so every single
    3D bounding box is inspectable.
    """
    records = excel_pack["records"]
    # Order so TARGET_ASSET_ID is bound first, then degraded/watch assets, then healthy
    priority_order = sorted(
        records.values(),
        key=lambda r: (
            0 if r["asset_id"] == C.TARGET_ASSET_ID else (1 if r["condition_state"] == "degraded" else (2 if r["condition_state"] == "watch" else 3)),
            r["health_score"],
            r["asset_id"],
        ),
    )

    used_obj_ids = set()
    used_centers = []
    obj_to_excel = {}

    for rec in priority_order:
        best_obj = None
        best_score = -1e9
        for obj in extracted_assets:
            if obj["id"] in used_obj_ids:
                continue
            sc = _preference_score(obj, rec, used_centers)
            if sc > best_score:
                best_score = sc
                best_obj = obj
        if best_obj is not None:
            used_obj_ids.add(best_obj["id"])
            used_centers.append(best_obj["center"])
            obj_to_excel[best_obj["id"]] = rec

    rng = np.random.default_rng(C.RANDOM_SEED)
    healthy_by_class = {"_any": sorted((r for r in records.values() if r["condition_state"] == "healthy"), key=lambda r: r["asset_id"])}
    for r in healthy_by_class["_any"]:
        healthy_by_class.setdefault(r["asset_class"], []).append(r)
    enriched = []
    aux_counter = 1
    for obj in extracted_assets:
        oid = obj["id"]
        rec = obj_to_excel.get(oid)
        cx, cy, cz = obj["center"]
        scan_center = [round(cx, 3), round(1.15 - cy, 3), round(-cz, 3)]
        if rec is not None:
            is_target = rec["asset_id"] == C.TARGET_ASSET_ID
            c_cls, c_label = CANONICAL_CLASS_LABELS.get(rec["asset_class"], (obj["class"], obj["label"]))
            cam_view = _assign_camera_view(obj["center"], obj["size"], c_cls)
            enriched.append({
                **obj,
                "class": c_cls,
                "label": c_label,
                "scene_class": obj["class"],                   # what the scan/AI says the object is
                "scene_label_source": obj.get("label_source", "geometric_rules"),
                "scan_center": scan_center,
                "camera_view": cam_view,
                "mapped": True,
                "is_target": is_target,
                "asset_id": rec["asset_id"],
                "tag": rec["tag"],
                "asset_class": rec["asset_class"],
                "area_id": rec["area_id"],
                "area_name": rec["area_name"],
                "process_context": rec["process_context"],
                "criticality": rec["criticality"],
                "health_score": rec["health_score"],
                "condition_state": rec["condition_state"],
                "issue_code": rec["issue_code"],
                "issue_summary": rec["issue_summary"],
                "urgency": rec["urgency"],
                "recommended_next_check": rec["recommended_next_check"],
                "model_confidence": rec["model_confidence"],
                "mapping_confidence": rec["mapping_confidence"],
                "effective_confidence": rec["effective_confidence"],
                "dq_penalty": rec["dq_penalty"],
                "qr_id": rec["qr_id"],
                "nfc_id": rec["nfc_id"],
                "installation_date": rec["installation_date"],
                "last_service_date": rec["last_service_date"],
                "connectivity_profile": rec["connectivity_profile"],
                "knowledge_doc_id": rec["knowledge_doc_id"],
                "knowledge_doc": rec["knowledge_doc"],
                "rag_docs": rec["rag_docs"],
                "data_quality_issues": rec["data_quality_issues"],
                "alarms": rec["alarms"],
                "maintenance_history": rec["maintenance_history"],
                "matched_cases": rec["matched_cases"],
                "telemetry": rec["telemetry"],
            })
        else:
            # Surrounding asset: borrow a HEALTHY Excel record of the closest equipment class as its
            # metadata/telemetry profile, so every number shown in 3D traces back to the Builder Pack.
            if obj.get("movable") or obj["class"] in NON_DEVICE:
                enriched.append(_movable_record(obj, scan_center, aux_counter, excel_pack))
                aux_counter += 1
                continue
            prof_class = PROFILE_CLASS.get(obj["class"], "PRESSURE_TRANSMITTER")
            pool = healthy_by_class.get(prof_class) or healthy_by_class["_any"]
            prof = pool[int(oid.split("-")[1]) % len(pool)]
            tag_prefix = {
                "vertical_pipe": "PR", "overhead_pipe": "OM", "horizontal_pipe": "HP",
                "expansion_vessel": "EV", "circulation_pump": "CP", "flow_marker": "FM",
                "electrical_cabinet": "EC", "pressure_sensor": "PS", "control_valve": "AV",
                "electric_motor": "EM", "heat_exchanger": "HX", "pipe_fitting": "PF",
                "process_unit": "PU", "structural_column": "SC", "ceiling_fixture": "CF",
                "industrial_asset": "AX", "clutter": "AX",
            }.get(obj["class"], "AX")
            tag = f"{tag_prefix}-{aux_counter:03d}"
            aux_counter += 1
            cam_view = _assign_camera_view(obj["center"], obj["size"], obj["class"])
            enriched.append({
                **obj,
                "scan_center": scan_center,
                "camera_view": cam_view,
                "mapped": False,
                "is_target": False,
                "asset_id": f"AUX-{oid.split('-')[1]}",
                "tag": tag,
                "asset_class": prof["asset_class"],
                "excel_profile_source": prof["asset_id"],
                "excel_profile_tag": prof["tag"],
                "area_id": prof["area_id"],
                "area_name": prof["area_name"],
                "process_context": prof["process_context"],
                "criticality": "C - standard",
                "health_score": prof["health_score"],
                "condition_state": "healthy",
                "issue_code": "NORMAL",
                "issue_summary": f"Nominal; telemetry profile from {prof['tag']} ({prof['asset_id']})",
                "urgency": "routine",
                "recommended_next_check": "monitor",
                "model_confidence": prof["model_confidence"],
                "mapping_confidence": obj["class_confidence"],
                "effective_confidence": round(min(prof["model_confidence"], obj["class_confidence"]), 2),
                "dq_penalty": 0.0,
                "qr_id": f"QR-AUX-{aux_counter:04d}",
                "nfc_id": f"NFC-AUX-{aux_counter:04d}",
                "installation_date": prof["installation_date"],
                "last_service_date": prof["last_service_date"],
                "connectivity_profile": prof["connectivity_profile"],
                "knowledge_doc_id": prof["knowledge_doc_id"],
                "knowledge_doc": prof["knowledge_doc"],
                "rag_docs": [],
                "data_quality_issues": [],
                "alarms": [],
                "maintenance_history": [],
                "matched_cases": [],
                "telemetry": prof["telemetry"],
            })

    return enriched
