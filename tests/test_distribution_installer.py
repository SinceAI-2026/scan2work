"""Installer safety and local launcher tests; no pip/network/model work."""
import copy
import hashlib
import http.client
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest import mock

from scripts import install_scan2work as I
from scripts import scan2work_launcher as L


STUB_SERVER = b'''import json,os,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  body=b'{"assets":[]}'
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
s=ThreadingHTTPServer((os.environ['VALMET_BIND_HOST'],int(sys.argv[1])),H)
print('server listening on http://127.0.0.1:'+str(s.server_address[1]),flush=True)
s.serve_forever()
'''


def archive(path, files):
    with tarfile.open(path, 'w:gz') as out:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data)
            out.addfile(info, io.BytesIO(data))
    return {'path': path.name, 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'unpacked_bytes': sum(map(len, files.values()))}


def scene_files():
    twin = {'assets': [{'id': 'OBJ-1', 'asset_id': 'ASSET-1', 'mapped': True}],
            'scene': {}, 'navigation': {'grid_meta': {}}}
    return {'digital_twin.json': json.dumps(twin).encode(),
            'boiler_room_points.bin': struct.pack('<4sIII', b'GSPL', 3, 1, 40) + bytes(40),
            'splat_instance.bin': struct.pack('<4sII', b'GSID', 1, 1) + bytes(2)}


