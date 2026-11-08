import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from distribution.publish_demo_assets import public_files


class PublicMediaTest(unittest.TestCase):
    def fixture(self, folder):
        files = {'source_scan.ply': b'ply', 'boiler_room_points.bin': b'splats',
                 'photos/object.jpg': b'image', 'field_log.json': b'{"private":"note"}',
                 'photos/secret.env': b'private'}
        inventory = []
        for name, data in files.items():
            p = folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            inventory.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        (folder / 'asset-inventory.json').write_text(json.dumps({'files': inventory}))

    def test_private_and_runtime_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); self.fixture(p)
            self.assertEqual({x[0] for x in public_files(p)},
                             {'boiler_room_points.bin', 'photos/object.jpg'})

    def test_modified_media_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); self.fixture(p)
            (p / 'photos/object.jpg').write_bytes(b'other')
            with self.assertRaisesRegex(ValueError, 'checksum changed'):
                public_files(p)

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); self.fixture(p)
            (p / 'photos/object.jpg').unlink()
            (p / 'photos/object.jpg').symlink_to(p / 'field_log.json')
            with self.assertRaisesRegex(ValueError, 'Linked media'):
                public_files(p)


if __name__ == '__main__':
    unittest.main()
