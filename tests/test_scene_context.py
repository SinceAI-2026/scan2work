"""Operator context must preserve provenance and never turn missing data safe."""
import copy
import json
import unittest

from pipeline.scene_context import build_scene_context


def valve(**updates):
    item = {"id": "OBJ-206", "asset_id": "ASSET-003", "tag": "CV-003", "mapped": True,
            "asset_class": "CONTROL_VALVE", "condition_state": "degraded", "health_score": 52,
            "issue_code": "POSITION_DEVIATION", "issue_summary": "Position tracking deviation",
            "urgency": "high", "recommended_next_check": "verify control and mechanics",
            "center": [2.124, 1.061, -1.597], "aabb_min": [2, .8, -1.8], "aabb_max": [2.3, 1.3, -1.3],
            "telemetry": {"latest": {"position_actual_pct": 67.792, "position_setpoint_pct": 58.66, "process_pressure_bar": 4.723},
                          "stats": {}, "series": {"timestamps": ["2026-10-07 23:00", "2026-10-07 23:30"]}},
            "data_quality_issues": [], "diagnostics": {"observations": [
                {"source_id": "TELEM:ASSET-003:position_actual_pct"}]}}
    item.update(updates)
    return item


def context(item):
    return build_scene_context({"assets": [item]})["devices"][item["id"]]


