"""Small, deterministic operator context from an already-built twin.

No workbook reads, model calls, scene edits or new asset bindings. Energy-class
rules are adapted from SinceAI-2026/rasse, commit 5a3ca0241255c7a22bafede9c251ba2ab058e849
(hazards/rules.json). We deliberately omit its inferred isolators, safety-state
machine and alternate condition ranking. These are synthetic recorded signals,
not live instrument readings or verification of safe isolation.
"""
from __future__ import annotations

import math

from pipeline.asset_records import UNMAPPED_NOTICE

RASSE_REVISION = "5a3ca0241255c7a22bafede9c251ba2ab058e849"
ENERGY = {
    "pressure": ("Pressure", "process_pressure_bar", "bar", 0.1, 1.0),
    "rotation": ("Rotation", "speed_rpm", "rpm", 5.0, 0.9),
    "electrical": ("Electrical", "motor_current_a", "A", 0.5, 0.8),
    "heat": ("Heat", "bearing_temp_c", "°C", 65.0, 0.7),
}
CLASS_ENERGY = {
    "PROCESS_PUMP": ("rotation", "pressure", "electrical", "heat"),
    "ELECTRIC_MOTOR": ("rotation", "electrical", "heat"),
    "CONTROL_VALVE": ("pressure",), "PRESSURE_TANK": ("pressure",),
    "PRESSURE_TRANSMITTER": ("pressure",), "ELECTRIC_PANEL": ("electrical",),
    "CONTROL_PANEL": ("electrical",), "RADIATOR": ("heat",),
}
METRICS = {
    "position_actual_pct": ("Actual position", "%"),
    "vibration_mm_s": ("Vibration", "mm/s"),
    "bearing_temp_c": ("Bearing temperature", "°C"),
    "motor_current_a": ("Motor current", "A"),
    "process_flow_pct": ("Process flow", "%"),
    "process_pressure_bar": ("Process pressure", "bar"),
    "stiction_index": ("Stiction index", "index"),
    "signal_quality_pct": ("Signal quality", "%"),
}
ISSUE_METRIC = {
    "POSITION_DEVIATION": "position_actual_pct", "CALIBRATION_DRIFT": "position_actual_pct",
    "MISALIGNMENT": "vibration_mm_s", "MOUNTING_ISSUE": "vibration_mm_s",
    "CURRENT_IMBALANCE": "motor_current_a", "OVERHEATING": "bearing_temp_c",
    "FOULING": "process_flow_pct", "CAVITATION_RISK": "process_pressure_bar",
    "SEAL_DEGRADATION": "bearing_temp_c", "STICTION": "stiction_index",
}
CLASS_METRIC = {
    "CONTROL_VALVE": "position_actual_pct", "PROCESS_PUMP": "vibration_mm_s",
    "ELECTRIC_MOTOR": "vibration_mm_s", "VIBRATION_SENSOR": "vibration_mm_s",
    "PRESSURE_TRANSMITTER": "process_pressure_bar",
}


