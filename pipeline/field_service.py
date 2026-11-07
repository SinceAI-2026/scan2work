"""Field-service jobs for the voice assistant (Vale): what is wrong, what to check, and what the technician did.

mcp_server.py exposes TOOLS over MCP; server.py serves the inspection queue to the viewer at /api/field-jobs and
lets the technician add notes and tick steps there too (both processes share the log under a file lock). Every status, reading and procedure comes from digital_twin.json
(the Excel builder pack bound to the scene); the job log is the only thing written, to web/data/field_log.json:

    {"jobs": {"ASSET-003": {"tag", "issue_code", "status", "opened_at", "technician",
                            "checks": [{"id": "1", "title", "detail", "citation", "safety", "done", "result", "at"}],
                            "notes": [{"at", "by", "kind", "text"}], "outcome", "summary", "closed_at"}}}

A job's checklist is the asset's grounded verification steps. A safety step (site safety, lockout/tagout) must be
confirmed before any step after it in the procedure; steps before it, such as confirming the equipment's identity,
need no permit. A job closes as fixed only when every step is confirmed.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import re
import threading
import time

from . import config as C

LOG_PATH = C.OUT_DIR / "field_log.json"
NOTE_KINDS = ("observation", "measurement", "fix", "escalation")
OUTCOMES = ("fixed", "not_fixed", "escalated")
_thread_lock = threading.Lock()


@contextlib.contextmanager
def _locked():
    """One writer at a time across threads and across processes (mcp_server.py and server.py)."""
    with _thread_lock, open(LOG_PATH.with_name(LOG_PATH.name + ".lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def read_log() -> dict:
    try:
        return json.loads(LOG_PATH.read_text())
    except (OSError, ValueError):
        return {"jobs": {}}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _key(text: str) -> str:
    """CV-003, cv 3 and CV003 all become CV3; ASSET-003 becomes ASSET3."""
    m = re.fullmatch(r"([A-Za-z]+)[\s_-]*0*(\d+)", text.strip())
    return f"{m[1].upper()}{m[2]}" if m else text.strip().upper()


class FieldService:
    def __init__(self, twin: dict, retriever=None):
        self.assets = [a for a in twin["assets"] if a.get("mapped")]
        self.by_key = {}
        for a in self.assets:
            for name in (a["tag"], a["asset_id"], a["id"]):
                self.by_key[_key(name)] = a
        self.retriever = retriever

    # ------------------------------------------------------------ reading the plant
    def asset(self, equipment: str) -> dict:
        a = self.by_key.get(_key(equipment))
        if a is None:
            raise ValueError(f"No registered equipment {equipment!r}. Use find_equipment to look it up by name.")
        return a

    def find_equipment(self, query: str) -> dict:
        words = query.lower().split()
        hits = [a for a in self.assets
                if all(w in " ".join([a["tag"], a["asset_id"], a["asset_class"], a.get("label", ""), a.get("area_name", "")]).lower()
                       for w in words)]
        return {"matches": [self._brief(a) for a in hits[:8]], "total": len(hits)}

    def _brief(self, a: dict) -> dict:
        return {"tag": a["tag"], "asset_id": a["asset_id"], "type": a["asset_class"].replace("_", " ").lower(),
                "area": a.get("area_name"), "state": a["condition_state"], "health": a.get("health_score"),
                "issue": a.get("issue_summary") if a["condition_state"] != "healthy" else None}

    def get_equipment_status(self, equipment: str) -> dict:
        a = self.asset(equipment)
        tele = a.get("telemetry") or {}
        stats = tele.get("stats") or {}
        readings = {k: {"now": v.get("latest"), "3_day_mean": v.get("mean"), "3_day_change": v.get("delta_3d")}
                    for k, v in stats.items()}
        alarms = [{k: e[k] for k in ("event_id", "timestamp", "severity", "event_code", "status")}
                  for e in a.get("alarms", []) if e.get("status") != "closed"][:5]
        last_wo = (a.get("maintenance_history") or [None])[0]
        return {**self._brief(a), "criticality": a.get("criticality"), "urgency": a.get("urgency"),
                "issue_code": a.get("issue_code"), "recommended_next_check": a.get("recommended_next_check"),
                "readings": readings, "open_alarms": alarms,
                "last_work_order": last_wo and {k: last_wo[k] for k in ("work_order_id", "opened_at", "action", "result")},
                "job": self._job_summary(a["asset_id"]), "source": "Valmet builder pack (synthetic)"}

    def _unhealthy(self) -> list[dict]:
        order = {"attention_required": 0, "degraded": 1, "watch": 2}
        return sorted((a for a in self.assets if a["condition_state"] in order),
                      key=lambda a: (order[a["condition_state"]], a.get("health_score") or 100))

    def list_open_problems(self) -> dict:
        """Equipment that still needs inspection, worst first. A job closed as fixed moves it to done."""
        jobs = self._read()["jobs"]
        rows = [{**self._brief(a), "urgency": a.get("urgency"), "target": bool(a.get("is_target")),
                 "job": self._job_summary(a["asset_id"])} for a in self._unhealthy()]
        fixed = lambda r: (jobs.get(r["asset_id"]) or {}).get("status") == "fixed"
        return {"problems": [r for r in rows if not fixed(r)], "done": [r["tag"] for r in rows if fixed(r)]}

    def inspection_queue(self) -> dict:
        """The viewer's panel: what needs inspection and what is done, each with its full job (checks and notes)."""
        jobs = self._read()["jobs"]
        rows = [{**self._brief(a), "object_id": a["id"], "label": a.get("label"), "job": jobs.get(a["asset_id"])}
                for a in self._unhealthy()]
        done = [r for r in rows if (r["job"] or {}).get("status") == "fixed"]
        return {"needs_inspection": [r for r in rows if r not in done], "done": done,
                "jobs": {j["tag"]: j for j in jobs.values()}}

    def get_procedure(self, equipment: str) -> dict:
        a = self.asset(equipment)
        d = a.get("diagnostics") or {}
        triage = d.get("ai_triage") or {}
        doc = a.get("knowledge_doc") or {}
        return {"tag": a["tag"], "summary": d.get("executive_summary"),
                "likely_causes": [{"cause": h["hypothesis"], "confidence": h.get("confidence"), "why": h.get("rationale")}
                                  for h in d.get("hypotheses", [])[:3]],
                "severity": (triage.get("severity") or {}).get("label"),
                "lockout_tagout_probability": triage.get("loto_required_p"),
                "data_trust_probability": triage.get("data_trustworthy_p"),
                "steps": [{"id": str(s["step"]), "title": s["title"], "detail": s["detail"], "citation": s.get("citation")}
                          for s in d.get("verification_steps", [])],
                "knowledge_doc": {"id": doc.get("doc_id"), "title": doc.get("title"), "content": doc.get("content")}}

    def search_service_knowledge(self, query: str, equipment: str = "") -> dict:
        if self.retriever is None:
            return {"results": [], "note": "The knowledge index is not built. Run the pipeline first."}
        a = self.asset(equipment) if equipment else None
        hits = self.retriever.search(f"{a['tag'] if a else ''} {query}".strip(), k=4, scope=a and a["asset_class"])
        return {"results": [{"id": h["id"], "title": h["title"], "text": h["text"][:600]} for h in hits]}

    # ------------------------------------------------------------ the job log
    def _read(self) -> dict:
        return read_log()

    def _write(self, log: dict) -> None:
        tmp = LOG_PATH.with_name(LOG_PATH.name + ".tmp")
        tmp.write_text(json.dumps(log, indent=1))
        tmp.replace(LOG_PATH)

    def _job_summary(self, asset_id: str):
        job = self._read()["jobs"].get(asset_id)
        if not job:
            return None
        return {"status": job["status"], "checks_done": sum(c["done"] for c in job["checks"]), "checks_total": len(job["checks"])}

    def get_job(self, equipment: str) -> dict:
        a = self.asset(equipment)
        job = self._read()["jobs"].get(a["asset_id"])
        if not job:
            return {"tag": a["tag"], "job": None, "note": "No job yet. Use start_job."}
        nxt = next((c for c in job["checks"] if not c["done"]), None)
        return {"tag": a["tag"], "job": job, "next_check": nxt}

    def start_job(self, equipment: str, technician: str = "") -> dict:
        a = self.asset(equipment)
        with _locked():
            log = self._read()
            job = log["jobs"].get(a["asset_id"])
            if job and job["status"] == "in_progress":
                return {"tag": a["tag"], "job": job, "note": "A job is already open for this equipment."}
            steps = (a.get("diagnostics") or {}).get("verification_steps", [])
            job = {"tag": a["tag"], "issue_code": a.get("issue_code"), "status": "in_progress", "opened_at": _now(),
                   "technician": technician, "outcome": None, "summary": None, "closed_at": None, "notes": [],
                   "checks": [{"id": str(s["step"]), "title": s["title"], "detail": s["detail"], "citation": s.get("citation"),
                               "safety": "safety" in s["title"].lower() or s.get("citation") == "DOC-FIELD-SAFETY",
                               "done": False, "result": None, "at": None} for s in steps]}
            log["jobs"][a["asset_id"]] = job
            self._write(log)
        return {"tag": a["tag"], "job": job, "next_check": job["checks"][0] if job["checks"] else None}

    def _open_job(self, log: dict, a: dict) -> dict:
        job = log["jobs"].get(a["asset_id"])
        if not job or job["status"] != "in_progress":
            raise ValueError(f"No open job for {a['tag']}. Use start_job first.")
        return job

    def complete_check(self, equipment: str, check_id: str, result: str, technician: str = "") -> dict:
        a = self.asset(equipment)
        with _locked():
            log = self._read()
            job = self._open_job(log, a)
            check = next((c for c in job["checks"] if c["id"] == str(check_id).strip()), None)
            if check is None:
                raise ValueError(f"{a['tag']} has no check {check_id!r}; its checks are {[c['id'] for c in job['checks']]}.")
            before = job["checks"][:job["checks"].index(check)]  # steps earlier in the procedure, e.g. identity
            pending_safety = [c["id"] for c in before if c["safety"] and not c["done"]]
            if pending_safety:
                raise ValueError(f"Confirm the safety step {pending_safety[0]} (site safety and lockout/tagout) before step {check['id']}.")
            check.update(done=True, result=result.strip(), at=_now(), by=technician)
            self._write(log)
        return {"tag": a["tag"], "done": check["id"], "next_check": next((c for c in job["checks"] if not c["done"]), None)}

    def add_note(self, equipment: str, text: str, kind: str = "observation", technician: str = "") -> dict:
        """Notes go on the equipment's job, open or closed (a follow-up after a fix is still worth keeping)."""
        a = self.asset(equipment)
        if kind not in NOTE_KINDS:
            raise ValueError(f"kind must be one of {NOTE_KINDS}")
        if not text.strip():
            raise ValueError("The note is empty.")
        with _locked():
            log = self._read()
            job = log["jobs"].get(a["asset_id"])
            if not job:
                raise ValueError(f"No job for {a['tag']}. Use start_job first.")
            job["notes"].append({"at": _now(), "by": technician, "kind": kind, "text": text.strip()})
            self._write(log)
        return {"tag": a["tag"], "notes": len(job["notes"])}

    def close_job(self, equipment: str, outcome: str, summary: str, technician: str = "") -> dict:
        a = self.asset(equipment)
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        with _locked():
            log = self._read()
            job = self._open_job(log, a)
            open_checks = [c["id"] for c in job["checks"] if not c["done"]]
            if outcome == "fixed" and open_checks:
                raise ValueError(f"Cannot close {a['tag']} as fixed: checks {open_checks} are not confirmed. "
                                 "Confirm them, or close as not_fixed or escalated.")
            job.update(status=outcome, outcome=outcome, summary=summary.strip(), closed_at=_now())
            job["notes"].append({"at": _now(), "by": technician, "kind": "fix" if outcome == "fixed" else "escalation",
                                 "text": summary.strip()})
            self._write(log)
        return {"tag": a["tag"], "status": outcome, "open_checks": open_checks}


