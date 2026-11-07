"""The Arduino fault panel: the board shows the first broken thing in the twin until its job is done.

The board knows nothing about the plant. When it starts it claims a fault: the first item of the inspection queue
(FieldService.inspection_queue, the "To inspect" list in the viewer, worst first), whatever data the server was
built from. It then sends a heartbeat every second and gets back what to show:

  fault        the job is not started            red blink; matrix: the worst signal, normal vs now
  in_progress  a job is open (tap, the panel or the voice assistant started it)   amber; matrix: checks done
  done         the job was closed as fixed, so the item left the queue            green; matrix: a tick
  clear        nothing is broken                                                  green; matrix: a tick
  waiting      (viewer only) no board has claimed a fault since the server started

A done task stays done; the board claims the newest fault nobody is working on the next time it starts (or on a
hold). With none left, a hold starts a simulated emergency: a scripted fault from the pack on a random healthy asset.
Tap = start the job (technician on site). The job is closed in the app as usual, which turns the board green.

Self-contained: server.py routes /api/live/* here; nothing else in Scan2Work imports live/.
"""
from __future__ import annotations

import json
import math
import random
import threading
import time

from .stores import snowflake, tiger
from .security import board_token, require_public_token, token_matches

BOARD_TIMEOUT_S = 5.0   # no heartbeat for this long -> the board is shown offline
EVENT_TYPE = {"claimed": "FAULT_DETECTED", "acknowledged": "ACKNOWLEDGED", "done": "RESTORED",
              "simulated_emergency": "SIMULATED_FAULT"}
# What a scripted fault carries over to the asset that plays it in a simulated emergency
SCRIPTED_FIELDS = ("health_score", "issue_code", "issue_summary", "recommended_next_check", "telemetry", "alarms",
                   "diagnostics", "event_code")
RUN_ID = time.strftime("%H%M%S")   # keeps Snowflake event ids unique across server restarts


def worst_signal(asset: dict) -> dict | None:
    """The telemetry signal furthest from its 3-day mean (in standard deviations), scaled to 0-100 % of its range,
    so the board's two bars (normal, now) work for any asset and any fault."""
    best = None
    for name, s in ((asset.get("telemetry") or {}).get("stats") or {}).items():
        lo, hi, std = s.get("min"), s.get("max"), s.get("std") or 0
        if None in (lo, hi, s.get("latest"), s.get("mean")) or hi <= lo or std <= 0:
            continue
        z = abs(s["latest"] - s["mean"]) / std
        if best is None or z > best[0]:
            best = (z, name, s, lo, hi)
    if best is None:
        return None
    z, name, s, lo, hi = best
    scale = lambda v: round(100 * (v - lo) / (hi - lo), 1)
    return {"name": name, "now": s["latest"], "normal": s["mean"], "now_pct": scale(s["latest"]),
            "normal_pct": scale(s["mean"]), "sigma": round(z, 1)}


