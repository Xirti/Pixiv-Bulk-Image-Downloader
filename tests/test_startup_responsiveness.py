import http.client
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

import moku_app
import server


class _LocalResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _RemoteResponse:
    def __init__(self, url, payload=b'{}', fail_read=False):
        self._url = url
        self._payload = payload
        self._fail_read = fail_read
        self.headers = self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self._url

    def get_content_type(self):
        return "application/json"

    def get(self, name, default=None):
        return default

    def read(self, _limit):
        if self._fail_read:
            raise http.client.IncompleteRead(b"partial", 20)
        return self._payload


class StartupResponsivenessTests(unittest.TestCase):
    def test_local_server_threads_never_block_desktop_shutdown(self):
        self.assertTrue(server.LocalThreadingHTTPServer.daemon_threads)
        self.assertFalse(server.LocalThreadingHTTPServer.block_on_close)

    def test_wait_ready_uses_health_and_static_assets_without_pixiv_side_effects(self):
        urls = []
        opener = Mock()

        def open_local(url, timeout):
            urls.append((url, timeout))
            return _LocalResponse()

        opener.open.side_effect = open_local
        with patch.object(
            moku_app, "read_json", return_value={"instanceId": "expected-instance"}
        ) as read_json, patch.object(moku_app, "_local_opener", return_value=opener), patch.object(
            server, "validated_session", side_effect=AssertionError("auth must not run")
        ):
            moku_app.wait_ready("http://127.0.0.1:45678/", "expected-instance", timeout=1)

        read_json.assert_called_once_with(
            "http://127.0.0.1:45678/api/health", same_origin=True,
        )
        self.assertEqual(urls, [
            ("http://127.0.0.1:45678/", 3),
            ("http://127.0.0.1:45678/style.css", 3),
            ("http://127.0.0.1:45678/app.js", 3),
        ])

    def test_health_endpoint_never_validates_remote_session(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "validated_session", side_effect=AssertionError("remote auth touched")):
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{httpd.server_port}/api/health", timeout=2
                ) as response:
                    data = json.loads(response.read())
            self.assertTrue(data["ok"])
            self.assertEqual(data["instanceId"], server.INSTANCE_ID)
            self.assertEqual(data["protocolVersion"], server.PROTOCOL_VERSION)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_pixiv_request_retries_incomplete_response_body(self):
        url = "https://www.pixiv.net/ajax/test"
        responses = [
            _RemoteResponse(url, fail_read=True),
            _RemoteResponse(url, payload=b'{"ok":true}'),
        ]

        class Opener:
            calls = 0

            def open(self, _request, timeout):
                self.calls += 1
                return responses.pop(0)

        opener = Opener()
        with patch.object(server, "PIXIV_OPENER", opener), patch.object(
            server, "session_cookie_header", return_value={}
        ), patch.object(server.time, "sleep", return_value=None):
            raw, content_type = server.pixiv_request(url)

        self.assertEqual(raw, b'{"ok":true}')
        self.assertEqual(content_type, "application/json")
        self.assertEqual(opener.calls, 2)


if __name__ == "__main__":
    unittest.main()