class ReadOnlyFieldService(FieldService):
    """Public scenario inspection with no shared operator history or write path."""
    def _read(self):
        return {"jobs": {}}

    def _write(self, log):
        raise RuntimeError("The public demo does not save field jobs")

    def get_job(self, equipment: str):
        return {"tag": self.asset(equipment)["tag"], "job": None,
                "note": "This hosted assistant is read-only; no visitor jobs are stored."}

    def _deny_write(self, *args, **kwargs):
        raise ValueError("This hosted assistant cannot start jobs, save notes or mark equipment repaired.")

    start_job = complete_check = add_note = close_job = _deny_write


_EQUIP = {"type": "string", "description": "Equipment tag as spoken or written, for example CV-003, P-001 or ASSET-003"}
_TECH = {"type": "string", "description": "Technician name, if known"}
TOOLS = [
    ("find_equipment", "Find registered equipment by words in its tag, type, name or area, e.g. 'pump chemical'.",
     {"query": {"type": "string"}}, ["query"]),
    ("get_equipment_status", "Current state, health, issue, latest readings against their 3-day trend, open alarms, "
     "last work order and the job progress of one piece of equipment.", {"equipment": _EQUIP}, ["equipment"]),
    ("list_open_problems", "Equipment that still needs inspection, worst first, with job progress. Equipment whose job "
     "was closed as fixed is listed under done instead.", {}, []),
    ("get_procedure", "Diagnosis, likely causes and the step-by-step verification procedure, with citations, for one "
     "piece of equipment.", {"equipment": _EQUIP}, ["equipment"]),
    ("search_service_knowledge", "Search service documents and historical cases, optionally for one piece of equipment.",
     {"query": {"type": "string"}, "equipment": _EQUIP}, ["query"]),
    ("get_job", "The open job for one piece of equipment: checklist, notes and the next check.", {"equipment": _EQUIP}, ["equipment"]),
    ("start_job", "Open a job for one piece of equipment. Its checklist is the verification procedure.",
     {"equipment": _EQUIP, "technician": _TECH}, ["equipment"]),
    ("complete_check", "Mark one checklist step as done, with what the technician observed or measured. Safety steps "
     "must be confirmed before the steps after them.", {"equipment": _EQUIP, "check_id": {"type": "string", "description": "Step number, e.g. 2"},
                                   "result": {"type": "string"}, "technician": _TECH}, ["equipment", "check_id", "result"]),
    ("add_note", "Record an observation, measurement, fix or escalation in the equipment's job. Notes show on the "
     "technician's screen.",
     {"equipment": _EQUIP, "text": {"type": "string"}, "kind": {"type": "string", "enum": list(NOTE_KINDS)},
      "technician": _TECH}, ["equipment", "text"]),
    ("close_job", "Close the job as fixed (all checks confirmed), not_fixed or escalated, with a one-sentence summary. "
     "Fixed marks the job done and removes the equipment from the inspection queue.",
     {"equipment": _EQUIP, "outcome": {"type": "string", "enum": list(OUTCOMES)}, "summary": {"type": "string"},
      "technician": _TECH}, ["equipment", "outcome", "summary"]),
]
READ_ONLY = {"find_equipment", "get_equipment_status", "list_open_problems", "get_procedure", "search_service_knowledge", "get_job"}


def tool_list() -> list[dict]:
    return [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r},
             "annotations": {"readOnlyHint": n in READ_ONLY}} for n, d, p, r in TOOLS]


def call(service: FieldService, name: str, args: dict) -> dict:
    if name not in {t[0] for t in TOOLS}:
        raise ValueError(f"Unknown tool {name!r}")
    return getattr(service, name)(**args)
