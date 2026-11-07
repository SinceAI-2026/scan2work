"""Range streaming follows the active dataset without serving an old scene's clip."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ['VALMET_OFFLINE'] = '1'
import server


class MediaRangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.web = root / 'web'; (self.web / 'data').mkdir(parents=True)
        self.data = root / 'active'; self.data.mkdir()
        self.payload = b'0123456789'
        (self.data / 'clip.mp4').write_bytes(self.payload)
        (self.web / 'data' / 'clip.mp4').write_bytes(b'WRONG-DATASET')
        (self.web / 'data' / 'fallback.webm').write_bytes(b'FALLBACK-ONLY')
        (self.web / 'private.mp4').write_bytes(b'OUTSIDE-ACTIVE-DATA')
        (self.data / 'linked.mp4').symlink_to(self.web / 'private.mp4')
        for patch in (mock.patch.object(server, 'WEB_DIR', self.web),
                      mock.patch.object(server.C, 'OUT_DIR', self.data)):
            patch.start(); self.addCleanup(patch.stop)
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.TwinRequestHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join()

    def get(self, path='/data/clip.mp4', byte_range=None):
        req = Request(f'http://127.0.0.1:{self.http.server_port}{path}',
                      headers={'Range': byte_range} if byte_range is not None else {})
        try:
            response = urlopen(req)
        except HTTPError as response:
            return response.code, response.headers, response.read()
        with response:
            return response.status, response.headers, response.read()

    def test_whole_media_uses_active_dataset_and_does_not_fall_back(self):
        status, headers, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertEqual(headers['Content-Length'], '10')
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(headers['Content-Type'], 'video/mp4')
        self.assertEqual(self.get('/data/fallback.webm')[0], 404)

    def test_prefix_closed_and_open_ended_ranges(self):
        for range_value, expected, content_range in (
            ('bytes=0-1', b'01', 'bytes 0-1/10'),
            ('bytes=1-', b'123456789', 'bytes 1-9/10'),
            ('bytes=9-9', b'9', 'bytes 9-9/10'),
            ('bytes=8-100', b'89', 'bytes 8-9/10'),
        ):
            with self.subTest(range_value=range_value):
                status, headers, body = self.get(byte_range=range_value)
                self.assertEqual(status, 206)
                self.assertEqual(body, expected)
                self.assertEqual(headers['Content-Range'], content_range)
                self.assertEqual(headers['Content-Length'], str(len(expected)))

    def test_suffix_range(self):
        status, headers, body = self.get(byte_range='bytes=-3')
        self.assertEqual(status, 206)
        self.assertEqual(body, b'789')
        self.assertEqual(headers['Content-Range'], 'bytes 7-9/10')

    def test_unsatisfiable_ranges_return_416_without_body(self):
        for range_value in ('bytes=10-', 'bytes=5-1', 'bytes=-0'):
            with self.subTest(range_value=range_value):
                status, headers, body = self.get(byte_range=range_value)
                self.assertEqual(status, 416)
                self.assertEqual(headers['Content-Range'], 'bytes */10')
                self.assertEqual(headers['Content-Length'], '0')
                self.assertEqual(body, b'')

    def test_malformed_range_keeps_upstream_ignore_behavior(self):
        status, headers, body = self.get(byte_range='bytes=not-a-range')
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertIsNone(headers.get('Content-Range'))

    def test_encoded_traversal_and_symlink_escape_are_not_served(self):
        for path in ('/data/%2e%2e/private.mp4', '/data/../private.mp4', '/data/linked.mp4'):
            with self.subTest(path=path):
                status, _, body = self.get(path, 'bytes=0-1')
                self.assertEqual(status, 404)
                self.assertNotIn(b'OUTSIDE-ACTIVE-DATA', body)


if __name__ == '__main__':
    unittest.main()
