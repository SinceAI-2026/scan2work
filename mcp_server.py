"""MCP server for the voice assistant (ElevenLabs agent Vale): equipment status, procedures and the job log.

    FIELD_ATLAS_MCP_TOKEN=<secret> .venv/bin/python mcp_server.py 8767

It serves only POST /mcp (MCP Streamable HTTP, JSON responses) with the tools in pipeline/field_service.py. A
tunnel for the cloud agent points here, not at server.py, so the viewer's routes stay private. A request that
arrives through a proxy or tunnel must carry "Authorization: Bearer <FIELD_ATLAS_MCP_TOKEN>"; a direct local
request needs no token in the installed mode. FIELD_ATLAS_MCP_PUBLIC=1 instead
requires bearer authentication on every request and defaults to six reading
tools. VALMET_PUBLIC_ASSISTANT=1 explicitly enables the shared job log and all
ten tools. FIELD_ATLAS_MCP_TOKEN_FILE can supply a private token for deployment.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pipeline.runtime_policy import configure_server_runtime
configure_server_runtime(Path(__file__).resolve().parent)
from pipeline import config as C
from pipeline import field_service, retrieval

PUBLIC_MCP = os.environ.get('FIELD_ATLAS_MCP_PUBLIC') == '1' or os.environ.get('VALMET_PUBLIC_DEMO') == '1'
PUBLIC_ASSISTANT = os.environ.get('VALMET_PUBLIC_ASSISTANT') == '1'
TOKEN = (Path(os.environ['FIELD_ATLAS_MCP_TOKEN_FILE']).read_text().strip()
         if os.environ.get('FIELD_ATLAS_MCP_TOKEN_FILE') else os.environ.get("FIELD_ATLAS_MCP_TOKEN", ""))
MAX_PUBLIC_BODY = 16 * 1024
MAX_PUBLIC_BATCH = 8
MAX_PUBLIC_WRITE_TEXT = 1000
_PUBLIC_SLOTS = threading.BoundedSemaphore(4)
INFO = {"name": "scan2work" if PUBLIC_MCP else "field-atlas", "version": "1.0"}
INSTRUCTIONS = ("Scan2Work equipment room. Look equipment up by tag (CV-003, P-001), read its status and procedure, "
                "and keep the technician's job: start_job, complete_check for each step (safety first), add_note, close_job.")
SERVICE: field_service.FieldService | None = None
READ_INSTRUCTIONS = ("Scan2Work equipment room, read-only public demonstration. Operating data is synthetic and physical "
                     "scale is unverified. Use the six reading tools to find equipment, explain readings and cite service "
                     "procedures. No jobs, visitor notes or real repairs are saved. Never claim to have performed an "
                     "inspection, acknowledged an alarm, or marked equipment fixed.")
SHARED_INSTRUCTIONS = (INSTRUCTIONS + " This is an explicitly enabled shared demonstration job log: notes and checks "
                       "are visible to other visitors. Operating data is synthetic. Record only the technician's "
                       "reported observations; never claim you performed a physical inspection or repair.")


def shared_jobs_enabled():
    return PUBLIC_MCP and PUBLIC_ASSISTANT


def valid_message(msg):
    return (isinstance(msg, dict) and msg.get('jsonrpc') == '2.0'
            and isinstance(msg.get('method'), str) and bool(msg['method'])
            and isinstance(msg.get('params', {}), dict)
            and ('id' not in msg or msg['id'] is None or type(msg['id']) in (str, int)))


def handle(msg: dict) -> dict | None:
    """The response to one JSON-RPC message, or None for a notification."""
    if not valid_message(msg):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-03-26"),
                  "capabilities": {"tools": {}}, "serverInfo": INFO,
                  "instructions": (SHARED_INSTRUCTIONS if shared_jobs_enabled() else
                                   READ_INSTRUCTIONS if PUBLIC_MCP else INSTRUCTIONS)}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [tool for tool in field_service.tool_list()
                            if not PUBLIC_MCP or shared_jobs_enabled() or tool['name'] in field_service.READ_ONLY]}
    elif method == "tools/call":
        params = msg.get("params") or {}
        try:
            if PUBLIC_MCP and not shared_jobs_enabled() and params.get('name') not in field_service.READ_ONLY:
                raise ValueError('This public MCP endpoint exposes reading tools only; no shared jobs or notes are written.')
            if not isinstance(params.get('arguments', {}), dict):
                raise ValueError('Tool arguments must be a JSON object')
            limit = MAX_PUBLIC_WRITE_TEXT if params.get('name') not in field_service.READ_ONLY else 2000
            if PUBLIC_MCP and any(not isinstance(v, str) or len(v) > limit for v in (params.get('arguments') or {}).values()):
                raise ValueError(f'Tool arguments must be text of at most {limit} characters')
            out, failed = field_service.call(SERVICE, params.get("name"), params.get("arguments") or {}), False
        except (ValueError, TypeError, KeyError) as err:
            out, failed = {"error": str(err)}, True
        result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": failed}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        if PUBLIC_MCP:
            self.connection.settimeout(10)

    def log_message(self, fmt, *args):  # one line per request, header names only (never values)
        if os.environ.get("FIELD_ATLAS_MCP_LOG"):
            names = ",".join(sorted(k.lower() for k in self.headers.keys())) if hasattr(self, "headers") else ""
            sys.stderr.write(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''} [{names}]\n")

    def _reply(self, status, obj=None):
        raw = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(status)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        if status == 401:
            self.send_header('WWW-Authenticate', 'Bearer realm="scan2work-mcp"')
        self.send_header('Cache-Control', 'no-store')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self) -> bool:
        if PUBLIC_MCP:
            sent = (self.headers.get('Authorization') or '').strip()
            return bool(TOKEN) and sent.lower().startswith('bearer ') and hmac.compare_digest(sent[7:].strip().encode(), TOKEN.encode())
        proxied = any(self.headers.get(h) for h in ("X-Forwarded-For", "Cf-Connecting-Ip", "X-Forwarded-Host", "Forwarded"))
        if not proxied and self.client_address[0] in ("127.0.0.1", "::1"):
            return True
        sent = (self.headers.get("Authorization") or "").strip()
        sent = sent[7:].strip() if sent.lower().startswith("bearer ") else sent
        return bool(TOKEN) and hmac.compare_digest(sent.encode(), TOKEN.encode())

    def do_GET(self):
        self._reply(405 if self.path.split("?")[0] == "/mcp" else 404)  # no server-sent event stream

    def do_POST(self):
        if self.path.split("?")[0] != "/mcp":
            return self._reply(404)
        if not self._allowed():
            return self._reply(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid length')
        except ValueError:
            self.close_connection = True
            return self._reply(400, {'error': 'Invalid Content-Length'})
        if length > (MAX_PUBLIC_BODY if PUBLIC_MCP else 1024 * 1024):
            self.close_connection = True
            return self._reply(413, {'error': 'Request body too large'})
        try:
            body = json.loads(self.rfile.read(length) or b'null')
        except TimeoutError:
            self.close_connection = True
            return self._reply(408, {'error': 'Request body timed out'})
        except (ValueError, UnicodeError):
            return self._reply(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        batch = body if isinstance(body, list) else [body]
        if not batch or any(not valid_message(msg) for msg in batch):
            return self._reply(400, {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid Request'}})
        if len(batch) > (MAX_PUBLIC_BATCH if PUBLIC_MCP else 64):
            return self._reply(413, {'error': 'Too many requests in one batch'})
        if PUBLIC_MCP and not _PUBLIC_SLOTS.acquire(blocking=False):
            return self._reply(429, {'error': 'MCP is busy; retry shortly'})
        try:
            deadline = time.monotonic() + 5
            replies = []
            for message in batch:
                if PUBLIC_MCP and time.monotonic() > deadline:
                    return self._reply(429, {'error': 'MCP batch exceeded its time budget'})
                reply = handle(message)
                if reply is not None:
                    replies.append(reply)
        finally:
            if PUBLIC_MCP:
                _PUBLIC_SLOTS.release()
        if not replies:
            return self._reply(202)
        self._reply(200, replies if isinstance(body, list) else replies[0])


def main(port: int = 8767):
    global SERVICE
    if PUBLIC_MCP and (len(TOKEN) < 32 or any(c.isspace() for c in TOKEN)):
        raise ValueError('Public MCP requires a random bearer token of at least 32 characters')
    twin = json.loads((C.OUT_DIR / "digital_twin.json").read_text())
    service_type = field_service.ReadOnlyFieldService if PUBLIC_MCP and not shared_jobs_enabled() else field_service.FieldService
    SERVICE = service_type(twin, retrieval.load_saved(try_gemma=not (C.OFFLINE or PUBLIC_MCP)))
    http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Scan2Work MCP on http://127.0.0.1:{http.server_address[1]}/mcp ({len(SERVICE.assets)} registered assets; "
          f"{'token set' if TOKEN else 'no FIELD_ATLAS_MCP_TOKEN: local requests only'})", flush=True)
    http.serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8767)
