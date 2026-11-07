"""HTTP & REST API server for the Valmet Industrial Metaverse Digital Twin.

Serves the interactive 3D WebGL viewer at / and implements the Valmet Excel Pack
API_Contract routes plus interactive AI Copilot Q&A and Work Order drafting.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import threading
import time
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
# Keep upstream defaults for normal Wael scenes. Imported/offline manifests must
# be recognized before config imports, which may otherwise load credentials.
from pipeline.runtime_policy import configure_server_runtime
configure_server_runtime(ROOT)
from pipeline import asset_export, decisions_client, field_service, retrieval, config as C
from pipeline.asset_records import asset_for_api, route_for_api, UNMAPPED_NOTICE
from pipeline.scene_context import build_scene_context

WEB_DIR = ROOT / "web"
PUBLIC_DEMO = os.environ.get("VALMET_PUBLIC_DEMO") == "1"
# Opt-in for the hosted demo: show the voice assistant and keep a shared inspection job log (field jobs only;
# scene edits and library writes stay read-only). mcp_server.py on the same host writes the same log.
PUBLIC_ASSISTANT = PUBLIC_DEMO and os.environ.get("VALMET_PUBLIC_ASSISTANT") == "1"
PUBLIC_ARDUINO = PUBLIC_DEMO and os.environ.get("VALMET_PUBLIC_ARDUINO") == "1"
PUBLIC_BOARD_GETS = {'/api/live/board', '/api/live/board/events'}
PUBLIC_BOARD_POSTS = {'/api/live/board/claim', '/api/live/board/heartbeat', '/api/live/board/action'}
PUBLIC_FIELD_TEXT_LIMIT = 1000   # characters per note, step result or summary sent from the public site
PUBLIC_BODY_LIMIT = 16 * 1024
_PUBLIC_WORK_SLOTS = threading.BoundedSemaphore(2)
DATA_PATH = C.OUT_DIR / "digital_twin.json"
EDITS_PATH = C.OUT_DIR / "scene_edits.json"   # isolated by the active dataset directory
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


PreviewFieldService = field_service.ReadOnlyFieldService


def public_install_url():
    value = os.environ.get("VALMET_INSTALL_URL", "").strip()
    url = urlparse(value)
    return value if url.scheme in {"http", "https"} and url.netloc and not url.username and not url.password else None


def served_manifest():
    """Runtime settings are a response projection, never a dataset rewrite."""
    manifest = dict(TWIN_DATA)
    if 'VALMET_PROPOSAL_VIEW_URL' in os.environ and isinstance(manifest.get('proposal_review'), dict):
        manifest['proposal_review'] = {**manifest['proposal_review'],
            'full_view_url': os.environ['VALMET_PROPOSAL_VIEW_URL'] or None}
    if PUBLIC_DEMO:
        manifest['runtime'] = {**(manifest.get('runtime') or {}), 'public_demo': True,
            'offline': True, 'external_models_enabled': False, 'shared_writes_enabled': False,
            'voice_assistant': PUBLIC_ASSISTANT, 'field_jobs_enabled': PUBLIC_ASSISTANT,
            'arduino_enabled': PUBLIC_ARDUINO, 'arduino_actions_enabled': PUBLIC_ARDUINO and PUBLIC_ASSISTANT,
            'install_url': public_install_url()}
    # With --assistant and no explicit agent override, preserve the upstream
    # Ilkka opt-in. An explicit (even empty) agent setting remains authoritative.
    if PUBLIC_ASSISTANT and 'VALMET_VOICE_AGENT_ID' not in os.environ:
        manifest['runtime'] = {**(manifest.get('runtime') or {})}
        manifest['runtime'].pop('voice', None)
    elif PUBLIC_DEMO or 'VALMET_VOICE_AGENT_ID' in os.environ:
        agent_id = os.environ.get('VALMET_VOICE_AGENT_ID', '').strip()
        enabled = bool(re.fullmatch(r'agent_[A-Za-z0-9_-]{1,100}', agent_id))
        mcp_url = os.environ.get('VALMET_VOICE_MCP_URL', '').strip()
        parsed = urlparse(mcp_url)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
            mcp_url = None
        manifest['runtime'] = {**(manifest.get('runtime') or {}), 'voice': {
            'enabled': enabled, 'provider': 'elevenlabs', 'agent_id': agent_id if enabled else None,
            'agent_name': os.environ.get('VALMET_VOICE_AGENT_NAME', 'Ilkka').strip()[:80] or 'Ilkka',
            'service_mode': 'shared_jobs' if PUBLIC_ASSISTANT else 'read_only', 'mcp_url': mcp_url}}
    return manifest

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
    ) if question.strip() and not PUBLIC_DEMO else None
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
    RETRIEVER = retrieval.load_saved(try_gemma=not (C.OFFLINE or PUBLIC_DEMO))
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TWIN_DATA = json.load(f)
    ASSETS_BY_ID = {a["asset_id"]: a for a in TWIN_DATA["assets"]}
    ASSETS_BY_OBJ = {a["id"]: a for a in TWIN_DATA["assets"]}
    if len(ASSETS_BY_ID) != len(TWIN_DATA["assets"]) or len(ASSETS_BY_OBJ) != len(TWIN_DATA["assets"]):
        raise ValueError("Duplicate object or asset IDs in the imported scene")
    FIELD = (PreviewFieldService if PUBLIC_DEMO and not PUBLIC_ASSISTANT else field_service.FieldService)(TWIN_DATA, RETRIEVER)



def answer_copilot_question(asset_id: str, question: str) -> dict:
    asset = ASSETS_BY_ID.get(asset_id) or ASSETS_BY_OBJ.get(asset_id)
    if asset and not asset.get("mapped"):
        return {**_answer(asset_id, "other"), "intent": "other", "router": "unmapped"}
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
        return {"asset_id": asset["asset_id"], "tag": asset["id"], "answer": UNMAPPED_NOTICE,
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
        z.writestr("library.json", json.dumps({"source": "Scan2Work asset library · Equipment Room", "saved": saved}, indent=1))
    return buf.getvalue()


class TwinRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def translate_path(self, path):
        # Let the standard handler normalize URL traversal first. Only the /data
        # subtree is redirected; code and UI still come from this checkout.
        translated = Path(super().translate_path(path))
        try:
            relative = translated.relative_to(WEB_DIR / 'data')
        except ValueError:
            return str(translated)
        return str(C.OUT_DIR / relative)

    def log_message(self, fmt, *args):
        pass

    def setup(self):
        super().setup()
        if PUBLIC_DEMO:
            self.connection.settimeout(15)

    def list_directory(self, path):
        if PUBLIC_DEMO:
            self.send_error(403, "Directory listings are unavailable in the public demo")
            return None
        return super().list_directory(path)

    def _public_static_allowed(self):
        """Do not publish shared operator state, hidden files or symlink escapes."""
        file = Path(self.translate_path(self.path)).resolve()
        roots = (WEB_DIR.resolve(), C.OUT_DIR.resolve())
        root = next((root for root in roots if file == root or root in file.parents), None)
        if root is None:
            return False
        relative = file.relative_to(root)
        return not any(part.startswith('.') for part in relative.parts) and not (
            file.name in {'field_log.json', 'scene_edits.json', 'decisions_cache.json', 'secrets.env'}
            or file.name.endswith(('.lock', '.tmp', '.env')))

    def send_head(self):
        if PUBLIC_DEMO:
            if not self._public_static_allowed():
                self.send_error(404, "File unavailable in the public demo")
                return None
            if Path(self.translate_path(self.path)).resolve() == DATA_PATH.resolve():
                raw = json.dumps(served_manifest()).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                return io.BytesIO(raw)
        return super().send_head()

    def _public_read_only(self):
        self.close_connection = True
        return self._send_json({'error': 'This hosted demo does not save shared changes. Install the demo to save jobs and library objects.',
            'code': 'public_demo_read_only', 'persisted': False, 'install_url': public_install_url()}, 403)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        super().end_headers()

    def _send_media(self, file_path: Path):
        """Serve a video with HTTP byte ranges (206), which browsers need to stream and seek it. Safari will not
        play a large MP4 without them, and the AR walkthrough is 64 MB."""
        size = file_path.stat().st_size
        start, end, status = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
            else:  # suffix range: the last N bytes
                start = max(0, size - int(m[2]))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with file_path.open("rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):  # the browser stopped this range to ask for another
            pass

    def _send_json(self, obj, status=200):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _live(self, method: str, path: str, body: dict):
        """The Arduino fault panel (with Tiger Data and Snowflake): all of it lives in live/, imported on first use."""
        if PUBLIC_DEMO and not PUBLIC_ARDUINO:
            # Even a state GET can log a completed board task. Do not import the
            # optional store module (or discover its credentials) in this mode.
            return self._public_read_only()
        if PUBLIC_DEMO:
            allowed = PUBLIC_BOARD_GETS if method == 'GET' else PUBLIC_BOARD_POSTS
            if path not in allowed:
                return self._send_json({'error': 'Unknown board endpoint'}, 404)
            if path == '/api/live/board/action' and not PUBLIC_ASSISTANT:
                return self._public_read_only()
            from live.security import require_public_token
            try:
                require_public_token()
            except ValueError as error:
                return self._send_json({'error': str(error)}, 503)
        try:
            from live import board as live_board
        except Exception as err:  # noqa: BLE001 - the panel is optional; the twin works without it
            return self._send_json({"error": f"fault panel unavailable: {err}"}, 503)
        out = live_board.handle(method, path, body, FIELD, (self.headers.get("X-Live-Token") or "").strip(),
                                **({'public': True} if PUBLIC_DEMO else {}))
        return self._send_json(*(out or ({"error": "Unknown endpoint"}, 404)))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/live/"):
            return self._live("GET", path, {})
        qs = parse_qs(parsed.query)

        if path == '/data/digital_twin.json' and (PUBLIC_DEMO or 'VALMET_PROPOSAL_VIEW_URL' in os.environ or 'VALMET_VOICE_AGENT_ID' in os.environ):
            return self._send_json(served_manifest())

        if path == "/api/scene/edits":
            return self._send_json({'removed': []} if PUBLIC_DEMO else read_edits())

        if path == "/api/scene-context":
            return self._send_json(build_scene_context(TWIN_DATA))

        if path.startswith("/data/") and path.lower().endswith((".mp4", ".webm")):
            if PUBLIC_DEMO and not self._public_static_allowed():
                return self.send_error(404, "Media unavailable in the public demo")
            media = Path(self.translate_path(path)).resolve()
            if C.OUT_DIR.resolve() in media.parents and media.is_file():
                return self._send_media(media)
            return self.send_error(404, "Media not found in the active dataset")

        if path == "/api/field-log":  # the technician's job log, written by mcp_server.py
            return self._send_json({'jobs': {}} if PUBLIC_DEMO and not PUBLIC_ASSISTANT else field_service.read_log())

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
            items = [asset_for_api(a) for a in TWIN_DATA["assets"] if (a["mapped"] if mapped_only else True)]
            return self._send_json({"count": len(items), "assets": items})

        if path.startswith("/api/assets/"):
            parts = [p for p in path.split("/") if p]
            aid = parts[2] if len(parts) >= 3 else ""
            asset = ASSETS_BY_ID.get(aid) or ASSETS_BY_OBJ.get(aid)
            if not asset:
                return self._send_json({"error": f"Asset {aid} not found"}, 404)
            asset = asset_for_api(asset)
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
            return self._send_json(route_for_api(route))

        if path == "/api/work-orders":
            return self._send_json({"drafts": [] if PUBLIC_DEMO else WORK_ORDER_DRAFTS})

        if path == "/api/library":
            return self._send_json({"saved": asset_export.read_library(), "saving": {} if PUBLIC_DEMO else saving_status()})

        if path == "/api/library/export.zip":
            if PUBLIC_DEMO and not _PUBLIC_WORK_SLOTS.acquire(blocking=False):
                return self._send_json({'error': 'The demo is busy; please try again shortly.'}, 429)
            try:
                raw = library_zip(asset_export.read_library())
            finally:
                if PUBLIC_DEMO:
                    _PUBLIC_WORK_SLOTS.release()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="scan2work-library.zip"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        public_posts = {'/api/copilot/diagnose', '/api/work-orders/draft'} | ({'/api/field-jobs'} if PUBLIC_ASSISTANT else set())
        if PUBLIC_ARDUINO:
            public_posts |= PUBLIC_BOARD_POSTS
        if PUBLIC_DEMO and parsed.path not in public_posts:
            return self._public_read_only()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid body length')
        except ValueError:
            self.close_connection = True
            return self._send_json({'error': 'A valid Content-Length is required'}, 400)
        if length > (PUBLIC_BODY_LIMIT if PUBLIC_DEMO else 1024 * 1024):
            self.close_connection = True
            return self._send_json({'error': 'Request body is too large'}, 413)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError('Expected JSON object')
            for key in ('asset_id', 'question', 'notes'):
                if key in body and body[key] is not None and not isinstance(body[key], str):
                    raise ValueError('Text fields must be strings')
            if PUBLIC_DEMO and (len(body.get('question') or '') > 2000 or len(body.get('notes') or '') > 4000):
                raise ValueError('Question or notes exceed the demo limit')
        except (ValueError, UnicodeError):
            return self._send_json({'error': 'Expected a JSON object with bounded text fields'}, 400)

        if parsed.path.startswith("/api/live/"):
            return self._live("POST", parsed.path, body)

        if parsed.path == "/api/copilot/diagnose":
            aid = body.get("asset_id") or TWIN_DATA["target_asset_id"]
            q = body.get("question") or ""
            if PUBLIC_DEMO and not _PUBLIC_WORK_SLOTS.acquire(blocking=False):
                return self._send_json({'error': 'The demo is busy; please try again shortly.'}, 429)
            try:
                answer = answer_copilot_question(aid, q)
            finally:
                if PUBLIC_DEMO:
                    _PUBLIC_WORK_SLOTS.release()
            return self._send_json(answer)

        if parsed.path == "/api/work-orders/draft":
            aid = body.get("asset_id") or TWIN_DATA["target_asset_id"]
            asset = ASSETS_BY_ID.get(aid) or ASSETS_BY_OBJ.get(aid)
            if not asset:
                return self._send_json({"error": "Asset not found"}, 404)
            if not asset.get("mapped") or not asset["diagnostics"].get("work_order_draft"):
                return self._send_json({"error": "No grounded maintenance draft is available for this item"}, 422)
            draft = dict(asset["diagnostics"].get("work_order_draft") or {})
            draft["notes"] = body.get("notes", "Field verification dispatched via Autonomous Copilot.")
            if PUBLIC_DEMO:
                draft['status'] = 'DEMO_DRAFT'
                return self._send_json({'ok': True, 'draft': draft, 'persisted': False,
                                       'public_demo': True, 'total_drafts': 0})
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
            if PUBLIC_DEMO and any(len(str(v)) > PUBLIC_FIELD_TEXT_LIMIT for v in body.values()):
                return self._send_json({"error": f"Keep notes and results under {PUBLIC_FIELD_TEXT_LIMIT} characters."}, 413)
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
        if PUBLIC_DEMO:
            return self._public_read_only()
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["api", "library"]:
            try:
                return self._send_json({"ok": True, "saved": asset_export.remove(parts[2])})
            except ValueError as err:
                return self._send_json({"error": str(err)}, 400)
        return self._send_json({"error": "Unknown endpoint"}, 404)


def run_server(port: int = 8765, host: str = "127.0.0.1"):
    if PUBLIC_ARDUINO:
        from live.security import require_public_token
        require_public_token()
    mimetypes.add_type("application/octet-stream", ".bin")
    mimetypes.add_type("application/octet-stream", ".gspl")
    mimetypes.add_type("application/octet-stream", ".ply")
    mimetypes.add_type("model/gltf-binary", ".glb")
    load_data()
    # --lan retains upstream's Arduino workflow. The installed launcher can
    # override it; the explicitly public preview always sits behind its proxy.
    host = '127.0.0.1' if PUBLIC_DEMO else os.environ.get('VALMET_BIND_HOST', host)
    server = ThreadingHTTPServer((host, port), TwinRequestHandler)
    print(f"Valmet Industrial Metaverse Digital Twin server listening on http://{host}:{server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if a != "--lan"]
    p = int(args[0]) if args else 8765
    # --lan: listen on every interface so an Arduino on the same network can reach the server (arduino/README.md)
    run_server(p, "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1")
