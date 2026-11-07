"""HTTP & REST API server for the Valmet Industrial Metaverse Digital Twin.

Serves the interactive 3D WebGL viewer at / and implements the Valmet Excel Pack
API_Contract routes plus interactive AI Copilot Q&A and Work Order drafting.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import threading
import time
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Serving a prepared demo must never silently spend API credits or load secrets.
# Explicit VALMET_OFFLINE=0 is required to opt into the legacy API-enabled mode.
os.environ.setdefault("VALMET_OFFLINE", "1")
from pipeline import asset_export, decisions_client, field_service, retrieval

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_PATH = WEB_DIR / "data" / "digital_twin.json"
EDITS_PATH = WEB_DIR / "data" / "scene_edits.json"   # room changes made in the viewer (objects removed / restored)
_EDITS_LOCK = threading.Lock()

TWIN_DATA: dict = {}
ASSETS_BY_ID: dict = {}
ASSETS_BY_OBJ: dict = {}
WORK_ORDER_DRAFTS: list = []

# Library saves take up to a few minutes, so they run in a thread and the viewer polls GET /api/library. A reload
# or a closed tab no longer loses the result. oid -> {"started": time} while running, {"error": msg} if it failed.
SAVING: dict = {}
SAVING_LOCK = threading.Lock()


def _save_to_library(oid: str) -> None:
    try:
        asset_export.save(oid)
        result = None
    except Exception as err:  # reported to the viewer; ImportError when torch is missing
        result = {"error": f"{type(err).__name__}: {err}" if not isinstance(err, (ValueError, RuntimeError, OSError)) else str(err)}
    with SAVING_LOCK:
        if result:
            SAVING[oid] = result
        else:
            SAVING.pop(oid, None)


def saving_status() -> dict:
    now = time.time()
    with SAVING_LOCK:
        return {oid: ({"seconds": round(now - j["started"])} if "started" in j else {"error": j["error"]})
                for oid, j in SAVING.items()}
RETRIEVER = None

INTENT_KEYWORDS = {
    "root_cause": ["why", "root", "cause", "fail", "deviat", "issue", "fault", "diagnos"],
    "telemetry": ["telem", "trend", "pressure", "setpoint", "stiction", "vibrat", "temp", "sensor", "data"],
    "safety": ["safe", "loto", "step", "check", "inspect", "verif", "procedure", "how"],
    "work_order": ["work order", "draft", "ticket", "escalat", "maint", "history"],
}


def classify_intent(question: str) -> tuple[str, str]:
    """Route a free-text question. Decisions API `choice` when available, keywords otherwise."""
    ans = decisions_client.decide(
        input_=question,
        questions=[{
            "type": "choice", "name": "intent",
            "instructions": "A field technician asks the copilot about one plant asset. What kind of answer do they need?",
            "choices": [
                {"value": "root_cause", "description": "Why the asset is faulty or deviating; diagnosis."},
                {"value": "telemetry", "description": "Measured values, trends, alarms or data-quality flags."},
                {"value": "safety", "description": "Safe inspection steps, LOTO, how to verify in the field."},
                {"value": "work_order", "description": "Maintenance history, work order or escalation."},
                {"value": "other", "description": "General summary or anything else."},
            ],
        }],
        timeout=15,
    ) if question.strip() else None
    if ans and ans.get("intent", {}).get("choice"):
        decisions_client.save_cache()
        return ans["intent"]["choice"], "decisions_api"
    q = question.lower()
    for intent, kws in INTENT_KEYWORDS.items():
        if any(k in q for k in kws):
            return intent, "keywords"
    return "other", "keywords"


FIELD_ACTIONS = {"start_job", "complete_check", "add_note", "close_job"}


def load_data():
    global TWIN_DATA, ASSETS_BY_ID, ASSETS_BY_OBJ, RETRIEVER, FIELD
    RETRIEVER = retrieval.load_saved(try_gemma=False)
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TWIN_DATA = json.load(f)
    ASSETS_BY_ID = {a["asset_id"]: a for a in TWIN_DATA["assets"]}
    ASSETS_BY_OBJ = {a["id"]: a for a in TWIN_DATA["assets"]}
    if len(ASSETS_BY_ID) != len(TWIN_DATA["assets"]) or len(ASSETS_BY_OBJ) != len(TWIN_DATA["assets"]):
        raise ValueError("Duplicate object or asset IDs in the imported scene")
    FIELD = field_service.FieldService(TWIN_DATA, RETRIEVER)



def answer_copilot_question(asset_id: str, question: str) -> dict:
    intent, router = classify_intent(question or "")
    res = _answer(asset_id, intent)
    res["intent"], res["router"] = intent, router
    if RETRIEVER is not None and question.strip() and ASSETS_BY_ID.get(res.get("asset_id"), {}).get("mapped"):
        asset = ASSETS_BY_ID.get(res["asset_id"], {})
        hits = [h for h in RETRIEVER.search(f"{asset.get('tag', '')} {question}", k=8, scope=asset.get("asset_class"))
                if h["kind"] != "asset_card" or h["id"] == f"DOC-{res['asset_id']}"][:3]
        res["answer"] += "\n\n**Retrieved knowledge** (" + RETRIEVER.backend + "):\n" + "\n".join(
            f"- `{h['id']}` {h['title']}: {h['text'][:180]}" for h in hits)
        res["citations"] = list(dict.fromkeys(res["citations"] + [h["id"] for h in hits]))
    return res


def _answer(asset_id: str, intent: str) -> dict:
    asset = ASSETS_BY_ID.get(asset_id) or ASSETS_BY_OBJ.get(asset_id)
    if not asset:
        return {"error": "Asset not found", "asset_id": asset_id, "answer": "No such scene item exists.", "citations": []}
    if not asset.get("mapped"):
        return {"asset_id": asset["asset_id"], "tag": asset["tag"], "answer": asset["diagnostics"]["executive_summary"],
                "citations": [], "mapped": False}
    diag = asset["diagnostics"]
    tag = asset["tag"]
    aid = asset["asset_id"]
    lat = asset.get("telemetry", {}).get("latest", {})

    if intent == "root_cause":
        h0 = diag["hypotheses"][0]
        h1 = diag["hypotheses"][1] if len(diag["hypotheses"]) > 1 else None
        text = (
            f"**Root-Cause Assessment for {tag} ({aid}):**\n"
            f"- **Primary Hypothesis ({int(h0['confidence']*100)}% confidence):** {h0['hypothesis']}.\n"
            f"- **Engineering Rationale:** {h0['rationale']}\n"
        )
        if h1:
            text += f"- **Secondary Hypothesis ({int(h1['confidence']*100)}% confidence):** {h1['hypothesis']} — {h1['rationale']}\n"
        tri = diag.get("ai_triage")
        if tri:
            text += (f"- **Decisions API triage:** severity *{tri['severity']['label']}*, "
                     f"LOTO required p={tri['loto_required_p']:.2f}, data trustworthy p={tri['data_trustworthy_p']:.2f}\n")
        text += f"- **Cited Evidence IDs:** `{', '.join(h0['supporting_ids'])}`"
        return {"asset_id": aid, "tag": tag, "answer": text, "citations": h0["supporting_ids"]}

    if intent == "telemetry":
        lines = [f"**Synthetic Pack Telemetry & 3-Day Evidence for {tag} ({aid}):**"]
        for ob in diag["observations"]:
            lines.append(f"- **{ob['metric']}:** {ob['value']} (`{ob['source_id']}`)")
        if asset.get("data_quality_issues"):
            dqs = ", ".join(f"{d['issue_id']} ({d['issue_type']}, {d['severity']})" for d in asset["data_quality_issues"])
            lines.append(f"- **Data Quality Notice:** Active flags `{dqs}` reduced confidence by {int(asset['dq_penalty']*100)}%.")
        return {
            "asset_id": aid,
            "tag": tag,
            "answer": "\n".join(lines),
            "citations": [o["source_id"] for o in diag["observations"]],
        }

    if intent == "safety":
        lines = [f"**Pack-Based Field Verification Workflow for {tag} ({aid}):**"]
        cites = []
        for s in diag["verification_steps"]:
            lines.append(f"{s['step']}. **{s['title']}:** {s['detail']} (`{s['citation']}`)")
            cites.append(s["citation"])
        return {"asset_id": aid, "tag": tag, "answer": "\n".join(lines), "citations": cites}

    if intent == "work_order":
        wo = diag.get("work_order_draft")
        hist = asset.get("maintenance_history", [])
        lines = [f"**Maintenance & Work-Order Package for {tag} ({aid}):**"]
        if wo:
            lines.append(f"- **Draft ID:** `{wo['draft_id']}` | **Priority:** `{wo['priority'].upper()}`")
            lines.append(f"- **Action:** {wo['recommended_action']} ({wo['summary']})")
            lines.append(f"- **Evidence Bundle:** `{', '.join(wo['evidence_bundle'])}`")
            lines.append(f"- **Trust Note:** {wo['confidence_note']}")
        if hist:
            h0 = hist[0]
            lines.append(f"- **Last Work Order:** `{h0['work_order_id']}` on {h0['opened_at']} — *{h0['action']}* ({h0['result']}).")
        return {
            "asset_id": aid,
            "tag": tag,
            "answer": "\n".join(lines),
            "citations": wo["evidence_bundle"] if wo else [],
        }

    # General comprehensive summary
    cites = [o["source_id"] for o in diag["observations"][:3]] + [diag["hypotheses"][0]["supporting_ids"][-1]]
    answer = (
        f"**{diag['headline']}**\n\n"
        f"{diag['executive_summary']}\n\n"
        f"- **Top Hypothesis:** {diag['hypotheses'][0]['hypothesis']} (Confidence: {int(asset['effective_confidence']*100)}%)\n"
        f"- **Next Field Step:** {diag['verification_steps'][-1]['detail']}\n"
        f"- **Evidence Citations:** `{', '.join(cites)}`"
    )
    return {"asset_id": aid, "tag": tag, "answer": answer, "citations": cites}


def read_edits() -> dict:
    try:
        doc = json.loads(EDITS_PATH.read_text())
        return {"removed": list(doc.get("removed", []))}
    except (OSError, ValueError):
        return {"removed": []}


def apply_edit(body: dict) -> tuple[dict, int]:
    """Remove an object from the room or restore it. Persisted in web/data/scene_edits.json, so the
    twin stays current when staff move equipment, without re-scanning. The demo target cannot be removed."""
    action, oid = body.get("action"), str(body.get("id") or "")
    if action not in ("remove", "restore"):
        return {"error": "action must be 'remove' or 'restore'"}, 400
    asset = ASSETS_BY_OBJ.get(oid)
    if not asset:
        return {"error": f"No object {oid!r} in the twin"}, 404
    if action == "remove" and asset.get("is_target"):
        return {"error": f"{asset.get('tag', oid)} is the current inspection target and cannot be removed"}, 409
    with _EDITS_LOCK:
        doc = read_edits()
        doc["removed"] = [e for e in doc["removed"] if e.get("id") != oid]
        if action == "remove":
            doc["removed"].append({
                "id": oid, "tag": asset.get("tag"), "label": asset.get("label"), "mapped": bool(asset.get("mapped")),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "by": str(body.get("by") or "viewer")[:80],
                "note": str(body.get("note") or "")[:500],
            })
        tmp = EDITS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(EDITS_PATH)
    return {"ok": True, **doc}, 200


def library_zip(saved: list[dict]) -> bytes:
    """The saved objects as one archive: each one's scan .ply, model .ply, .glb mesh and thumbnail, plus library.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for e in saved:
            for key in ("ply", "model_ply", "glb", "thumb", "gspl", "scan_gspl", "source_rows"):
                name = e["files"].get(key)
                if name and (asset_export.LIBRARY / name).is_file():
                    z.write(asset_export.LIBRARY / name, name)
        z.writestr("library.json", json.dumps({"source": "Field Atlas asset library · Equipment Room", "saved": saved}, indent=1))
    return buf.getvalue()


class TwinRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        super().end_headers()

    def _send_json(self, obj, status=200):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/scene/edits":
            return self._send_json(read_edits())

        if path == "/api/field-log":  # the technician's job log, written by mcp_server.py
            return self._send_json(field_service.read_log())

        if path == "/api/field-jobs":  # the viewer's inspection queue: needs inspection / done, with checks and notes
            return self._send_json(FIELD.inspection_queue())

        if path == "/api/field-jobs/find":  # a spoken tag ("cv 3") to the object the viewer selects
            try:
                a = FIELD.asset(qs.get("equipment", [""])[0])
            except ValueError as err:
                return self._send_json({"error": str(err)}, 404)
            return self._send_json({"tag": a["tag"], "object_id": a["id"], "label": a.get("label"), "area": a.get("area_name")})

        if path == "/api/assets":
            mapped_only = qs.get("mapped", ["false"])[0].lower() == "true"
            items = [a for a in TWIN_DATA["assets"] if (a["mapped"] if mapped_only else True)]
            return self._send_json({"count": len(items), "assets": items})

        if path.startswith("/api/assets/"):
            parts = [p for p in path.split("/") if p]
            aid = parts[2] if len(parts) >= 3 else ""
            asset = ASSETS_BY_ID.get(aid) or ASSETS_BY_OBJ.get(aid)
            if not asset:
                return self._send_json({"error": f"Asset {aid} not found"}, 404)
            if len(parts) == 3:
                return self._send_json(asset)
            sub = parts[3]
            if sub == "telemetry":
                return self._send_json({"asset_id": asset["asset_id"], "tag": asset["tag"], "telemetry": asset["telemetry"]})
            if sub == "events":
                return self._send_json({"asset_id": asset["asset_id"], "tag": asset["tag"], "alarms": asset["alarms"]})
            if sub == "maintenance":
                return self._send_json({"asset_id": asset["asset_id"], "tag": asset["tag"], "maintenance_history": asset["maintenance_history"]})
            if sub == "diagnostics":
                return self._send_json({"asset_id": asset["asset_id"], "tag": asset["tag"], "diagnostics": asset["diagnostics"]})

        if path == "/api/navigation/route":
            target = qs.get("target", [TWIN_DATA["target_asset_id"]])[0]
            route = TWIN_DATA["navigation"]["routes_by_asset"].get(target)
            if not route:
                return self._send_json({"status": "unavailable", "validated": False, "waypoints": [],
                                       "reason": "No floor-supported route was generated for this item", "asset_id": target}, 404)
            return self._send_json(route)

        if path == "/api/work-orders":
            return self._send_json({"drafts": WORK_ORDER_DRAFTS})

        if path == "/api/library":
            return self._send_json({"saved": asset_export.read_library(), "saving": saving_status()})

        if path == "/api/library/export.zip":
            raw = library_zip(asset_export.read_library())
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="field-atlas-library.zip"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length > 0 else {}

        if parsed.path == "/api/copilot/diagnose":
            aid = body.get("asset_id") or TWIN_DATA["target_asset_id"]
            q = body.get("question") or ""
            return self._send_json(answer_copilot_question(aid, q))

        if parsed.path == "/api/work-orders/draft":
            aid = body.get("asset_id") or TWIN_DATA["target_asset_id"]
            asset = ASSETS_BY_ID.get(aid) or ASSETS_BY_OBJ.get(aid)
            if not asset:
                return self._send_json({"error": "Asset not found"}, 404)
            if not asset.get("mapped") or not asset["diagnostics"].get("work_order_draft"):
                return self._send_json({"error": "No grounded maintenance draft is available for this item"}, 422)
            draft = dict(asset["diagnostics"].get("work_order_draft") or {})
            draft["notes"] = body.get("notes", "Field verification dispatched via Autonomous Copilot.")
            draft["status"] = "DRAFT_SUBMITTED"
            WORK_ORDER_DRAFTS.append(draft)
            return self._send_json({"ok": True, "draft": draft, "total_drafts": len(WORK_ORDER_DRAFTS)})

        if parsed.path == "/api/scene/edits":
            out, status = apply_edit(body)
            return self._send_json(out, status)

        if parsed.path == "/api/field-jobs":  # the technician's own edits from the panel, same rules as the assistant
            action = body.pop("action", "")
            if action not in FIELD_ACTIONS:
                return self._send_json({"error": f"action must be one of {sorted(FIELD_ACTIONS)}"}, 400)
            try:
                field_service.call(FIELD, action, body)
            except (ValueError, TypeError) as err:
                return self._send_json({"error": str(err)}, 422)
            return self._send_json(FIELD.inspection_queue())

        if parsed.path == "/api/library":
            oid = str(body.get("id") or "")
            if oid not in ASSETS_BY_OBJ:
                return self._send_json({"error": f"No object {oid!r} in the twin"}, 404)
            with SAVING_LOCK:  # starts the save once; a second request while it runs just reports it
                if "started" not in SAVING.get(oid, {}):
                    SAVING[oid] = {"started": time.time()}
                    threading.Thread(target=_save_to_library, args=(oid,), daemon=True).start()
            return self._send_json({"ok": True, "saving": saving_status(), "saved": asset_export.read_library()}, 202)

        return self._send_json({"error": "Unknown endpoint"}, 404)

    def do_DELETE(self):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["api", "library"]:
            try:
                return self._send_json({"ok": True, "saved": asset_export.remove(parts[2])})
            except ValueError as err:
                return self._send_json({"error": str(err)}, 400)
        return self._send_json({"error": "Unknown endpoint"}, 404)


def run_server(port: int = 8765):
    mimetypes.add_type("application/octet-stream", ".bin")
    mimetypes.add_type("application/octet-stream", ".gspl")
    mimetypes.add_type("application/octet-stream", ".ply")
    mimetypes.add_type("model/gltf-binary", ".glb")
    load_data()
    server = ThreadingHTTPServer(("127.0.0.1", port), TwinRequestHandler)
    print(f"Valmet Industrial Metaverse Digital Twin server listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import sys
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run_server(p)
