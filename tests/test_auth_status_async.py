from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

import server


class AsyncAuthStatusTests(unittest.TestCase):
    def test_frontend_has_no_remote_checking_state_that_can_overwrite_local_login(self):
        source = (server.WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('data.authState === "checking"', source)
        self.assertNotIn("PIXIV CHECKING", source)
        self.assertNotIn("正在向 Pixiv 验证", source)

    def test_status_returns_authorized_without_pixiv_network(self):
        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "session_cookie_header", return_value={"Cookie": "PHPSESSID=validvalue"}), patch.object(
                server, "pixiv_request", side_effect=AssertionError("remote probe must not run")
            ):
                before = time.monotonic()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/status",
                    headers={"X-MOKU-Request-Token": server.REQUEST_TOKEN},
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    body = json.loads(response.read())
                elapsed = time.monotonic() - before
                self.assertLess(elapsed, 1.0)
                self.assertEqual(body["authState"], "authorized")
                self.assertTrue(body["loggedIn"])
                self.assertTrue(body["sessionPresent"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()