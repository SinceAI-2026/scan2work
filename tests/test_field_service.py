"""The technician's job log behind the voice assistant's MCP tools (pipeline/field_service.py)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import config as C
from pipeline import field_service as FS

TWIN = C.OUT_DIR / "digital_twin.json"


@unittest.skipUnless(TWIN.exists(), "generated scene missing")
class FieldServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = FS.FieldService(json.loads(TWIN.read_text()))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.object(FS, "LOG_PATH", Path(self.tmp.name) / "field_log.json")
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_spoken_tags_resolve_to_one_asset(self):
        target_object = self.service.asset("CV-003")["id"]
        ids = {self.service.asset(t)["asset_id"] for t in ("CV-003", "cv 003", "CV3", "ASSET-003", target_object)}
        self.assertEqual(ids, {"ASSET-003"})
        with self.assertRaises(ValueError):
            self.service.asset("XX-999")

    def test_open_problems_are_the_unhealthy_excel_assets_worst_first(self):
        problems = self.service.list_open_problems()["problems"]
        self.assertEqual(len(problems), 13)
        self.assertEqual(problems[0]["state"], "attention_required")
        self.assertIn("CV-003", [p["tag"] for p in problems])

    def test_job_enforces_safety_first_and_complete_checks_before_fixed(self):
        s = self.service
        checks = s.start_job("CV-003", "Mika")["job"]["checks"]
        first_safety = next(i for i, c in enumerate(checks) if c["safety"])
        before = [c["id"] for c in checks[:first_safety]]
        safety = [c["id"] for c in checks if c["safety"]]
        after = [c["id"] for c in checks[first_safety:] if not c["safety"]]
        self.assertTrue(before and safety and after, "CV-003 has identity, safety and physical steps")
        s.complete_check("CV-003", before[0], "tag plate matches")  # identity needs no permit
        with self.assertRaisesRegex(ValueError, "safety"):
            s.complete_check("CV-003", after[0], "looked fine")
        for cid in safety:
            s.complete_check("CV-003", cid, "permit and lockout confirmed", "Mika")
        other = after
        with self.assertRaisesRegex(ValueError, "not confirmed"):
            s.close_job("CV-003", "fixed", "recalibrated positioner")
        s.complete_check("CV-003", other[0], "positioner recalibrated")
        for cid in other[1:]:
            s.complete_check("CV-003", cid, "done")
        s.add_note("CV-003", "Positioner recalibrated, error now 0.4 %", "fix", "Mika")
        self.assertEqual(s.close_job("CV-003", "fixed", "Recalibrated the positioner.")["status"], "fixed")
        saved = FS.read_log()["jobs"]["ASSET-003"]
        self.assertEqual(saved["status"], "fixed")
        self.assertTrue(all(c["done"] for c in saved["checks"]))
        self.assertEqual([n["kind"] for n in saved["notes"]], ["fix", "fix"])
        open_tags = [p["tag"] for p in s.list_open_problems()["problems"]]
        self.assertNotIn("CV-003", open_tags, "a fixed job leaves the inspection queue")
        self.assertEqual(s.list_open_problems()["done"], ["CV-003"])
        queue = s.inspection_queue()
        self.assertEqual([r["tag"] for r in queue["done"]], ["CV-003"])
        self.assertEqual(len(queue["needs_inspection"]), 12)
        s.add_note("CV-003", "Re-checked after one shift: stable")  # follow-up notes are kept on a closed job
        self.assertEqual(FS.read_log()["jobs"]["ASSET-003"]["notes"][-1]["text"], "Re-checked after one shift: stable")

    def test_notes_need_an_open_job(self):
        with self.assertRaisesRegex(ValueError, "start_job"):
            self.service.add_note("P-001", "noise at the coupling")

    def test_every_tool_is_callable_by_name(self):
        names = {t["name"] for t in FS.tool_list()}
        self.assertEqual(names, {t[0] for t in FS.TOOLS})
        self.assertEqual(FS.call(self.service, "get_equipment_status", {"equipment": "P-001"})["tag"], "P-001")
        with self.assertRaises(ValueError):
            FS.call(self.service, "drop_tables", {})


if __name__ == "__main__":
    unittest.main()
