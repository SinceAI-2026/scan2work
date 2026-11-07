"""Run from the repo root: .venv/bin/python -m unittest tests.test_fault_panel -v."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from live import board as panel
from pipeline import field_service


def _asset(tag, state, health, stats=None):
    return {"tag": tag, "asset_id": f"ASSET-{tag}", "id": f"OBJ-{tag}", "mapped": True, "asset_class": "PROCESS_PUMP",
            "label": "pump", "condition_state": state, "health_score": health, "issue_code": f"ISSUE_{tag[-1]}",
            "issue_summary": f"{tag} is off", "telemetry": {"stats": stats or {}},
            "diagnostics": {"verification_steps": [{"step": 1, "title": "Check it", "detail": "look"}]}}


class FaultPanelTests(unittest.TestCase):
    def setUp(self):
        log = Path(tempfile.mkdtemp()) / "field_log.json"
        patcher = mock.patch.object(field_service, "LOG_PATH", log)
        patcher.start()
        self.addCleanup(patcher.stop)
        twin = {"assets": [
            _asset("P-1", "watch", 70),
            _asset("P-2", "attention_required", 40, {
                "temp_c": {"latest": 80.0, "mean": 60.0, "min": 50.0, "max": 80.0, "std": 5.0},
                "vibration": {"latest": 2.0, "mean": 1.9, "min": 1.0, "max": 3.0, "std": 0.5},
                "quality": {"latest": 99.0, "mean": 99.0, "min": 99.0, "max": 99.0, "std": 0.0}}),
            _asset("P-3", "healthy", 95)]}
        self.field = field_service.FieldService(twin)
        panel._BOARD = None

    def call(self, method, sub="", body=None, token=None):
        return panel.handle(method, f"/api/live/board{sub}", body, self.field, token)

    def test_board_claims_the_first_broken_item_with_its_worst_signal(self):
        out, code = self.call("POST", "/claim")
        self.assertEqual((code, out["status"], out["task"]["tag"]), (200, "fault", "P-2"))   # worst first
        sig = out["task"]["signal"]
        self.assertEqual(sig["name"], "temp_c")          # 4 sigma beats 0.2; a flat signal is skipped
        self.assertEqual((sig["now_pct"], sig["normal_pct"]), (100.0, 33.3))

    def test_task_goes_in_progress_then_done_and_stays_done(self):
        self.call("POST", "/claim")
        out, _ = self.call("POST", "/action", {"action": "ack"})
        self.assertEqual(out["status"], "in_progress")
        self.field.complete_check("P-2", "1", "ok")
        self.field.close_job("P-2", "fixed", "replaced")
        out, _ = self.call("POST", "/heartbeat", {"touch": None})
        self.assertEqual((out["status"], out["task"]["tag"], out["next_fault"]), ("done", "P-2", "P-1"))
        out, _ = self.call("POST", "/action", {"action": "claim"})   # a hold (or a restart) takes the newest
        self.assertEqual((out["status"], out["task"]["tag"]), ("fault", "P-1"))
        kinds = [e["kind"] for e in self.call("GET", "/events")[0]["events"]]
        self.assertEqual(kinds, ["claimed", "acknowledged", "done", "claimed"])

    def test_nothing_broken_is_all_clear(self):
        self.field = field_service.FieldService({"assets": [_asset("P-3", "healthy", 95)]})
        out, _ = self.call("POST", "/claim")
        self.assertEqual((out["status"], out["task"]), ("clear", None))

    def test_claim_skips_faults_someone_is_working_on(self):
        self.field.start_job("P-2")
        out, _ = self.call("POST", "/claim")
        self.assertEqual((out["status"], out["task"]["tag"]), ("fault", "P-1"))

    def test_hold_with_nothing_left_starts_a_simulated_emergency(self):
        self.field.start_job("P-1")
        self.field.start_job("P-2")
        out, _ = self.call("POST", "/claim")              # on start: nothing new, so the first open job (amber)
        self.assertEqual(out["status"], "in_progress")
        out, _ = self.call("POST", "/action", {"action": "claim"})   # a hold: a new emergency on the healthy P-3
        self.assertEqual((out["status"], out["task"]["tag"]), ("fault", "P-3"))
        p3 = self.field.asset("P-3")
        self.assertTrue(p3["simulated_emergency"])
        self.assertEqual((p3["condition_state"], p3["urgency"]), ("attention_required", "high"))
        self.assertEqual(p3["issue_code"], "ISSUE_2")   # only P-2 is a scripted (attention/degraded) fault
        self.assertTrue(p3["issue_summary"].startswith("P-3"))   # the scripted text names its new device
        self.assertEqual(len(p3["diagnostics"]["verification_steps"]), 1)
        self.assertIn("P-3", [r["tag"] for r in self.field.inspection_queue()["needs_inspection"]])
        self.assertEqual(self.call("GET", "/events")[0]["events"][-2]["kind"], "simulated_emergency")

    def test_token_guards_the_board_calls_only(self):
        with mock.patch.dict(os.environ, {"LIVE_TOKEN": "s3cret"}):
            self.assertEqual(self.call("POST", "/claim")[1], 401)
            self.assertEqual(self.call("POST", "/heartbeat", {}, token="wrong")[1], 401)
            self.assertEqual(self.call("POST", "/claim", token="s3cret")[1], 200)
            self.assertEqual(self.call("POST", "/action", {"action": "ack"})[1], 200)
        self.assertIsNone(panel.handle("GET", "/api/assets", None, self.field))
        self.assertEqual(self.call("POST", "/action", {"action": "explode"})[1], 400)


if __name__ == "__main__":
    unittest.main()