class Board:
    def __init__(self, field):
        self.field = field   # pipeline.field_service.FieldService: the inspection queue and the jobs
        self.lock = threading.Lock()
        self.task: dict | None = None   # the claimed fault: {"tag", "asset_id", "claimed_at"}
        self.claimed = False             # claim() ran since this server started
        self.done_logged = False
        self.last_seen = 0.0
        self.touch = None
        self.events: list[dict] = []

    def log(self, kind: str, tag: str | None, by: str, detail: str = ""):
        row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": tag, "kind": kind, "by": by, "detail": detail}
        self.events.append(row)
        del self.events[:-200]
        snowflake.write_later(
            "INSERT INTO DEVICE_EVENTS (EVENT_ID, TS, TAG, EVENT_TYPE, SOURCE, ACTOR, DETAILS) "
            "SELECT %s, %s, %s, %s, 'field-atlas-board', %s, PARSE_JSON(%s)",
            (f"BOARD-{len(self.events):04d}-{RUN_ID}", row["ts"], tag, EVENT_TYPE.get(kind, kind.upper()), by,
             json.dumps({"kind": kind, "detail": detail})))

    # ------------------------------------------------------------------------------------- the queue
    def _queue(self) -> dict:
        return self.field.inspection_queue()

    def claim(self, by: str = "board", simulate_if_idle: bool = False) -> dict:
        """Take the first broken item nobody is working on as the board's task. With none left: on a hold
        (simulate_if_idle) start a simulated emergency; on start, show the first open job, or nothing."""
        rows = self._queue()["needs_inspection"]
        first = next((r for r in rows if (r.get("job") or {}).get("status") != "in_progress"), None)
        if first is None and simulate_if_idle:
            first = self.simulate_emergency(by)
        if first is None:
            first = next(iter(rows), None)
        with self.lock:
            self.task = first and {"tag": first["tag"], "asset_id": first["asset_id"],
                                   "claimed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            self.done_logged = False
            self.claimed = True
        if first:
            self.log("claimed", first["tag"], by, first.get("issue") or "")
        return self.state()

    def simulate_emergency(self, by: str) -> dict | None:
        """A new emergency for the demo: one of the pack's scripted faults (issue, urgency, readings and verification
        steps of a faulty asset, preferably of the same class) is given to a random healthy asset with no job, in
        memory only (a server restart clears it). It then shows everywhere the inspection queue does."""
        jobs = self._queue()["jobs"]
        assets = self.field.assets
        healthy = [a for a in assets if a["condition_state"] == "healthy" and a["tag"] not in jobs]
        scripted = [a for a in assets if a["condition_state"] in ("attention_required", "degraded") and not a.get("simulated_emergency")]
        if not healthy or not scripted:
            return None
        target = random.choice(healthy)
        same = [a for a in scripted if a.get("asset_class") == target.get("asset_class")]
        src = random.choice(same or scripted)
        moved = json.loads(json.dumps({k: src.get(k) for k in SCRIPTED_FIELDS if k in src}).replace(src["tag"], target["tag"]))
        if not same:   # another class's readings would not fit this device: keep its own
            moved.pop("telemetry", None)
        target.update(moved, condition_state="attention_required", urgency="high", simulated_emergency=True)
        self.log("simulated_emergency", target["tag"], by, f"{src.get('issue_code')} copied from {src['tag']}")
        return next((r for r in self._queue()["needs_inspection"] if r["tag"] == target["tag"]), None)

    def acknowledge(self, by: str = "board") -> dict:
        """Tap: the technician is on site, so the job starts (a no-op if one is already open)."""
        if self.task and self.status() == "fault":
            self.field.start_job(self.task["tag"], technician=by)
            self.log("acknowledged", self.task["tag"], by)
        return self.state()

    def status(self) -> str:
        if not self.claimed:
            return "waiting"   # no board has claimed a fault since the server started
        if not self.task:
            return "clear"
        q = self._queue()
        row = next((r for r in q["needs_inspection"] if r["tag"] == self.task["tag"]), None)
        if row is None:   # closed as fixed (or no longer broken in this data): the task is done
            return "done"
        return "in_progress" if (row.get("job") or {}).get("status") == "in_progress" else "fault"

    # ------------------------------------------------------------------------------------- the state
    def state(self) -> dict:
        status = self.status()
        task = None
        if self.task:
            if status == "done" and not self.done_logged:
                self.done_logged = True
                self.log("done", self.task["tag"], "app", "job closed, board back to normal")
            a = self.field.asset(self.task["tag"])
            job = (self._queue()["jobs"].get(self.task["tag"]) or {})
            checks = job.get("checks") or []
            task = {**self.task, "label": a.get("label"), "issue_code": a.get("issue_code"),
                    "issue": a.get("issue_summary"), "object_id": a.get("id"),
                    "signal": worst_signal(a) if status == "fault" else None,
                    "checks_done": sum(bool(c.get("done")) for c in checks), "checks_total": len(checks)}
        nxt = next(iter(self._queue()["needs_inspection"]), None)
        age = max(0.0, time.time() - self.last_seen) if self.last_seen else None
        return {"status": status, "task": task, "next_fault": nxt and nxt["tag"],
                "board_online": age is not None and age < BOARD_TIMEOUT_S, "touch": self.touch,
                "last_seen_unix": self.last_seen or None, "heartbeat_age_s": round(age, 2) if age is not None else None,
                "telemetry_source": "Valmet builder pack (synthetic)", "touch_source": "Arduino A0 input",
                "stores": {"tiger": tiger.status(), "snowflake": snowflake.status()}}

    def heartbeat(self, touch: dict | None) -> dict:
        self.last_seen = time.time()
        self.touch = touch
        if not self.claimed:   # the server restarted under a running board: claim again
            self.claim("board")
        out = self.state()
        tiger.insert_heartbeat(self.task and self.task["tag"], out["status"],
                               (touch or {}).get("swing"), (touch or {}).get("touched"))
        return out


_BOARD: Board | None = None


def handle(method: str, path: str, body: dict | None, field, token: str | None = None, *, public=False) -> tuple[dict, int] | None:
    """Route /api/live/... for server.py. Returns (json, status), or None for a path that is not ours.

      GET  /api/live/board              state (the viewer polls it)
      POST /api/live/board/claim        take the first broken item as the task (the board calls it on start)
      POST /api/live/board/heartbeat    {"touch": {...}} -> state (the board, once a second)
      POST /api/live/board/action       {"action": "ack" | "claim"}
      GET  /api/live/board/events       the panel's event log
    """
    global _BOARD
    parts = [p for p in path.split("/") if p]
    if parts[:3] != ["api", "live", "board"]:
        return None
    if field is None:
        return {"error": "the twin is not loaded"}, 503
    sub, body = parts[3] if len(parts) > 3 else "", body or {}
    # The token proves a request comes from the board (claim, heartbeat: "board online" can't be faked). The viewer's
    # actions need none: acknowledging starts a job, which /api/field-jobs allows anyway.
    try:
        secret = require_public_token() if public else board_token()
    except (OSError, ValueError):
        return {"error": "Arduino device credential is unavailable"}, 503
    if method == "POST" and sub in ("claim", "heartbeat") and secret and not token_matches(secret, token):
        return {"error": "missing or wrong X-Live-Token"}, 401
    if public:
        if len(parts) > 4 or not isinstance(body, dict):
            return {"error": "Invalid board request"}, 400
        if any(k not in {'touch', 'action', 'by'} for k in body):
            return {"error": "Unknown board field"}, 400
        if any(k in body and (not isinstance(body[k], str) or len(body[k]) > 80) for k in ('action', 'by')):
            return {"error": "Board action and actor must be short text"}, 400
        touch = body.get('touch')
        if touch is not None:
            if not isinstance(touch, dict) or any(k not in {'swing', 'base', 'touched'} for k in touch):
                return {"error": "Invalid touch reading"}, 400
            for key in ('swing', 'base'):
                value = touch.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 65535):
                    return {"error": "Touch readings must be finite ADC values"}, 400
            if 'touched' in touch and type(touch['touched']) is not bool:
                return {"error": "Touched must be a boolean"}, 400
    if _BOARD is None or _BOARD.field is not field:
        _BOARD = Board(field)
    b = _BOARD
    def response(out):
        if public and isinstance(out, dict) and 'stores' in out:
            out['stores'] = {name: {**state, 'error': 'Connection unavailable' if state.get('error') else None}
                             for name, state in out['stores'].items()}
        return out, 200
    try:
        if method == "GET" and sub == "":
            return response(b.state())
        if method == "GET" and sub == "events":
            return {"events": list(b.events)}, 200
        if method == "POST" and sub == "claim":
            return response(b.claim(str(body.get("by", "board"))))
        if method == "POST" and sub == "heartbeat":
            touch = body.get("touch") if isinstance(body.get("touch"), dict) else None
            return response(b.heartbeat(touch))
        if method == "POST" and sub == "action":
            action, by = str(body.get("action", "")), str(body.get("by", "viewer"))
            if action == "ack":
                return response(b.acknowledge(by))
            if action == "claim":   # a hold, or the card's Show newest: with nothing left, a simulated emergency
                return response(b.claim(by, simulate_if_idle=True))
            return {"error": "action must be ack or claim"}, 400
    except ValueError as err:   # e.g. the claimed tag is not in this twin's data
        return {"error": str(err)}, 422
    return {"error": "unknown live endpoint"}, 404
