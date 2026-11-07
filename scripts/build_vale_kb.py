"""Build the voice assistant's (Vale's) knowledge base from the Excel builder pack.

    .venv/bin/python -m scripts.build_vale_kb        # writes web/data/vale_kb/*.md

Three short documents replace the whole pack as one PDF. Readings, alarms, work orders and the job log are not
in them: Vale reads those live through the MCP tools (mcp_server.py), so the knowledge base never goes stale.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

from pipeline import config as C

OUT = C.OUT_DIR / "vale_kb"
UNITS = {"bar": "bar", "pct": "%", "_c": "°C", "mm_s": "mm/s", "_a": "A", "_kw": "kW", "rpm": "rpm", "_ma": "mA",
         "index": "index"}


def _unit(col: str) -> str:
    return next((u for k, u in UNITS.items() if col.endswith(k)), "")


def _reading(col: str) -> str:
    """position_actual_pct -> position actual"""
    return " ".join(w for w in col.split("_") if w not in ("pct", "bar", "c", "mm", "s", "a", "kw", "ma"))


def _words(s) -> str:
    return str(s).replace("_", " ").lower()


def register(x: dict) -> str:
    am, cs, dq = x["Asset_Master"], x["Current_State"].set_index("asset_id"), x["Data_Quality"]
    quality = dq.groupby("asset_id")["issue_type"].apply(lambda s: ", ".join(f"{_words(k)} ({v})" for k, v in Counter(s).most_common()))
    lines = ["# Field Atlas equipment register: Equipment Room",
             "",
             "Synthetic Valmet builder-pack data for the 30 registered assets. Current state, readings, alarms and "
             "work orders change: get them with the get_equipment_status tool. The condition below is the pack's "
             "baseline.",
             ""]
    for r in am.sort_values("tag").itertuples():
        s = cs.loc[r.asset_id]
        lines += [f"## {r.tag} ({r.asset_id}), {_words(r.asset_class)}",
                  f"- Location: {r.area_name} ({r.area_id}). Process: {r.process_context}.",
                  f"- Criticality {r.criticality}. Installed {str(r.installation_date)[:10]}, last service "
                  f"{str(r.last_service_date)[:10]}, service interval {r.service_interval_days} days.",
                  f"- Identify it by QR {r.qr_id} or NFC {r.nfc_id}. Connectivity: {r.connectivity_profile}.",
                  f"- Baseline condition: {_words(s.condition_state)}, health {s.health_score}. "
                  + (f"Issue {s.issue_code}: {s.issue_summary}. Urgency {s.urgency}. Next check: {s.recommended_next_check}."
                     if s.condition_state != "healthy" else "No open issue."),
                  f"- Guidance document: {s.knowledge_doc_id}."]
        if r.asset_id in quality:
            lines.append(f"- Known data-quality flags: {quality[r.asset_id]}. Treat affected readings with care.")
        lines.append("")
    return "\n".join(lines)


def troubleshooting(x: dict) -> str:
    docs = x["Knowledge_Docs"].set_index("doc_id")
    cases, tele = x["Historical_Service_Cases"], x["Telemetry_Sample_3d"]
    signal_cols = [c for c in tele.columns if c not in ("timestamp", "asset_id", "tag", "asset_class", "area_id")]
    lines = ["# Field Atlas troubleshooting by equipment type", "",
             "For each equipment type: what to look at, the readings it reports, and every historical service case "
             "by issue code. Cases are synthetic examples from the Valmet builder pack; cite them by case id. "
             f"Every case follows the same workflow: {cases.safe_evidence_workflow.iloc[0]}", ""]
    for cls in sorted(cases.asset_class.unique()):
        doc = docs.loc[f"DOC-{cls}-CORE"] if f"DOC-{cls}-CORE" in docs.index else None
        lines.append(f"## {_words(cls).capitalize()}")
        if doc is not None:
            lines += [f"{doc.title} ({doc.name}): {doc.content}", ""]
        sub = tele[tele.asset_class == cls]
        reads = [c for c in signal_cols if sub[c].notna().any() and pd.api.types.is_float_dtype(sub[c])]
        if reads:
            lines.append("Readings: " + "; ".join(
                f"{_reading(c)} ({_unit(c) or 'value'}, typical {sub[c].quantile(.05):.3g} to {sub[c].quantile(.95):.3g})" for c in reads) + ".")
            lines.append("")
        for code, grp in cases[cases.asset_class == cls].groupby("historical_issue_code"):
            g = grp.iloc[0]
            lines.append(f"### {code}: {g.historical_interpretation}")
            lines.append(f"- Next check: {'; '.join(dict.fromkeys(grp.recommended_next_check))}.")
            lines.append(f"- Severity seen: {', '.join(sorted(set(grp.severity)))}. Cases: {', '.join(grp.case_id)}.")
            lines.append("")
    return "\n".join(lines)


def safety(x: dict) -> str:
    docs = x["Knowledge_Docs"].set_index("doc_id")
    return "\n".join([
        "# Field Atlas safety and evidence rules", "",
        f"## {docs.loc['DOC-FIELD-SAFETY'].title} (DOC-FIELD-SAFETY)", docs.loc["DOC-FIELD-SAFETY"].content, "",
        "## Rules for every job",
        "- Confirm the equipment's identity first: tag plate, QR or NFC code, and location.",
        "- Site safety, permits and lockout/tagout come before any physical step. The job log refuses other steps "
        "until the safety step is confirmed.",
        "- The assistant recommends checks and escalation. It never tells anyone to bypass a procedure, permit or "
        "OEM instruction, and it never claims a fix that was not confirmed.",
        "- A job closes as fixed only when every checklist step is confirmed. Otherwise close it as not fixed or "
        "escalated, with the reason.",
        "- Unknown or hazardous conditions: stop work, keep clear, escalate to a qualified person.", "",
        f"## {docs.loc['DOC-RAG-GUIDE'].title} (DOC-RAG-GUIDE)", docs.loc["DOC-RAG-GUIDE"].content, "",
        f"## {docs.loc['DOC-3D-MAPPING'].title} (DOC-3D-MAPPING)", docs.loc["DOC-3D-MAPPING"].content, "",
        "## About the data",
        "All equipment records, readings, alarms, work orders and cases are synthetic data from the Valmet x Since AI "
        "2026 builder pack. Say so if asked whether the plant is real.", ""])


def main():
    x = pd.read_excel(C.XLSX_PATH, sheet_name=None)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in (("equipment-register", register(x)), ("troubleshooting", troubleshooting(x)), ("safety-rules", safety(x))):
        (OUT / f"{name}.md").write_text(text)
        print(f"{name}.md: {len(text):,} characters")


if __name__ == "__main__":
    main()
