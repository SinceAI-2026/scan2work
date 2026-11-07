"""Hosted preview stays read-only while preserving the prepared spatial demo."""
import copy
import builtins
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import sys
import unittest
from unittest import mock

os.environ['VALMET_OFFLINE'] = '1'
import server
from pipeline.runtime_policy import configure_server_runtime
from scripts import serve_public_demo


class PublicRuntimeTests(unittest.TestCase):
    def test_public_forces_offline_before_reading_manifest_or_config(self):
        env = {'VALMET_PUBLIC_DEMO': '1', 'VALMET_OFFLINE': '0'}
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('must not read scene or credentials')):
            self.assertTrue(configure_server_runtime(Path('/unused'), env))
        self.assertEqual(env['VALMET_OFFLINE'], '1')

    def test_public_service_does_not_read_or_write_operator_log(self):
        field = server.PreviewFieldService({'assets': []})
        with mock.patch.object(server.field_service, 'read_log', side_effect=AssertionError('private log')):
            self.assertEqual(field.inspection_queue(), {'needs_inspection': [], 'done': [], 'jobs': {}})
        with self.assertRaisesRegex(RuntimeError, 'does not save'):
            field._write({'jobs': {}})

    def test_hosted_launcher_sets_loopback_and_offline_without_rewriting_files(self):
        with mock.patch.object(serve_public_demo, 'check_dataset', return_value=504) as check, \
             mock.patch.object(serve_public_demo.os, 'execve') as execute:
            serve_public_demo.main(['--data-dir', '/prepared', '--install-url', 'https://downloads.example/', '--port', '8089'])
            check.assert_called_once_with(Path('/prepared'), thorough=False)
            command, env = execute.call_args.args[1:]
            self.assertEqual(command[-1], '8089')
            for key, value in {'VALMET_BIND_HOST': '127.0.0.1', 'VALMET_PUBLIC_DEMO': '1',
                               'VALMET_OFFLINE': '1', 'VALMET_PROPOSAL_VIEW_URL': ''}.items():
                self.assertEqual(env[key], value)

    def test_preflight_failure_does_not_start_server(self):
        with mock.patch.object(serve_public_demo, 'check_dataset', side_effect=serve_public_demo.LaunchError('missing renderer')), \
             mock.patch.object(serve_public_demo.os, 'execve') as execute, self.assertRaises(SystemExit) as failure:
            serve_public_demo.main(['--data-dir', '/missing', '--install-url', 'https://downloads.example/'])
        self.assertEqual(failure.exception.code, 1)
        execute.assert_not_called()

    def test_hosted_assistant_is_explicit_and_keeps_offline_loopback(self):
        with mock.patch.object(serve_public_demo, 'check_dataset', return_value=504), \
             mock.patch.object(serve_public_demo.os, 'execve') as execute:
            for enabled in (False, True):
                serve_public_demo.main(['--data-dir', '/prepared', '--install-url', 'https://downloads.example/']
                                       + (['--assistant'] if enabled else []))
                env = execute.call_args.args[2]
                self.assertEqual(env['VALMET_PUBLIC_ASSISTANT'], '1' if enabled else '0')
                self.assertEqual(env['VALMET_OFFLINE'], '1')
                self.assertEqual(env['VALMET_BIND_HOST'], '127.0.0.1')

    def test_hosted_launcher_requires_explicit_safe_download_url(self):
        for value in ('javascript:alert(1)', 'http://downloads.example/', 'https://user:pass@example.com/'):
            with self.assertRaises(serve_public_demo.argparse.ArgumentTypeError):
                serve_public_demo.https_url(value)

    def test_public_arduino_startup_requires_private_credential_before_loading_data(self):
        with mock.patch.object(server, 'PUBLIC_ARDUINO', True), mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(server, 'load_data', side_effect=AssertionError('must fail before loading data')):
            with self.assertRaisesRegex(ValueError, 'private device token'):
                server.run_server(0)


class PublicHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.web = root / 'web'; self.web.mkdir()
        (self.web / 'index.html').write_text('prepared viewer')
        self.data = root / 'data'; self.data.mkdir()
        self.twin = {'runtime': {'offline': False}, 'assets': [], 'target_asset_id': 'ASSET-003',
                     'proposal_review': {'full_view_url': None},
                     'navigation': {'routes_by_asset': {'ASSET-003': {'waypoints': [{'position': [0, 0, 0]}]}}}}
        self.manifest = self.data / 'digital_twin.json'
        self.manifest.write_text(json.dumps(self.twin))
        self.original = self.manifest.read_bytes()
        for name in ('field_log.json', 'scene_edits.json', 'decisions_cache.json', '.private'):
            (self.data / name).write_text('operator-private')
        (self.data / 'clip.mp4').write_bytes(b'0123456789')
        (self.data / 'photo.jpg').write_bytes(b'photo')
        outside = root / 'outside.txt'; outside.write_text('outside-private')
        (self.data / 'escape.txt').symlink_to(outside)
        self.asset = {'id': 'OBJ-206', 'asset_id': 'ASSET-003', 'mapped': True,
                      'tag': 'CV-003', 'condition_state': 'degraded', 'asset_class': 'valve',
                      'diagnostics': {'verification_steps': [{'step': 1, 'title': 'Inspect',
                          'detail': 'Inspect the actuator', 'citation': 'DOC-003'}],
                          'work_order_draft': {'draft_id': 'DRAFT-003', 'evidence_bundle': ['DOC-003']}}}
        field = server.PreviewFieldService({'assets': [self.asset]})
        patches = [mock.patch.object(server, 'WEB_DIR', self.web),
                   mock.patch.object(server.C, 'OUT_DIR', self.data),
                   mock.patch.object(server, 'DATA_PATH', self.manifest),
                   mock.patch.object(server, 'TWIN_DATA', self.twin),
                   mock.patch.object(server, 'PUBLIC_DEMO', True),
                   mock.patch.object(server, 'PUBLIC_ASSISTANT', False),
                   mock.patch.object(server, 'PUBLIC_ARDUINO', False),
                   mock.patch.object(server, 'ASSETS_BY_ID', {'ASSET-003': self.asset}),
                   mock.patch.object(server, 'ASSETS_BY_OBJ', {'OBJ-206': self.asset}),
                   mock.patch.object(server, 'WORK_ORDER_DRAFTS', [{'notes': 'private draft'}]),
                   mock.patch.object(server, 'FIELD', field, create=True),
                   mock.patch.object(server, 'RETRIEVER', None),
                   mock.patch.dict(os.environ, {'VALMET_INSTALL_URL': 'https://downloads.example/releases/0.1.0/'})]
        for patch in patches:
            patch.start(); self.addCleanup(patch.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.TwinRequestHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        if isinstance(body, dict):
            body = json.dumps(body)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse(); raw = response.read(); status = response.status
        response_headers = dict(response.getheaders()); connection.close()
        content = json.loads(raw) if raw and 'application/json' in response_headers.get('Content-Type', '') else raw
        return status, content, response_headers

    def test_manifest_runtime_projection_does_not_change_dataset(self):
        before = copy.deepcopy(self.twin)
        status, manifest, _ = self.request('GET', '/data/digital_twin.json')
        self.assertEqual(status, 200)
        self.assertEqual(manifest['runtime'], {'public_demo': True, 'offline': True,
            'external_models_enabled': False, 'shared_writes_enabled': False,
            'voice_assistant': False, 'field_jobs_enabled': False,   # off unless VALMET_PUBLIC_ASSISTANT=1
            'arduino_enabled': False, 'arduino_actions_enabled': False,
            'install_url': 'https://downloads.example/releases/0.1.0/', 'voice': {
                'enabled': False, 'provider': 'elevenlabs', 'agent_id': None,
                'agent_name': 'Ilkka', 'service_mode': 'read_only', 'mcp_url': None}})
        self.assertEqual(self.twin, before)
        self.assertEqual(self.manifest.read_bytes(), self.original)
        self.assertTrue(self.request('GET', '/data//digital_twin.json')[1]['runtime']['public_demo'])

    def test_explicit_voice_config_does_not_enable_shared_writes_or_backend_models(self):
        with mock.patch.dict(os.environ, {'VALMET_VOICE_AGENT_ID': 'agent_fixture123',
            'VALMET_VOICE_AGENT_NAME': 'Public guide', 'VALMET_VOICE_MCP_URL': 'https://example.test/mcp'}):
            runtime = self.request('GET', '/data/digital_twin.json')[1]['runtime']
        self.assertEqual(runtime['voice'], {'enabled': True, 'provider': 'elevenlabs',
            'agent_id': 'agent_fixture123', 'agent_name': 'Public guide',
            'service_mode': 'read_only', 'mcp_url': 'https://example.test/mcp'})
        self.assertTrue(runtime['offline'])
        self.assertFalse(runtime['external_models_enabled'])
        self.assertFalse(runtime['shared_writes_enabled'])
        self.assertEqual(self.manifest.read_bytes(), self.original)

    def test_shared_mutations_are_denied_without_invoking_writers(self):
        with mock.patch.object(server, 'apply_edit', side_effect=AssertionError('write')), \
             mock.patch.object(server.field_service, 'call', side_effect=AssertionError('write')), \
             mock.patch.object(server.asset_export, 'save', side_effect=AssertionError('write')), \
             mock.patch.object(server.asset_export, 'remove', side_effect=AssertionError('write')):
            for method, path in [('POST', '/api/scene/edits'), ('POST', '/api/field-jobs'),
                                 ('POST', '/api/library'), ('DELETE', '/api/library/OBJ-206')]:
                with self.subTest(path=path):
                    status, result, _ = self.request(method, path, {'id': 'OBJ-206'})
                    self.assertEqual(status, 403)
                    self.assertEqual(result['code'], 'public_demo_read_only')
                    self.assertFalse(result['persisted'])

    def test_assistant_preserves_upstream_voice_and_explicit_agent_overrides(self):
        before = copy.deepcopy(self.twin)
        with mock.patch.object(server, 'PUBLIC_ASSISTANT', True), mock.patch.dict(os.environ, {}, clear=True):
            self.twin['runtime']['voice'] = {'enabled': False}
            runtime = self.request('GET', '/data/digital_twin.json')[1]['runtime']
            self.assertTrue(runtime['voice_assistant'])
            self.assertTrue(runtime['field_jobs_enabled'])
            self.assertNotIn('voice', runtime)
            self.assertFalse(runtime['shared_writes_enabled'])
            with mock.patch.dict(os.environ, {'VALMET_VOICE_AGENT_ID': 'agent_fixture123'}):
                voice = self.request('GET', '/data/digital_twin.json')[1]['runtime']['voice']
                self.assertTrue(voice['enabled'])
                self.assertEqual(voice['service_mode'], 'shared_jobs')
            with mock.patch.dict(os.environ, {'VALMET_VOICE_AGENT_ID': ''}):
                self.assertFalse(self.request('GET', '/data/digital_twin.json')[1]['runtime']['voice']['enabled'])
            self.assertEqual(self.twin['runtime']['voice'], {'enabled': False})
        self.twin['runtime'] = before['runtime']
        self.assertEqual(self.manifest.read_bytes(), self.original)

    def test_explicit_assistant_shares_jobs_only_and_bounds_notes(self):
        log = self.data / 'shared_test_log.json'
        field = server.field_service.FieldService({'assets': [self.asset]})
        with mock.patch.object(server, 'PUBLIC_ASSISTANT', True), mock.patch.object(server, 'FIELD', field), \
             mock.patch.object(server.field_service, 'LOG_PATH', log):
            status, _, _ = self.request('POST', '/api/field-jobs', {'action': 'start_job', 'equipment': 'CV-003'})
            self.assertEqual(status, 200)
            before = log.read_bytes()
            status, _, _ = self.request('POST', '/api/field-jobs', {'action': 'add_note', 'equipment': 'CV-003', 'text': 'x' * 1001})
            self.assertEqual(status, 413)
            self.assertEqual(log.read_bytes(), before)
            status, _, _ = self.request('POST', '/api/field-jobs', {'action': 'add_note', 'equipment': 'CV-003', 'text': 'Demo observation'})
            self.assertEqual(status, 200)
            self.assertIn('Demo observation', json.dumps(self.request('GET', '/api/field-log')[1]))
            for path in ('/api/scene/edits', '/api/library', '/api/live/board/action'):
                self.assertEqual(self.request('POST', path, {})[0], 403)
            self.assertEqual(self.request('GET', '/data/field_log.json')[0], 404)

    def test_operator_state_is_not_exposed_by_api_or_static_paths(self):
        for path, expected in [('/api/scene/edits', {'removed': []}),
                               ('/api/field-log', {'jobs': {}}), ('/api/work-orders', {'drafts': []})]:
            self.assertEqual(self.request('GET', path)[:2], (200, expected))
        queue = self.request('GET', '/api/field-jobs')[1]
        self.assertEqual(queue['jobs'], {})
        self.assertEqual(queue['needs_inspection'][0]['object_id'], 'OBJ-206')
        for path in ('/data/field_log.json', '/data/scene_edits.json', '/data/decisions_cache.json',
                     '/data/.private', '/data/%66ield_log.json', '/data/escape.txt'):
            for method in ('GET', 'HEAD'):
                self.assertEqual(self.request(method, path)[0], 404, (method, path))
        self.assertEqual(self.request('GET', '/data/')[0], 403)

    def test_work_order_draft_is_stateless_and_no_other_visitor_can_read_notes(self):
        before = copy.deepcopy(server.WORK_ORDER_DRAFTS)
        first = self.request('POST', '/api/work-orders/draft', {'notes': 'visitor one'})
        second = self.request('POST', '/api/work-orders/draft', {'notes': 'visitor two'})
        self.assertEqual(first[0], 200)
        self.assertFalse(first[1]['persisted'])
        self.assertEqual(first[1]['draft']['status'], 'DEMO_DRAFT')
        self.assertEqual(second[1]['draft']['notes'], 'visitor two')
        self.assertEqual(self.request('GET', '/api/work-orders')[1], {'drafts': []})
        self.assertEqual(server.WORK_ORDER_DRAFTS, before)
        self.assertNotIn('notes', self.asset['diagnostics']['work_order_draft'])

    def test_copilot_is_local_bounded_and_preserves_citations(self):
        with mock.patch.object(server.decisions_client, 'decide', side_effect=AssertionError('external model')):
            status, answer, _ = self.request('POST', '/api/copilot/diagnose', {'question': 'How should I inspect?'})
        self.assertEqual(status, 200)
        self.assertEqual(answer['router'], 'keywords')
        self.assertEqual(answer['citations'], ['DOC-003'])
        self.assertEqual(self.request('POST', '/api/copilot/diagnose', {'question': 'x' * 2001})[0], 400)
        self.assertEqual(self.request('POST', '/api/copilot/diagnose', 'x' * (server.PUBLIC_BODY_LIMIT + 1))[0], 413)
        for body in ('{broken', '[]', {'question': []}, {'asset_id': {}}):
            self.assertEqual(self.request('POST', '/api/copilot/diagnose', body)[0], 400)
        with mock.patch.object(server, '_PUBLIC_WORK_SLOTS') as slots:
            slots.acquire.return_value = False
            self.assertEqual(self.request('POST', '/api/copilot/diagnose', {'question': 'inspect'})[0], 429)
            slots.release.assert_not_called()

    def test_existing_view_media_navigation_and_downloads_remain_available(self):
        self.assertEqual(self.request('GET', '/')[1], b'prepared viewer')
        self.assertEqual(self.request('GET', '/data/photo.jpg')[1], b'photo')
        status, body, headers = self.request('GET', '/data/clip.mp4', headers={'Range': 'bytes=1-3'})
        self.assertEqual((status, body, headers['Content-Range']), (206, b'123', 'bytes 1-3/10'))
        self.assertEqual(self.request('GET', '/api/navigation/route')[1]['waypoints'][0]['position'], [0, 0, 0])
        with mock.patch.object(server.asset_export, 'read_library', return_value=[]):
            self.assertEqual(self.request('GET', '/api/library')[1], {'saved': [], 'saving': {}})
            self.assertEqual(self.request('GET', '/api/library/export.zip')[0], 200)

    def test_normal_installed_mode_keeps_existing_edit_workflow(self):
        with mock.patch.object(server, 'PUBLIC_DEMO', False), \
             mock.patch.object(server, 'apply_edit', return_value=({'ok': True}, 200)) as edit:
            self.assertEqual(self.request('POST', '/api/scene/edits', {'action': 'remove', 'id': 'OBJ-004'})[:2],
                             (200, {'ok': True}))
            edit.assert_called_once_with({'action': 'remove', 'id': 'OBJ-004'})

    def test_public_hardware_gets_and_posts_never_import_optional_stores(self):
        original_import = builtins.__import__

        def reject_live(name, *args, **kwargs):
            if name == 'live' or name.startswith('live.'):
                raise AssertionError('Public requests must not import hardware/store modules')
            return original_import(name, *args, **kwargs)

        with mock.patch.object(builtins, '__import__', side_effect=reject_live):
            for method, path in [('GET', '/api/live/board'), ('GET', '/api/live/board/events'),
                                 ('POST', '/api/live/board/claim'), ('POST', '/api/live/board/heartbeat'),
                                 ('POST', '/api/live/board/action')]:
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.request(method, path, {} if method == 'POST' else None)[0], 403)

    def test_normal_installed_hardware_requests_keep_upstream_dispatch_and_token(self):
        live = types.ModuleType('live')
        live.board = types.SimpleNamespace(handle=mock.Mock(return_value=({'status': 'waiting'}, 200)))
        with mock.patch.object(server, 'PUBLIC_DEMO', False), mock.patch.dict(sys.modules, {'live': live}):
            self.assertEqual(self.request('GET', '/api/live/board')[:2], (200, {'status': 'waiting'}))
            live.board.handle.assert_called_with('GET', '/api/live/board', {}, server.FIELD, '')
            self.assertEqual(self.request('POST', '/api/live/board/heartbeat', {'touch': {'swing': 3}},
                {'X-Live-Token': 'fixture-token'})[0], 200)
            live.board.handle.assert_called_with('POST', '/api/live/board/heartbeat',
                {'touch': {'swing': 3}}, server.FIELD, 'fixture-token')

    def test_public_arduino_opt_in_requires_device_token_and_preserves_other_guards(self):
        from live import board
        field = server.field_service.FieldService({'assets': [self.asset]})
        token_file = self.data / 'device-token'
        token_file.write_text('fixture-device-' * 4)
        with mock.patch.object(server, 'PUBLIC_ARDUINO', True), mock.patch.object(server, 'PUBLIC_ASSISTANT', True), \
             mock.patch.object(server, 'FIELD', field), mock.patch.object(board, '_BOARD', None), \
             mock.patch.object(board.snowflake, 'write_later'), mock.patch.object(board.tiger, 'insert_heartbeat'), \
             mock.patch.object(server.field_service, 'LOG_PATH', self.data / 'arduino_test_jobs.json'), \
             mock.patch.dict(os.environ, {'LIVE_TOKEN_FILE': str(token_file)}):
            rt = self.request('GET', '/data/digital_twin.json')[1]['runtime']
            self.assertTrue(rt['arduino_enabled']); self.assertTrue(rt['arduino_actions_enabled'])
            status, state, _ = self.request('GET', '/api/live/board')
            self.assertEqual((status, state['status'], state['board_online']), (200, 'waiting', False))
            for path in ('/api/live/board/claim', '/api/live/board/heartbeat'):
                self.assertEqual(self.request('POST', path, {})[0], 401)
            self.assertEqual(self.request('GET', '/api/live/board')[1]['status'], 'waiting')
            status, state, _ = self.request('POST', '/api/live/board/heartbeat', {'touch': {'swing': 123, 'base': 22, 'touched': True}},
                {'X-Live-Token': token_file.read_text()})
            self.assertEqual(status, 200); self.assertTrue(state['board_online'])
            self.assertEqual(state['touch']['swing'], 123)
            self.assertEqual(self.request('POST', '/api/live/board/action', {'action': 'ack'})[0], 200)
            self.assertEqual(self.request('GET', '/api/live/board')[1]['status'], 'in_progress')
            self.assertEqual(self.request('POST', '/api/live/board/heartbeat/extra', {})[0], 403)
            for path in ('/api/scene/edits', '/api/library'):
                self.assertEqual(self.request('POST', path, {})[0], 403)
            self.assertEqual(self.request('GET', '/data/')[0], 403)
            token_file.unlink()
            self.assertEqual(self.request('GET', '/api/live/board')[0], 503)


if __name__ == '__main__':
    unittest.main()
