"""Run from the repo root: .venv/bin/python -m unittest tests.test_local_objects -v."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from pipeline import local_objects


def _object(oid, cls, label, center, size):
    return {"id": oid, "class": cls, "label": label, "confidence": 0.8, "label_source": "classifier",
            "excel_class": None, "center": center, "size": size}


class LocalObjectsTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(0)
        # 300 levelled Gaussians; ingest dropped PLY row 5, so splat index != PLY row from row 5 on
        self.ply_rows = np.array([r for r in range(301) if r != 5])
        xyz = np.concatenate([rng.uniform([0, 0.2, 0], [0.4, 0.6, 0.3], (150, 3)),        # a valve-sized object
                              rng.uniform([2, 1.9, 1], [3.5, 2.0, 1.1], (150, 3))])     # a pipe above the split height
        self.splat = {"xyz": xyz, "rgb": np.full((300, 3), 0.5, np.float32), "ply_rows": self.ply_rows}
        self.dir = Path(tempfile.mkdtemp())
        objects = [_object("obj-001", "valve", "valve", [0, 0, 0], [0.4, 0.4, 0.3]),
                   _object("obj-002", "unclassified", "pipe", [0, 0, 0], [1.5, 0.1, 0.1]),
                   _object("obj-003", "unclassified", "bucket", [0, 0, 0], [0.3, 0.3, 0.3])]
        (self.dir / "objects.json").write_text(json.dumps(objects))
        rows_a = self.ply_rows[:150]                    # PLY rows of the first 150 splats (skips row 5)
        rows_b = self.ply_rows[150:]
        rows_c = np.array([5, 5, 5])                    # only the dropped row: nothing left, so skipped
        rows = np.concatenate([rows_a, rows_b, rows_c])
        np.savez(self.dir / "objects.npz", rows=rows, offsets=np.array([0, 150, 300, 303]))

    def load(self):
        with mock.patch.object(local_objects.C, "LOCAL_OBJECTS_DIR", self.dir):
            return local_objects.load(self.splat)

    def test_objects_become_assets_with_classes_and_provenance(self):
        out = self.load()
        valve, pipe = out["assets"]
        self.assertEqual(len(out["assets"]), 2)
        self.assertEqual(out["stats"]["skipped"], 1)
        self.assertEqual((valve["class"], valve["tier"], valve["movable"]), ("control_valve", "equipment", False))
        self.assertEqual((pipe["class"], pipe["tier"]), ("overhead_pipe", "overhead"))
        self.assertEqual(valve["label_source"], "local:classifier")
        self.assertEqual(valve["local_id"], "obj-001")

    def test_membership_follows_ply_rows_not_positions(self):
        out = self.load()
        idx = out["splat_asset_idx"]
        self.assertTrue((idx[:150] == 0).all())
        self.assertTrue((idx[150:] == 1).all())

    def test_box_is_refitted_on_the_levelled_splat(self):
        valve = self.load()["assets"][0]
        self.assertTrue(0.0 <= valve["center"][0] <= 0.4 and 0.2 <= valve["center"][1] <= 0.6)
        self.assertLess(max(valve["size"]), 0.6)


if __name__ == "__main__":
    unittest.main()
