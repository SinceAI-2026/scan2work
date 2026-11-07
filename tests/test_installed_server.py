"""Installed launch settings preserve data files and original server defaults."""
import copy
import json
import os
import threading
import unittest
from unittest import mock
from urllib.request import urlopen

os.environ['VALMET_OFFLINE'] = '1'
import server


class InstalledServerTests(unittest.TestCase):
    def test_runtime_candidate_url_is_only_a_served_manifest_override(self):
        twin = {'assets': [], 'proposal_review': {'count': 1933, 'full_view_url': None}}
        original = copy.deepcopy(twin)
        with mock.patch.object(server, 'TWIN_DATA', twin):
            http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.TwinRequestHandler)
            thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
            try:
                for value in ('http://127.0.0.1:8772/', ''):
                    with mock.patch.dict(os.environ, {'VALMET_PROPOSAL_VIEW_URL': value}):
                        with urlopen(f'http://127.0.0.1:{http.server_port}/data/digital_twin.json') as response:
                            actual = json.load(response)
                    self.assertEqual(actual['proposal_review']['full_view_url'], value or None)
                    self.assertEqual(twin, original)
            finally:
                http.shutdown(); http.server_close(); thread.join()

    def test_bind_host_default_and_explicit_override(self):
        for override, expected in ((None, '127.0.0.1'), ('0.0.0.0', '0.0.0.0')):
            env = {} if override is None else {'VALMET_BIND_HOST': override}
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(server, 'load_data'), \
                 mock.patch.object(server, 'ThreadingHTTPServer') as http:
                http.return_value.server_address = (expected, 12345)
                server.run_server(0)
                http.assert_called_once_with((expected, 0), server.TwinRequestHandler)
                http.return_value.serve_forever.assert_called_once()

    def test_lan_argument_retained_but_public_preview_stays_loopback(self):
        for public, expected in ((False, '0.0.0.0'), (True, '127.0.0.1')):
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(server, 'PUBLIC_DEMO', public), \
                 mock.patch.object(server, 'load_data'), \
                 mock.patch.object(server, 'ThreadingHTTPServer') as http:
                http.return_value.server_address = (expected, 12345)
                server.run_server(0, '0.0.0.0')
                http.assert_called_once_with((expected, 0), server.TwinRequestHandler)


if __name__ == '__main__':
    unittest.main()
