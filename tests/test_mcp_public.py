"""Public MCP authentication and reading-only tools, without model or store calls."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

os.environ['VALMET_OFFLINE'] = '1'
import mcp_server as M
from pipeline import field_service as FS
from pipeline.runtime_policy import configure_server_runtime

TOKEN = 'test-only-' * 5
ASSET = {'id': 'OBJ-206', 'asset_id': 'ASSET-003', 'tag': 'CV-003', 'mapped': True,
         'asset_class': 'control_valve', 'label': 'Control valve', 'condition_state': 'degraded',
         'issue_code': 'POSITION_DEVIATION', 'issue_summary': 'Synthetic position deviation',
         'diagnostics': {'verification_steps': [{'step': 1, 'title': 'Verify identity',
             'detail': 'Check device identity', 'citation': 'DOC-003'}]}}


def message(method, params=None, request_id=1):
    return {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params or {}}


class MCPHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.log = self.folder / 'field_log.json'
        self.log.write_text('{"jobs":{"ASSET-003":{"notes":["other visitor"]}}}')
        self.original = self.log.read_bytes()
        for patch in (mock.patch.object(M, 'PUBLIC_MCP', True), mock.patch.object(M, 'TOKEN', TOKEN),
                      mock.patch.object(M, 'PUBLIC_ASSISTANT', False),
                      mock.patch.object(M, 'SERVICE', FS.ReadOnlyFieldService({'assets': [ASSET]})),
                      mock.patch.object(FS, 'LOG_PATH', self.log)):
            patch.start(); self.addCleanup(patch.stop)
        self.http = M.ThreadingHTTPServer(('127.0.0.1', 0), M.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()
        self.assertEqual(self.log.read_bytes(), self.original)
        self.assertFalse(self.log.with_name('field_log.json.lock').exists())

    def request(self, payload, token=TOKEN, headers=None):
        h = {'Content-Type': 'application/json', **(headers or {})}
        if token is not None: h['Authorization'] = 'Bearer ' + token
        raw = json.dumps(payload) if not isinstance(payload, bytes) else payload
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        conn.request('POST', '/mcp', body=raw, headers=h)
        response = conn.getresponse(); body = response.read(); status = response.status
        conn.close()
        return status, json.loads(body) if body else None

    def test_public_requires_bearer_even_on_loopback(self):
        for token in (None, 'wrong', 'é' * 40):
            self.assertEqual(self.request(message('tools/list'), token=token)[0], 401)
        self.assertEqual(self.request(message('tools/list'), token=None, headers={'Authorization': TOKEN})[0], 401)
        self.assertEqual(self.request(message('tools/list'))[0], 200)

    def test_public_lists_exactly_six_reading_tools_and_instructions(self):
        result = self.request(message('tools/list'))[1]['result']
        self.assertEqual({t['name'] for t in result['tools']}, FS.READ_ONLY)
        self.assertEqual(len(result['tools']), 6)
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in result['tools']))
        instructions = self.request(message('initialize'))[1]['result']['instructions']
        self.assertIn('read-only', instructions)
        self.assertIn('synthetic', instructions)

    def test_semantic_write_calls_are_denied_before_any_dispatch(self):
        with mock.patch.object(FS, 'call', side_effect=AssertionError('write dispatch')):
            for name in ('start_job', 'complete_check', 'add_note', 'close_job'):
                status, doc = self.request(message('tools/call', {'name': name, 'arguments': {}}))
                self.assertEqual(status, 200)
                self.assertTrue(doc['result']['isError'])

    def test_reads_cite_prepared_data_and_never_return_private_job_log(self):
        call = lambda name: self.request(message('tools/call', {'name': name, 'arguments': {'equipment': 'CV3'}}))[1]['result']
        procedure = json.loads(call('get_procedure')['content'][0]['text'])
        self.assertEqual(procedure['steps'][0]['citation'], 'DOC-003')
        job = json.loads(call('get_job')['content'][0]['text'])
        self.assertIsNone(job['job'])
        self.assertIn('read-only', job['note'])
        self.assertNotIn('other visitor', json.dumps(job))
        with self.assertRaises(ValueError): M.SERVICE.start_job('CV3')

    def test_invalid_protocol_objects_and_malformed_json_are_400(self):
        for payload in (None, [], 'text', 123, {'jsonrpc': '2.0'},
                        {'jsonrpc': '2.0', 'method': 'ping', 'params': []},
                        {'jsonrpc': '2.0', 'method': 'ping', 'id': {}}):
            self.assertEqual(self.request(payload)[0], 400, payload)
        self.assertEqual(self.request(b'{broken')[1]['error']['code'], -32700)

    def test_request_batch_and_argument_limits(self):
        self.assertEqual(self.request(b'x' * (M.MAX_PUBLIC_BODY + 1))[0], 413)
        self.assertEqual(self.request([message('ping')] * 9)[0], 413)
        self.assertEqual(self.request([message('ping', request_id=1), message('ping', request_id=2)])[0], 200)
        payload = message('tools/call', {'name': 'find_equipment', 'arguments': {'query': 'x' * 2001}})
        self.assertTrue(self.request(payload)[1]['result']['isError'])
        self.assertEqual(self.request(message('ping'), headers={'Content-Length': '-1'})[0], 400)

    def test_notifications_and_busy_limit(self):
        self.assertEqual(self.request({'jsonrpc': '2.0', 'method': 'notifications/initialized'}), (202, None))
        with mock.patch.object(M, '_PUBLIC_SLOTS') as slots:
            slots.acquire.return_value = False
            self.assertEqual(self.request(message('ping'))[0], 429)
            slots.release.assert_not_called()

    def test_normal_local_mcp_retains_all_tools_and_legacy_auth(self):
        with mock.patch.object(M, 'PUBLIC_MCP', False), mock.patch.object(M, 'TOKEN', ''):
            status, doc = self.request(message('tools/list'), token=None)
            self.assertEqual(status, 200)
            self.assertEqual(len(doc['result']['tools']), 10)
            self.assertEqual(self.request(message('ping'), token=None, headers={'X-Forwarded-For': '192.0.2.1'})[0], 401)
        with mock.patch.object(M, 'PUBLIC_MCP', False), mock.patch.object(FS, 'call', return_value={'ok': True}) as dispatch:
            result = self.request(message('tools/call', {'name': 'start_job', 'arguments': {'equipment': 'CV3'}}))[1]
            self.assertFalse(result['result']['isError'])
            dispatch.assert_called_once()

    def test_explicit_shared_assistant_retains_ten_tools_auth_and_bounded_writes(self):
        with mock.patch.object(M, 'PUBLIC_ASSISTANT', True), \
             mock.patch.object(FS, 'call', return_value={'ok': True}) as dispatch:
            self.assertEqual(self.request(message('tools/list'), token=None)[0], 401)
            tools = self.request(message('tools/list'))[1]['result']['tools']
            self.assertEqual(len(tools), 10)
            self.assertIn('shared demonstration job log', self.request(message('initialize'))[1]['result']['instructions'])
            args = {'equipment': 'CV3', 'text': 'x' * 1000}
            result = self.request(message('tools/call', {'name': 'add_note', 'arguments': args}))[1]['result']
            self.assertFalse(result['isError'])
            dispatch.assert_called_once_with(M.SERVICE, 'add_note', args)
            dispatch.reset_mock()
            args['text'] += 'x'
            self.assertTrue(self.request(message('tools/call', {'name': 'add_note', 'arguments': args}))[1]['result']['isError'])
            dispatch.assert_not_called()
            self.assertEqual(self.request(b'x' * (M.MAX_PUBLIC_BODY + 1))[0], 413)


class MCPStartupTests(unittest.TestCase):
    def test_public_policy_precedes_configuration(self):
        env = {'FIELD_ATLAS_MCP_PUBLIC': '1', 'VALMET_OFFLINE': '0'}
        with mock.patch.object(Path, 'read_text', side_effect=AssertionError('No credentials/scene needed')):
            self.assertTrue(configure_server_runtime(Path('/unused'), env))
        self.assertEqual(env['VALMET_OFFLINE'], '1')

    def test_public_startup_requires_strong_token_before_loading_data(self):
        with mock.patch.object(M, 'PUBLIC_MCP', True), mock.patch.object(M, 'TOKEN', ''), \
             mock.patch.object(Path, 'read_text', side_effect=AssertionError('must fail before data')):
            with self.assertRaisesRegex(ValueError, 'bearer token'): M.main(0)

    def test_public_startup_uses_local_retrieval_readonly_service_and_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'digital_twin.json').write_text(json.dumps({'assets':[ASSET]}))
            with mock.patch.object(M, 'PUBLIC_MCP', True), mock.patch.object(M, 'TOKEN', TOKEN), \
                 mock.patch.object(M, 'PUBLIC_ASSISTANT', False), \
                 mock.patch.object(M.C, 'OUT_DIR', root), mock.patch.object(M.C, 'OFFLINE', False), \
                 mock.patch.object(M.retrieval, 'load_saved', return_value=None) as load, \
                 mock.patch.object(M, 'ThreadingHTTPServer') as http, mock.patch.object(M, 'SERVICE', None):
                http.return_value.server_address=('127.0.0.1',12345)
                M.main(0)
                self.assertIsInstance(M.SERVICE, FS.ReadOnlyFieldService)
                load.assert_called_once_with(try_gemma=False)
                http.assert_called_once_with(('127.0.0.1',0),M.Handler)

    def test_shared_assistant_startup_uses_normal_job_service_but_no_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'digital_twin.json').write_text(json.dumps({'assets':[ASSET]}))
            with mock.patch.object(M, 'PUBLIC_MCP', True), mock.patch.object(M, 'PUBLIC_ASSISTANT', True), \
                 mock.patch.object(M, 'TOKEN', TOKEN), mock.patch.object(M.C, 'OUT_DIR', root), \
                 mock.patch.object(M.C, 'OFFLINE', False), mock.patch.object(M.retrieval, 'load_saved', return_value=None) as load, \
                 mock.patch.object(M, 'ThreadingHTTPServer') as http, mock.patch.object(M, 'SERVICE', None):
                http.return_value.server_address=('127.0.0.1',12345)
                M.main(0)
                self.assertIs(type(M.SERVICE), FS.FieldService)
                load.assert_called_once_with(try_gemma=False)


if __name__ == '__main__': unittest.main()
