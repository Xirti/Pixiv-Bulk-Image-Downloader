from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import server


class WindowedServerTests(unittest.TestCase):
    def test_get_request_survives_when_stderr_is_unavailable(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(sys, "stderr", None):
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{httpd.server_port}/api/health", timeout=2
                ) as response:
                    payload = json.loads(response.read())
            self.assertTrue(payload["ok"])
            self.assertNotIn("requestToken", payload)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