class SceneContextTests(unittest.TestCase):
    def test_canonical_status_binding_and_geometry_are_preserved_without_mutation(self):
        item = valve(); original = copy.deepcopy(item)
        result = context(item)
        for key in ["asset_id", "tag", "condition_state", "issue_code", "health_score", "center", "aabb_min", "aabb_max"]:
            self.assertEqual(result[key], item[key])
        self.assertEqual(result["object_id"], "OBJ-206")
        self.assertEqual(result["next_check"], item["recommended_next_check"])
        self.assertEqual(item, original)
        result["center"][0] = 0
        self.assertEqual(item, original)

    def test_position_metric_uses_own_setpoint_and_source_ids(self):
        result = context(valve())
        metric = result["primary_metric"]
        self.assertEqual(metric["latest"], 67.792)
        self.assertEqual(metric["comparison"]["value"], 58.66)
        self.assertEqual(metric["delta"], 9.132)
        self.assertEqual(metric["source_ids"], ["TELEM:ASSET-003:position_actual_pct"])
        self.assertEqual(metric["source_refs"][1], {"sheet": "Telemetry_Sample_3d", "asset_id": "ASSET-003",
            "field": "position_setpoint_pct", "as_of": "2026-10-07 23:30", "scope": "latest_sample"})
        self.assertEqual(result["snapshot_at"], "2026-10-07 23:30")
        self.assertIsNone(result["status_as_of"])  # telemetry time is not a fabricated Current_State time
        self.assertIn("Current_State:ASSET-003", result["source_ids"])

    def test_unmapped_borrowed_profiles_are_not_device_context(self):
        borrowed = valve(id="OBJ-004", mapped=False, condition_state="healthy")
        result = build_scene_context({"assets": [valve(), borrowed]})
        self.assertEqual(set(result["devices"]), {"OBJ-206"})
        self.assertEqual(result["counts"], {"assets": 2, "mapped": 1, "unmapped": 1, "hazard_assets": 1})
        self.assertIn("no workbook record", result["unmapped_notice"])

    def test_region_dataset_counts_only_actual_mapped_bindings(self):
        candidates = [{"id": f"region-{i}", "mapped": False} for i in range(50)]
        result = build_scene_context({"assets": candidates + [valve(id="linards-BOX-0173")]})
        self.assertEqual(list(result["devices"]), ["linards-BOX-0173"])
        self.assertEqual(result["counts"]["unmapped"], 50)
        self.assertNotIn("OBJ-206", result["devices"])

    def test_missing_relevant_signal_is_not_replaced_by_other_reading(self):
        item = valve(); del item["telemetry"]["latest"]["position_actual_pct"]
        metric = context(item)["primary_metric"]
        self.assertEqual(metric["signal"], "position_actual_pct")
        self.assertIsNone(metric["latest"])
        self.assertIsNone(metric["delta"])

    def test_own_history_mean_is_not_a_healthy_baseline(self):
        item = valve(asset_class="ELECTRIC_MOTOR", issue_code="MISALIGNMENT",
                     telemetry={"latest": {"vibration_mm_s": 5.5}, "stats": {"vibration_mm_s": {"mean": 5.0}}})
        metric = context(item)["primary_metric"]
        self.assertEqual(metric["comparison"]["kind"], "own_history_mean")
        self.assertEqual(metric["delta"], .5)
        self.assertIn("not a healthy baseline", metric["comparison_notice"])
        self.assertIsNone(metric["as_of"])

    def test_nonfinite_bool_and_strings_are_unknown_not_zero(self):
        for invalid in [float("nan"), float("inf"), -float("inf"), None, True, "0"]:
            with self.subTest(invalid=invalid):
                item = valve(health_score=invalid)
                item["telemetry"]["latest"].update(process_pressure_bar=invalid, position_actual_pct=invalid)
                result = context(item)
                self.assertEqual(result["hazards"][0]["state"], "unknown")
                self.assertIsNone(result["hazards"][0]["reading"]["value"])
                self.assertIsNone(result["primary_metric"]["latest"])
                self.assertIsNone(result["health_score"])
                json.dumps(result, allow_nan=False)

    def test_hot_bearing_remains_recorded_energy_when_other_energy_is_zero(self):
        item = valve(asset_class="PROCESS_PUMP", telemetry={"latest": {
            "process_pressure_bar": 0, "motor_current_a": 0, "speed_rpm": 0, "bearing_temp_c": 80}})
        hazards = {h["kind"]: h for h in context(item)["hazards"]}
        self.assertEqual(hazards["heat"]["state"], "recorded_energy")
        self.assertEqual(hazards["electrical"]["state"], "potential")
        self.assertIn("does not prove voltage absence", hazards["electrical"]["notice"])
        self.assertEqual(hazards["rotation"]["state"], "potential")
        self.assertNotIn("safe", {h["state"] for h in hazards.values()})

    def test_quality_issue_preserved_without_hiding_recorded_pressure(self):
        item = valve(data_quality_issues=[{"issue_id": "DQ-0035", "issue_type": "duplicate_event", "severity": "high"}])
        result = context(item)
        self.assertEqual(result["data_quality"][0]["issue_id"], "DQ-0035")
        self.assertEqual(result["hazards"][0]["state"], "recorded_energy")
        self.assertEqual(result["hazards"][0]["reading"]["quality"], "data_quality_flagged")
        self.assertEqual(result["primary_metric"]["quality"], "data_quality_flagged")

    def test_missing_source_identifier_uses_field_reference_without_invented_id(self):
        result = context(valve(diagnostics={}))
        self.assertEqual(result["primary_metric"]["source_ids"], [])
        ref = result["hazards"][0]["reading"]["source_refs"][0]
        self.assertEqual(ref["sheet"], "Telemetry_Sample_3d")
        self.assertEqual(ref["asset_id"], "ASSET-003")
        self.assertEqual(ref["field"], "process_pressure_bar")

    def test_runtime_scripted_fault_is_not_cited_as_recipient_workbook_data(self):
        item = valve()
        twin = {"assets": [item]}
        self.assertEqual(build_scene_context(twin)["devices"][item["id"]]["provenance"]["kind"], "synthetic_workbook")
        item.update(simulated_emergency=True, condition_state="attention_required")
        result = build_scene_context(twin)["devices"][item["id"]]
        self.assertTrue(result["simulated_emergency"])
        self.assertEqual(result["condition_state"], "attention_required")
        self.assertEqual(result["provenance"]["kind"], "runtime_scenario")
        self.assertEqual(result["source_ids"], [])
        self.assertEqual(result["primary_metric"]["source_ids"], [])
        self.assertEqual(result["primary_metric"]["comparison"]["label"], "Scenario reference")
        self.assertIsNone(result["snapshot_at"])
        self.assertEqual(result["hazards"][0]["reading"]["quality"], "runtime_scenario")
        self.assertEqual(result["hazards"][0]["reading"]["source_ids"], [])

    def test_overlays_do_not_claim_metres_isolators_or_change_navigation(self):
        nav = {"waypoints": [[1, 0, 1]]}
        twin = {"assets": [valve()], "navigation": nav, "scene": {"mpu": 1.082}}
        before = copy.deepcopy(twin)
        result = build_scene_context(twin)
        self.assertFalse(result["scale_verified"])
        self.assertEqual(result["units"], "scene_units")
        self.assertFalse(result["provenance"]["navigation_modified"])
        hazard = result["devices"]["OBJ-206"]["hazards"][0]
        self.assertNotIn("isolator", hazard)
        self.assertNotIn("radius_m", hazard)
        self.assertTrue(hazard["provenance"]["simulated"])
        self.assertEqual(twin, before)


if __name__ == "__main__":
    unittest.main()
