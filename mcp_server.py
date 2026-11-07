"""MCP server for the voice assistant (ElevenLabs agent Vale): equipment status, procedures and the job log.

    FIELD_ATLAS_MCP_TOKEN=<secret> .venv/bin/python mcp_server.py 8767

It serves only POST /mcp (MCP Streamable HTTP, JSON responses) with the tools in pipeline/field_service.py. A
tunnel for the cloud agent points here, not at server.py, so the viewer's routes stay private. A request that
arrives through a proxy or tunnel must carry "Authorization: Bearer <FIELD_ATLAS_MCP_TOKEN>"; a direct local
request needs no token.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pipeline import config as C
from pipeline import field_service, retrieval

TOKEN = os.environ.get("FIELD_ATLAS_MCP_TOKEN", "")
INFO = {"name": "field-atlas", "version": "1.0"}
INSTRUCTIONS = ("Scan2Work equipment room. Look equipment up by tag (CV-003, P-001), read its status and procedure, "
                "and keep the technician's job: start_job, complete_check for each step (safety first), add_note, close_job.")
SERVICE: field_service.FieldService | None = None


def handle(msg: dict) -> dict | None:
    """The response to one JSON-RPC message, or None for a notification."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-03-26"),
                  "capabilities": {"tools": {}}, "serverInfo": INFO, "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": field_service.tool_list()}
    elif method == "tools/call":
        params = msg.get("params") or {}
        try:
            out, failed = field_service.call(SERVICE, params.get("name"), params.get("arguments") or {}), False
        except (ValueError, TypeError, KeyError) as err:
            out, failed = {"error": str(err)}, True
        result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": failed}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # one line per request, header names only (never values)
        if os.environ.get("FIELD_ATLAS_MCP_LOG"):
            names = ",".join(sorted(k.lower() for k in self.headers.keys())) if hasattr(self, "headers") else ""
            sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''} [{names}]\n")

    def _reply(self, status, obj=None):
        raw = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(status)
        if obj is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _allowed(self) -> bool:
        proxied = any(self.headers.get(h) for h in ("X-Forwarded-For", "Cf-Connecting-Ip", "X-Forwarded-Host", "Forwarded"))
        if not proxied and self.client_address[0] in ("127.0.0.1", "::1"):
            return True
        sent = (self.headers.get("Authorization") or "").strip()
        sent = sent[7:].strip() if sent.lower().startswith("bearer ") else sent
        return bool(TOKEN) and hmac.compare_digest(sent, TOKEN)

    def do_GET(self):
        self._reply(405 if self.path.split("?")[0] == "/mcp" else 404)  # no server-sent event stream

    def do_POST(self):
        if self.path.split("?")[0] != "/mcp":
            return self._reply(404)
        if not self._allowed():
            return self._reply(401, {"error": "unauthorized"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"null")
        except ValueError:
            return self._reply(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        replies = [r for r in map(handle, body if isinstance(body, list) else [body]) if r is not None]
        if not replies:
            return self._reply(202)
        self._reply(200, replies if isinstance(body, list) else replies[0])


def main(port: int = 8767):
    global SERVICE
    twin = json.loads((C.OUT_DIR / "digital_twin.json").read_text())
    SERVICE = field_service.FieldService(twin, retrieval.load_saved())
    print(f"Scan2Work MCP on http://127.0.0.1:{port}/mcp ({len(SERVICE.assets)} registered assets; "
          f"{'token set' if TOKEN else 'no FIELD_ATLAS_MCP_TOKEN: local requests only'})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8767)
