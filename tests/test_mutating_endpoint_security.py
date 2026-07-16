from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

import server


class MutatingEndpointSecurityTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def post(self, path: str, payload: dict, *, origin: str | None, token: str | None, content_type: str = "application/json"):
        headers = {"Content-Type": content_type}
        if origin is not None:
            headers["Origin"] = origin
        if token is not None:
            headers["X-MOKU-Request-Token"] = token
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_logout_rejects_cross_site_request_before_side_effect(self):
        with patch.object(server, "disconnect_authorized_session", side_effect=AssertionError("must not log out")):
            status, body = self.post(
                "/api/auth/logout", {}, origin="https://evil.example", token=None,
            )
        self.assertEqual(status, 403)
        self.assertIn("同源", body["error"])

    def test_logout_rejects_missing_token_even_from_loopback(self):
        with patch.object(server, "disconnect_authorized_session", side_effect=AssertionError("must not log out")):
            status, body = self.post(
                "/api/auth/logout", {}, origin=self.base, token=None,
            )
        self.assertEqual(status, 403)
        self.assertIn("授权", body["error"])

    def test_valid_logout_uses_local_side_effect(self):
        with patch.object(server, "disconnect_authorized_session") as logout:
            status, body = self.post(
                "/api/auth/logout", {}, origin=self.base, token=server.REQUEST_TOKEN,
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        logout.assert_called_once_with()

    def test_mutating_endpoint_rejects_non_json_even_with_token(self):
        with patch.object(server, "disconnect_authorized_session", side_effect=AssertionError("must not log out")):
            status, body = self.post(
                "/api/auth/logout", {}, origin=self.base,
                token=server.REQUEST_TOKEN, content_type="text/plain",
            )
        self.assertEqual(status, 415)
        self.assertIn("application/json", body["error"])

    def test_removed_http_login_endpoint_returns_not_found_without_side_effect(self):
        status, body = self.post(
            "/api/auth/login", {"remember": False},
            origin=self.base, token=server.REQUEST_TOKEN,
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not found")


if __name__ == "__main__":
    unittest.main()
