"""Portable CPU-host contracts and real offline process integration on disposable data."""
from __future__ import annotations

import configparser
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'distribution/cpu-host'
DEFAULT_DATA = Path('/Users/perttu/Downloads/Scan2Work/staging/0.1.1')
BASELINE = Path(os.environ.get('VALMET_CPU_TEST_BASELINE', DEFAULT_DATA / 'baseline'))
EXTRACTION = Path(os.environ.get('VALMET_CPU_TEST_EXTRACTION', DEFAULT_DATA / 'extraction'))


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(port_number, path, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port_number, timeout=15)
    data = None if body is None else json.dumps(body)
    connection.request('GET' if body is None else 'POST', path, body=data,
                       headers={'Content-Type': 'application/json', **(headers or {})})
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw) if raw else None


class PortablePackageTests(unittest.TestCase):
    def test_shell_syntax_and_invalid_service_fail_closed(self):
        subprocess.run(['bash', '-n', str(PACKAGE / 'run-service.sh')], check=True)
        run = subprocess.run(['bash', str(PACKAGE / 'run-service.sh'), 'unknown'], capture_output=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn(b'Unknown Scan2Work service', run.stderr)

    def test_units_share_only_baseline_state_and_require_persistent_mount(self):
        for name in ('baseline', 'review', 'mcp'):
            config = configparser.ConfigParser(interpolation=None)
            config.read(PACKAGE / 'units' / f'scan2work-{name}.service')
            service = config['Service']
            self.assertEqual(service['User'], 'scan2work')
            self.assertEqual(service['ProtectSystem'], 'strict')
            self.assertIn('--check', service['ExecStartPre'])
            self.assertIn('/var/lib/scan2work/data/', config['Unit']['RequiresMountsFor'])
            self.assertEqual(config['Unit']['ConditionPathIsMountPoint'], '/var/lib/scan2work')
            self.assertNotIn('/workspace', service['ExecStart'])
            if name == 'review':
                self.assertNotIn('ReadWritePaths', service)
            else:
                self.assertEqual(service['ReadWritePaths'], '/var/lib/scan2work/data/baseline')

    @unittest.skipUnless(shutil.which('caddy'), 'Caddy parser is not installed on this development host')
    def test_caddy_configuration_parses(self):
        subprocess.run(['caddy', 'adapt', '--config', str(PACKAGE / 'Caddyfile'), '--adapter', 'caddyfile'],
                       check=True, stdout=subprocess.DEVNULL)


@unittest.skipUnless((BASELINE / 'digital_twin.json').exists() and (EXTRACTION / 'digital_twin.json').exists(),
                     'Prepared datasets are not installed')
class CPUProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='scan2work-cpu-host-test-')
        cls.root = Path(cls.tmp.name)
        cls.processes = []
        cls.logs = []
        cls.addClassCleanup(cls.cleanup)
        cls.before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                      for folder in (BASELINE, EXTRACTION)
                      for path in (folder / 'digital_twin.json', folder / 'field_log.json') if path.exists()}
        cls.ports = {name: port() for name in ('baseline', 'review', 'mcp')}
        config = cls.root / 'config'
        config.mkdir(mode=0o700)
        cls.token = 'local-cpu-test-credential-' * 3
        for name in ('mcp-token', 'arduino-token'):
            file = config / name
            file.write_text(cls.token)
            file.chmod(0o600)

        def link_or_copy(source, target):
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)
            return target

        for name, source in [('baseline', BASELINE), ('extraction', EXTRACTION)]:
            shutil.copytree(source, cls.root / 'data' / name, copy_function=link_or_copy,
                            ignore=shutil.ignore_patterns('field_log.json', 'field_log.json.lock',
                                                          'field_log.json.tmp', 'scene_edits.json'))
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'TMPDIR') if key in os.environ}
        env.update(SCAN2WORK_CODE_DIR=str(ROOT), SCAN2WORK_PYTHON=sys.executable,
                   SCAN2WORK_DATA_ROOT=str(cls.root / 'data'), SCAN2WORK_CONFIG_DIR=str(config),
                   SCAN2WORK_MAIN_PORT=str(cls.ports['baseline']), SCAN2WORK_REVIEW_PORT=str(cls.ports['review']),
                   SCAN2WORK_MCP_PORT=str(cls.ports['mcp']), VALMET_PUBLIC_ASSISTANT='1',
                   VALMET_PUBLIC_ARDUINO='1', VALMET_VOICE_AGENT_ID='agent_cpu_test',
                   VALMET_VOICE_MCP_URL='https://scan2work.app/mcp')
        cls.env = env
        for name in ('baseline', 'review', 'mcp'):
            log = (cls.root / f'{name}.log').open('wb')
            cls.logs.append(log)
            process = subprocess.Popen(['bash', str(PACKAGE / 'run-service.sh'), name], env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
            cls.processes.append(process)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(f'{name} failed startup: {(cls.root / (name + ".log")).read_text()[-2000:]}')
                try:
                    with socket.create_connection(('127.0.0.1', cls.ports[name]), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise AssertionError(f'{name} did not start')

    @classmethod
    def cleanup(cls):
        for process in cls.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        for log in cls.logs:
            log.close()
        cls.tmp.cleanup()

    def mcp(self, method, params=None, authenticated=True):
        status, response = request(self.ports['mcp'], '/mcp',
            {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}},
            {'Authorization': 'Bearer ' + self.token} if authenticated else {})
        return status, response

    def call(self, name, **arguments):
        status, response = self.mcp('tools/call', {'name': name, 'arguments': arguments})
        self.assertEqual(status, 200)
        result = response['result']
        self.assertFalse(result.get('isError'), result)
        return json.loads(result['content'][0]['text'])

    def test_main_opt_ins_and_review_isolation(self):
        for name, count in [('baseline', 504), ('review', 1933)]:
            status, twin = request(self.ports[name], '/data/digital_twin.json')
            self.assertEqual(status, 200); self.assertEqual(len(twin['assets']), count)
            runtime = twin['runtime']; enabled = name == 'baseline'
            self.assertTrue(runtime['public_demo']); self.assertTrue(runtime['offline'])
            self.assertFalse(runtime['shared_writes_enabled'])
            for key in ('field_jobs_enabled', 'arduino_enabled'):
                self.assertEqual(runtime[key], enabled)
            self.assertEqual(runtime['voice']['enabled'], enabled)
        self.assertEqual(request(self.ports['review'], '/api/field-jobs',
                                 {'action': 'start_job', 'equipment': 'CV-003'})[0], 403)
        self.assertEqual(request(self.ports['baseline'], '/api/scene/edits', {'action': 'remove', 'id': 'OBJ-004'})[0], 403)

    def test_mcp_and_http_share_only_disposable_job_log(self):
        self.assertEqual(self.mcp('tools/list', authenticated=False)[0], 401)
        self.assertEqual(len(self.mcp('tools/list')[1]['result']['tools']), 10)
        status, _ = request(self.ports['baseline'], '/api/field-jobs',
                            {'action': 'start_job', 'equipment': 'CV-003', 'technician': 'CPU deployment test'})
        self.assertEqual(status, 200)
        self.assertEqual(self.call('get_job', equipment='CV-003')['job']['status'], 'in_progress')
        with ThreadPoolExecutor(max_workers=2) as workers:
            via_http = workers.submit(request, self.ports['baseline'], '/api/field-jobs',
                {'action': 'add_note', 'equipment': 'CV-003', 'text': 'Disposable HTTP synchronization check'})
            via_mcp = workers.submit(self.call, 'add_note', equipment='CV-003', text='Disposable MCP synchronization check')
            self.assertEqual(via_http.result()[0], 200); via_mcp.result()
        job = self.call('get_job', equipment='CV-003')['job']
        self.assertEqual({n['text'] for n in job['notes']},
                         {'Disposable HTTP synchronization check', 'Disposable MCP synchronization check'})
        self.assertEqual(request(self.ports['baseline'], '/api/field-jobs')[1]['jobs']['CV-003']['notes'], job['notes'])
        for file, digest in self.before.items():
            self.assertEqual(hashlib.sha256(Path(file).read_bytes()).hexdigest(), digest)
        self.assertFalse((self.root / 'data/extraction/field_log.json').exists())

    def test_arduino_prepared_but_not_falsely_online(self):
        status, board = request(self.ports['baseline'], '/api/live/board')
        self.assertEqual(status, 200); self.assertFalse(board['board_online'])
        self.assertEqual(request(self.ports['baseline'], '/api/live/board/heartbeat', {})[0], 401)
        self.assertFalse(request(self.ports['baseline'], '/api/live/board')[1]['board_online'])
        self.assertEqual(request(self.ports['review'], '/api/live/board')[0], 403)

    def test_missing_private_token_fails_before_server_launch(self):
        env = {**self.env, 'SCAN2WORK_CONFIG_DIR': str(self.root / 'missing')}
        run = subprocess.run(['bash', str(PACKAGE / 'run-service.sh'), 'mcp'], env=env, capture_output=True, timeout=5)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn(b'Private MCP token file is missing', run.stderr)


if __name__ == '__main__':
    unittest.main()
