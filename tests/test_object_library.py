"""Saving an object to the asset library: files on disk, the library entry, removal, and the photo refinement.

Needs the generated scene (web/data) and the source scan; skipped without them.
"""
from __future__ import annotations

import functools
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import asset_export as AX
from pipeline import config as C

READY = (C.OUT_DIR / "digital_twin.json").exists() and C.PLY_PATH.exists()


@unittest.skipUnless(READY, "generated scene or source scan missing")
class ObjectLibraryTest(unittest.TestCase):
    OID = "OBJ-206"  # CV-003, the faulty valve

    @classmethod
    def setUpClass(cls):
        from pipeline import object_refine as RF
        cls.RF = RF
        cls.tmp = tempfile.TemporaryDirectory()
        lib = Path(cls.tmp.name)
        cls.patches = [mock.patch.object(AX, "LIBRARY", lib), mock.patch.object(AX, "LIBRARY_JSON", lib / "library.json"),
                       mock.patch.object(RF, "refine", functools.partial(RF.refine, iters=40))]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_save_writes_every_file_and_remove_deletes_them(self):
        entry = AX.save(self.OID)
        folder = AX.LIBRARY / self.OID
        for key in ("ply", "model_ply", "gspl", "scan_gspl", "glb", "thumb"):
            self.assertTrue((AX.LIBRARY / entry["files"][key]).is_file(), key)
        self.assertEqual([s["id"] for s in json.loads(AX.LIBRARY_JSON.read_text())["saved"]], [self.OID])
        n_scan = struct.unpack_from("<I", (folder / f"{self.OID}.scan.gspl").read_bytes(), 8)[0]
        self.assertEqual(n_scan, entry["splats"])
        self.assertGreater(entry["model"]["splats"], entry["splats"], "the model is denser than the scan")
        self.assertLess(entry["model"]["loss_end"], entry["model"]["loss_start"], "the fit lowers the photo loss")
        self.assertEqual(AX.remove(self.OID), [])
        self.assertFalse(folder.exists())

    def test_remove_refuses_paths(self):
        for bad in ("..", "", "a/b"):
            with self.assertRaises(ValueError):
                AX.remove(bad)


if __name__ == "__main__":
    unittest.main()