def fake_environment(stage, python):
    executable = I.venv_python(stage)
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    (stage / '.venv' / 'pyvenv.cfg').write_text('test placeholder\n')
    shutil.copy2(stage / 'app' / 'scripts' / 'scan2work_launcher.py', stage / 'start.py')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.release = self.root / 'release'; self.release.mkdir()
        self.dest = self.root / 'installed'
        code = {'server.py': STUB_SERVER, 'requirements-demo.lock.txt': b'# fixture only\n',
                'scripts/scan2work_launcher.py': Path(L.__file__).read_bytes()}
        self.manifest = {'schema_version': 1, 'release': 'test.1', 'artifacts': {
            'code': archive(self.release / 'code.tar.gz', code),
            'baseline': archive(self.release / 'baseline.tar.gz', scene_files()),
            'extraction': archive(self.release / 'extraction.tar.gz', scene_files())}}
        self.path = self.release / 'manifest.json'; self.save_manifest()

    def save_manifest(self):
        self.path.write_text(json.dumps(self.manifest))

    def install(self, with_extraction=False, validator=None):
        with mock.patch.object(I, 'supported_python', return_value=sys.executable), \
             mock.patch.object(I, 'prepare_environment', side_effect=fake_environment), \
             mock.patch.object(I, 'validate_install', side_effect=validator or I.validate_install):
            return I.install(self.path, self.dest, with_extraction)

    def assert_no_partial_install(self):
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob('.installed.stage-*')), [])
        self.assertFalse((self.root / '.installed.install.lock').exists())

    def test_baseline_profile_installs_and_launcher_doctor_starts_http(self):
        self.install()
        self.assertEqual(json.loads((self.dest / 'install.json').read_text())['profiles'], ['baseline'])
        self.assertTrue((self.dest / 'data' / 'baseline' / 'digital_twin.json').is_file())
        self.assertFalse((self.dest / 'data' / 'extraction').exists())
        self.assertTrue((self.dest / 'start.py').is_file())
        result = subprocess.run([str(I.venv_python(self.dest)), str(self.dest / 'start.py'), '--doctor'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Doctor passed', result.stdout)

    def test_optional_extraction_profile_installs_both_datasets(self):
        self.install(True)
        self.assertEqual(json.loads((self.dest / 'install.json').read_text())['profiles'], ['baseline', 'extraction'])
        env = L.server_environment(self.dest, 'baseline', 8772)
        self.assertEqual(env['VALMET_PROPOSAL_VIEW_URL'], 'http://127.0.0.1:8772/')
        self.assertEqual(env['VALMET_OFFLINE'], '1')
        self.assertEqual(env['VALMET_BIND_HOST'], '127.0.0.1')
        self.assertEqual(L.server_environment(self.dest, 'baseline')['VALMET_PROPOSAL_VIEW_URL'], '')

    def test_existing_install_directory_is_never_changed_even_when_empty(self):
        self.dest.mkdir()
        with self.assertRaisesRegex(I.InstallError, 'already exists'):
            I.install(self.path, self.dest)
        self.assertEqual(list(self.dest.iterdir()), [])
        (self.dest / 'keep').write_text('user data')
        with self.assertRaises(I.InstallError):
            I.install(self.path, self.dest)
        self.assertEqual((self.dest / 'keep').read_text(), 'user data')

    def test_checksum_failure_cleans_stage_and_leaves_destination_absent(self):
        self.manifest['artifacts']['baseline']['sha256'] = '0' * 64
        self.save_manifest()
        with self.assertRaisesRegex(I.InstallError, 'SHA256'):
            self.install()
        self.assert_no_partial_install()

    def test_partial_dependency_or_startup_failure_cleans_stage(self):
        with self.assertRaisesRegex(I.InstallError, 'simulated startup failure'):
            self.install(validator=mock.Mock(side_effect=I.InstallError('simulated startup failure')))
        self.assert_no_partial_install()

    def test_dependency_failure_cleans_partial_virtualenv(self):
        def fail(stage, python):
            (stage / '.venv').mkdir()
            (stage / '.venv' / 'partial').write_text('partial installation')
            raise I.InstallError('simulated dependency failure')
        with mock.patch.object(I, 'supported_python', return_value=sys.executable), \
             mock.patch.object(I, 'prepare_environment', side_effect=fail):
            with self.assertRaisesRegex(I.InstallError, 'dependency failure'):
                I.install(self.path, self.dest)
        self.assert_no_partial_install()

    def test_size_and_disk_checks_fail_before_publication(self):
        spec = copy.deepcopy(self.manifest['artifacts']['code']); spec['bytes'] -= 1
        with self.assertRaisesRegex(I.InstallError, 'size'):
            I.download_artifact(self.release / 'code.tar.gz', self.root / 'copy', spec)
        with mock.patch.object(I, 'supported_python', return_value=sys.executable), \
             mock.patch.object(I.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(1, 1, 0)):
            with self.assertRaisesRegex(I.InstallError, 'disk space'):
                I.install(self.path, self.dest)
        self.assert_no_partial_install()

    def test_http_requires_explicit_flag_and_artifacts_cannot_escape_release(self):
        with self.assertRaises(I.InstallError):
            I.checked_url('http://127.0.0.1/manifest.json')
        self.assertEqual(I.checked_url('http://127.0.0.1/manifest.json', True), 'http://127.0.0.1/manifest.json')
        for name in ('../escape.tar.gz', '%2e%2e/escape.tar.gz', '/absolute.tar.gz', 'https://other/artifact', '//other/a'):
            with self.subTest(name=name), self.assertRaises(I.InstallError):
                I.artifact_source(self.path, name)

    def test_manifest_and_archive_requests_identify_installer_through_redirects(self):
        requested = []
        release = self.release

        class BrandedDownloadHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(release), **kwargs)

            def do_GET(self):
                agent = self.headers.get('User-Agent')
                requested.append((self.path, agent))
                if agent != 'Scan2WorkInstaller/0.1':
                    self.send_error(403, 'An explicit installer identity is required')
                elif self.path.startswith('/redirect/'):
                    self.send_response(302)
                    self.send_header('Location', self.path.removeprefix('/redirect'))
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                else:
                    super().do_GET()

            def log_message(self, *args):
                pass

        fixture_http = ThreadingHTTPServer(('127.0.0.1', 0), BrandedDownloadHandler)
        thread = threading.Thread(target=fixture_http.serve_forever, daemon=True); thread.start()
        try:
            connection = http.client.HTTPConnection('127.0.0.1', fixture_http.server_port)
            connection.request('GET', '/manifest.json')
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 403)
            connection.close()
            base = f'http://127.0.0.1:{fixture_http.server_port}'
            for index, prefix in enumerate(('', '/redirect')):
                manifest = I.read_manifest(f'{base}{prefix}/manifest.json', allow_http=True)
                self.assertEqual(manifest, self.manifest)
                for name, spec in manifest['artifacts'].items():
                    output = self.root / f'download-{index}-{name}'
                    I.download_artifact(f'{base}{prefix}/{spec["path"]}', output, spec, allow_http=True)
                    self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), spec['sha256'])
            self.assertTrue(all(agent == 'Scan2WorkInstaller/0.1' for _, agent in requested[1:]))
            self.assertEqual(len(requested), 13)  # one rejection + 4 direct + 4 redirects/followups
        finally:
            fixture_http.shutdown(); fixture_http.server_close(); thread.join()

    def test_launcher_detects_corrupt_binary_before_starting(self):
        self.install()
        (self.dest / 'data' / 'baseline' / 'splat_instance.bin').write_bytes(b'broken')
        with mock.patch.object(L, 'start_server') as start:
            self.assertEqual(L.main(['--doctor'], root=self.dest), 1)
            start.assert_not_called()

    def test_library_checks_current_entries_without_requiring_deleted_archive_entries(self):
        folder = self.root / 'dataset'; folder.mkdir()
        for name, content in scene_files().items():
            (folder / name).write_bytes(content)
        library = folder / 'library'; library.mkdir()
        index = library / 'library.json'
        index.write_text('{"saved":[]}')
        L.check_dataset(folder)
        index.write_text('{"saved":[{"files":{"ply":"missing.ply"}}]}')
        with self.assertRaisesRegex(L.LaunchError, 'Missing packaged file'):
            L.check_dataset(folder)
        (library / 'missing.ply').write_bytes(b'ply')
        L.check_dataset(folder)

    def test_optional_take_apart_files_are_checked_without_requiring_them_for_older_scenes(self):
        folder = self.root / 'dataset'; folder.mkdir()
        for name, content in scene_files().items():
            (folder / name).write_bytes(content)
        L.check_dataset(folder)
        models = folder / 'take_apart'; models.mkdir()
        index = models / 'index.json'
        index.write_text(json.dumps({'models': {'pump': {'file': 'pump.glb'}}, 'ai3d': {}}))
        with self.assertRaisesRegex(L.LaunchError, 'Missing packaged file'):
            L.check_dataset(folder)
        model = models / 'pump.glb'
        model.write_bytes(struct.pack('<4sII', b'glTF', 2, 24) + struct.pack('<I4s', 4, b'JSON') + b'{}  ')
        L.check_dataset(folder)
        model.write_bytes(model.read_bytes()[:-1])
        with self.assertRaisesRegex(L.LaunchError, 'Invalid optional GLB'):
            L.check_dataset(folder)
        index.write_text(json.dumps({'ai3d': {'escape': {'file': '../source_scan.ply'}}}))
        with self.assertRaisesRegex(L.LaunchError, 'escapes'):
            L.check_dataset(folder)


