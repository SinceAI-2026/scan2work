"""HTTP & REST API server for the Valmet Industrial Metaverse Digital Twin.

Serves the interactive 3D WebGL viewer at / and implements the Valmet Excel Pack
API_Contract routes plus interactive AI Copilot Q&A and Work Order drafting.
"""
from __future__ import annotations

import json
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pipeline import decisions_client, retrieval

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_PATH = WEB_DIR / "data" / "digital_twin.json"

TWIN_DATA: dict = {}
ASSETS_BY_ID: dict = {}
ASSETS_BY_OBJ: dict = {}
WORK_ORDER_DRAFTS: list = []
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


def load_data():
    global TWIN_DATA, ASSETS_BY_ID, ASSETS_BY_OBJ, RETRIEVER
    RETRIEVER = retrieval.load_saved()
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TWIN_DATA = json.load(f)
    ASSETS_BY_ID = {a["asset_id"]: a for a in TWIN_DATA["assets"]}
    ASSETS_BY_OBJ = {a["id"]: a for a in TWIN_DATA["assets"]}


def answer_copilot_question(asset_id: str, question: str) -> dict:
    intent, router = classify_intent(question or "")
    res = _answer(asset_id, intent)
    res["intent"], res["router"] = intent, router
    if RETRIEVER is not None and question.strip():
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
        asset = ASSETS_BY_ID.get(TWIN_DATA["target_asset_id"])
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
        lines = [f"**Live Telemetry & 3-Day Evidence for {tag} ({aid}):**"]
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
        lines = [f"**Approved Field Verification Workflow for {tag} ({aid}):**"]
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
                return self._send_json(TWIN_DATA["navigation"]["primary_route"])
            return self._send_json(route)

        if path == "/api/work-orders":
            return self._send_json({"drafts": WORK_ORDER_DRAFTS})

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
            draft = dict(asset["diagnostics"].get("work_order_draft") or {})
            draft["notes"] = body.get("notes", "Field verification dispatched via Autonomous Copilot.")
            draft["status"] = "DRAFT_SUBMITTED"
            WORK_ORDER_DRAFTS.append(draft)
            return self._send_json({"ok": True, "draft": draft, "total_drafts": len(WORK_ORDER_DRAFTS)})

        return self._send_json({"error": "Unknown endpoint"}, 404)


def run_server(port: int = 8765):
    mimetypes.add_type("application/octet-stream", ".bin")
    load_data()
    server = ThreadingHTTPServer(("127.0.0.1", port), TwinRequestHandler)
    print(f"Valmet Industrial Metaverse Digital Twin server listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import sys
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run_server(p)