def finite(value):
    """Reject bools, strings and non-finite values; missing never becomes zero."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _vector(value):
    return list(value) if isinstance(value, (list, tuple)) and len(value) == 3 and all(finite(v) is not None for v in value) else None


def _snapshot(asset):
    series = (asset.get("telemetry") or {}).get("series") or {}
    stamps = [v for v in series.get("timestamps", []) if isinstance(v, str) and v.strip()]
    return max(stamps) if stamps else None


def _telemetry_ids(asset, signal):
    """Use an existing observation ID only; do not invent a pack row ID."""
    expected = f"TELEM:{asset['asset_id']}:{signal}"
    return [expected] if any(o.get("source_id") == expected for o in
                            (asset.get("diagnostics") or {}).get("observations", []) if isinstance(o, dict)) else []


def _signal_reference(asset, signal, snapshot, scope="latest_sample"):
    return {"sheet": "Telemetry_Sample_3d", "asset_id": asset["asset_id"],
            "field": signal, "as_of": snapshot, "scope": scope}


def _primary_metric(asset, snapshot):
    telemetry = asset.get("telemetry") or {}
    latest = telemetry.get("latest") or {}
    signal = ISSUE_METRIC.get(asset.get("issue_code")) or CLASS_METRIC.get(asset.get("asset_class"))
    # Missing issue-relevant evidence is explicit; do not replace it with an
    # unrelated healthy metric just to fill the card.
    if signal not in METRICS:
        return None
    value = finite(latest.get(signal))
    label, unit = METRICS[signal]
    refs = _telemetry_ids(asset, signal)
    fields = [_signal_reference(asset, signal, snapshot)]
    comparison = None
    if signal == "position_actual_pct":
        target = finite(latest.get("position_setpoint_pct"))
        if target is not None:
            comparison = {"kind": "setpoint", "value": target, "label": "Setpoint",
                          "signal": "position_setpoint_pct", "time_window": "same archived sample"}
            refs.extend(_telemetry_ids(asset, "position_setpoint_pct"))
            fields.append(_signal_reference(asset, "position_setpoint_pct", snapshot))
    else:
        target = finite(((telemetry.get("stats") or {}).get(signal) or {}).get("mean"))
        if target is not None:
            comparison = {"kind": "own_history_mean", "value": target, "label": "Own 3-day mean",
                          "signal": signal, "time_window": "stored 3-day aggregate"}
            fields.append(_signal_reference(asset, signal, None, "stored_3_day_mean"))
    return {"signal": signal, "label": label, "latest": value, "unit": unit, "as_of": snapshot,
            "comparison": comparison, "delta": round(value - comparison["value"], 6) if value is not None and comparison else None,
            "source_ids": refs, "source_refs": fields,
            "quality": "missing_or_invalid" if value is None else "data_quality_flagged" if asset.get("data_quality_issues") else "recorded",
            "comparison_notice": "Historical mean is context, not a healthy baseline or an operating limit." if comparison and comparison["kind"] == "own_history_mean" else None}


def _hazards(asset, snapshot):
    latest = (asset.get("telemetry") or {}).get("latest") or {}
    result = []
    for kind in CLASS_ENERGY.get(asset.get("asset_class"), ()):
        label, signal, unit, threshold, radius = ENERGY[kind]
        value = finite(latest.get(signal))
        state = "unknown" if value is None else "recorded_energy" if value > threshold else "potential"
        result.append({
            "kind": kind, "label": label, "state": state,
            "reading": {"signal": signal, "value": value, "unit": unit, "as_of": snapshot,
                        "source_ids": _telemetry_ids(asset, signal),
                        "source_refs": [_signal_reference(asset, signal, snapshot)],
                        "quality": "missing_or_invalid" if value is None else "data_quality_flagged" if asset.get("data_quality_issues") else "archived_synthetic"},
            "threshold": {"value": threshold, "unit": unit, "provenance": "Generic demonstration assumption; not a site operating limit"},
            "radius_scene_units": radius,
            "provenance": {"kind": "class_rule_and_synthetic_workbook", "rule_revision": RASSE_REVISION,
                           "class_source": f"Asset_Master:{asset['asset_id']}", "simulated": True},
            "notice": "Current does not prove voltage absence or electrical isolation." if kind == "electrical" else
                      "Archived synthetic reading; no isolation, local measurement or safe-work state is verified.",
        })
    return result


def build_scene_context(twin: dict) -> dict:
    """Return a JSON-safe context without changing the twin or generating data."""
    devices = {}
    assets = twin.get("assets") or []
    for asset in assets:
        if asset.get("mapped") is not True or not asset.get("id") or not asset.get("asset_id"):
            continue
        snapshot = _snapshot(asset)
        aid = asset["asset_id"]
        # Keep only source-backed operator fields, never old borrowed profiles,
        # model-generated confidence rankings or unrelated service documents.
        quality = [{k: v for k, v in q.items() if k in {"issue_id", "issue_type", "severity", "source_sheet", "recommended_handling"} and isinstance(v, str)}
                   for q in asset.get("data_quality_issues", []) if isinstance(q, dict)]
        devices[asset["id"]] = {
            "object_id": asset["id"], "asset_id": aid, "tag": asset.get("tag"),
            "asset_class": asset.get("asset_class"), "condition_state": asset.get("condition_state"),
            "health_score": finite(asset.get("health_score")), "issue_code": asset.get("issue_code"),
            "issue_summary": asset.get("issue_summary"), "urgency": asset.get("urgency"),
            "next_check": asset.get("recommended_next_check"),
            "snapshot_at": snapshot, "status_as_of": asset.get("last_updated"),
            "source_ids": [f"Current_State:{aid}", f"Asset_Master:{aid}"],
            "primary_metric": _primary_metric(asset, snapshot), "data_quality": quality,
            "hazards": _hazards(asset, snapshot), "center": _vector(asset.get("center")),
            "aabb_min": _vector(asset.get("aabb_min")), "aabb_max": _vector(asset.get("aabb_max")),
            "provenance": {"kind": "synthetic_workbook", "mapping": "existing_procedural_scene_binding",
                           "status_preserved": True, "live": False},
        }
        if asset.get("simulated_emergency"):
            # Upstream's board demo can copy a scripted fault onto another
            # device in memory. Its original record is not recoverable here;
            # do not cite that recipient's workbook row for the borrowed data.
            device = devices[asset["id"]]
            device.update(simulated_emergency=True, source_ids=[], snapshot_at=None, status_as_of=None,
                          provenance={"kind": "runtime_scenario", "live": False,
                                      "status_preserved": True, "canonical_status_available": False,
                                      "notice": "Scripted demo fault assigned in memory; readings are not this device's original workbook record."})
            metric = device["primary_metric"]
            if metric:
                metric.update(source_ids=[], source_refs=[], as_of=None, quality="runtime_scenario")
                if metric["comparison"]:
                    metric["comparison"] = {**metric["comparison"], "label": "Scenario reference",
                                             "time_window": "scripted demonstration profile"}
                metric["comparison_notice"] = "Comparison belongs to a scripted demonstration profile."
            for hazard in device["hazards"]:
                hazard["reading"].update(source_ids=[], source_refs=[], as_of=None, quality="runtime_scenario")
                hazard["provenance"].update(kind="class_rule_and_runtime_scenario", class_source=None)
                hazard["notice"] = "Scripted demonstration reading; no real energy state or isolation is verified."
    return {
        "schema_version": 1, "scale_verified": False, "units": "scene_units",
        "provenance": {"adapter": "pipeline.scene_context", "rules_from": "SinceAI-2026/rasse",
                       "rules_revision": RASSE_REVISION, "source": "active digital_twin.json",
                       "readings": "archived synthetic workbook", "navigation_modified": False,
                       "notice": "Hazard shapes are illustrative scan-space overlays, not certified clearances or a route safety assessment."},
        "counts": {"assets": len(assets), "mapped": len(devices), "unmapped": len(assets) - len(devices),
                   "hazard_assets": sum(bool(d["hazards"]) for d in devices.values())},
        "unmapped_notice": UNMAPPED_NOTICE, "devices": devices,
    }