class SafeExtractionTests(unittest.TestCase):
    def test_regular_files_extract_but_links_traversal_and_special_members_do_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = archive(root / 'good.tar.gz', {'folder/file.txt': b'hello'})
            I.extract_archive(root / 'good.tar.gz', root / 'good', spec['unpacked_bytes'])
            self.assertEqual((root / 'good' / 'folder' / 'file.txt').read_bytes(), b'hello')
            for i, (name, kind) in enumerate((('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                     ('a\\b', tarfile.REGTYPE), ('link', tarfile.SYMTYPE), ('hard', tarfile.LNKTYPE),
                     ('fifo', tarfile.FIFOTYPE))):
                path = root / f'bad-{i}.tar.gz'
                with tarfile.open(path, 'w:gz') as bundle:
                    member = tarfile.TarInfo(name); member.type = kind; member.linkname = '../outside'
                    bundle.addfile(member, io.BytesIO())
                with self.subTest(name=name), self.assertRaises(I.InstallError):
                    I.extract_archive(path, root / f'bad-{i}', 1)
            self.assertFalse((root / 'escape').exists())

    def test_declared_unpacked_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); archive(root / 'x.tar.gz', {'file': b'12345'})
            with self.assertRaisesRegex(I.InstallError, 'expands beyond'):
                I.extract_archive(root / 'x.tar.gz', root / 'out', 4)


if __name__ == '__main__':
    unittest.main()
