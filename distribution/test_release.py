"""Packaging regression tests; no source datasets or external services needed."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from distribution.build_release import code_entries, data_entries, write_archive, REQUIRED_DATA
from distribution.publish_r2 import validate_release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def dataset(self):
        source = self.root / "data"
        source.mkdir()
        for name in REQUIRED_DATA:
            (source / name).write_bytes(b"{}" if name.endswith(".json") else b"native-source")
        (source / "digital_twin.json").write_text(json.dumps({"proposal_review": {"full_view_url": "http://127.0.0.1:8768/"}, "assets": [{"center": [1, 2, 3]}]}))
        return source

    def test_dataset_excludes_mutable_and_secret_files_without_changing_source(self):
        source = self.dataset()
        before = (source / "digital_twin.json").read_bytes()
        for name in ["secrets.env", "field_log.json", "scene_edits.json", "decisions_cache.json"]:
            (source / name).write_text("private")
        (source / "photos").mkdir()
        (source / "photos" / "OBJ-1.jpg").write_bytes(b"image")
        entries = dict(data_entries(source))
        for name in ["secrets.env", "field_log.json", "scene_edits.json", "decisions_cache.json"]:
            self.assertNotIn(name, entries)
        twin = json.loads(entries["digital_twin.json"])
        self.assertIsNone(twin["proposal_review"]["full_view_url"])
        self.assertEqual(twin["assets"][0]["center"], [1, 2, 3])
        self.assertTrue(twin["runtime"]["offline"])
        self.assertEqual(before, (source / "digital_twin.json").read_bytes())
        self.assertIn("photos/OBJ-1.jpg", entries)

    def test_links_rejected_in_dataset(self):
        source = self.dataset()
        (source / "photos").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlink"):
            list(data_entries(source))

    def test_take_apart_assets_and_optional_hardware_code_are_packaged(self):
        source = self.dataset()
        for name, content in [("take_apart/index.json", b'{"units":[]}'),
                              ("take_apart/parts/unit.glb", b"native-glb"),
                              ("take_apart/ai3d/unit.glb", b"native-ai-glb")]:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        entries = dict(data_entries(source))
        self.assertIn("take_apart/index.json", entries)
        self.assertEqual(entries["take_apart/parts/unit.glb"].read_bytes(), b"native-glb")
        self.assertEqual(entries["take_apart/ai3d/unit.glb"].read_bytes(), b"native-ai-glb")
        names = ["live/board.py", "live/stores.py", "live/requirements.txt",
                 "arduino/README.md", "live/.env.example", "live/.env", "secrets.env",
                 "web/data/take_apart/parts/unit.glb"]
        code = self.root / "code"
        for name in names:
            path = code / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"content")
        with patch("distribution.build_release.subprocess.check_output", return_value="\0".join(names).encode()):
            shipped = set(dict(code_entries(code)))
        self.assertEqual(shipped, set(names[:5]))

    def test_archive_inventory_hashes_native_bytes_and_is_reproducible(self):
        source = self.dataset()
        a, b = self.root / "a.tar.gz", self.root / "b.tar.gz"
        artifact = write_archive(a, data_entries(source))
        second = write_archive(b, data_entries(source))
        self.assertEqual(artifact["sha256"], second["sha256"])
        with tarfile.open(a) as tar:
            total = sum(m.size for m in tar.getmembers())
            self.assertEqual(total, artifact["unpacked_bytes"])
            inv = json.load(tar.extractfile("asset-inventory.json"))
            for item in inv["files"]:
                raw = tar.extractfile(item["path"]).read()
                self.assertEqual(len(raw), item["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(tar.extractfile("source_scan.ply").read(), b"native-source")

    def test_publisher_detects_tampering_before_upload(self):
        release = self.root / "release"
        release.mkdir()
        for name in ["install.py", "install.sh"]:
            (release / name).write_text("installer")
        artifact = write_archive(release / "code.tar.gz", [("server.py", b"code")])
        (release / "manifest.json").write_text(json.dumps({"release": "test", "artifacts": {"code": artifact}}))
        (release / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(release.iterdir())))
        manifest, files = validate_release(release)
        self.assertEqual(files[-1], "manifest.json")
        (release / "code.tar.gz").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            validate_release(release)


if __name__ == "__main__":
    unittest.main()
