"""Read-only API presentation of archived assets and current workbook bindings.

Old scene archives may contain borrowed profiles on unmapped objects. Preserve
those source artifacts, but never present those profiles as this object's data.
"""
from __future__ import annotations


UNMAPPED_NOTICE = (
    "Found in the scan; no workbook record is assigned to this object. "
    "Its condition is unknown; no device status, telemetry, maintenance history or diagnosis is available."
)


def asset_for_api(asset: dict) -> dict:
    """Return a non-mutating operational view; only mapped records have readings."""
    if asset.get("mapped"):
        return dict(asset)
    out = dict(asset)
    candidate = bool(asset.get("source_region_id")) or asset.get("condition_state") == "unknown"
    movable = bool(asset.get("movable")) or asset.get("condition_state") == "movable"
    out.update(
        tag=asset["id"], instrumented=False, health_score=None,
        condition_state="unknown" if candidate else "movable" if movable else "not_instrumented",
        issue_code="NO_PACK_RECORD", issue_summary=UNMAPPED_NOTICE,
        process_context="scan_only", criticality=None, urgency=None,
        recommended_next_check="Assign a verified workbook record before requesting device guidance",
        telemetry={"latest": {}, "stats": {}, "series": {}},
        alarms=[], maintenance_history=[], matched_cases=[], data_quality_issues=[],
        diagnostics={"executive_summary": UNMAPPED_NOTICE, "observations": [],
                     "hypotheses": [], "verification_steps": [], "work_order_draft": None},
        model_confidence=None, mapping_confidence=None, effective_confidence=None, dq_penalty=None,
        qr_id=None, nfc_id=None, installation_date=None, last_service_date=None,
        connectivity_profile="none", knowledge_doc_id=None, knowledge_doc=None, rag_docs=[],
        excel_profile_source=None, excel_profile_tag=None, area_id=None, area_name=None,
        operational_data_available=False,
    )
    if not candidate and not movable and asset.get("asset_class") != "FACILITY_ITEM":
        out["asset_class"] = "UNRECORDED_DEVICE"
    if asset.get("excel_profile_source"):
        out["archived_profile_provenance"] = {
            "source_asset_id": asset["excel_profile_source"],
            "source_tag": asset.get("excel_profile_tag"),
            "status": "Archived borrowed demonstration profile; excluded from operational responses",
        }
    return out


def route_for_api(route: dict) -> dict:
    """Preserve route geometry while suppressing archived borrowed proximity data."""
    def nearby_record(record):
        projected = asset_for_api(record)
        return {key: projected[key] for key in record}
    return {**route, "waypoints": [
        {**waypoint, "nearby_assets": [nearby_record(record) for record in waypoint.get("nearby_assets", [])]}
        for waypoint in route.get("waypoints", [])
    ]}
