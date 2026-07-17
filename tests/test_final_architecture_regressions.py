from __future__ import annotations

import gc
import http.client
import inspect
import json
import logging

import tempfile
import threading
import time
import unittest
import urllib.parse

import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import moku_app
import server


class FinalArchitectureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def raw_request(self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw)

    def authorized_headers(self, **extra):
        return {
            "Host": f"127.0.0.1:{self.httpd.server_port}",
            "Origin": self.base,
            "Content-Type": "application/json",
            "X-MOKU-Request-Token": server.REQUEST_TOKEN,
            **extra,
        }

    def test_all_api_gets_reject_dns_rebinding_host_and_cross_site_origin(self):
        for headers in (
            {"Host": "attacker.example"},
            {"Host": f"127.0.0.1:{self.httpd.server_port}", "Origin": "https://attacker.example"},
            {"Host": f"127.0.0.1:{self.httpd.server_port}", "Sec-Fetch-Site": "cross-site"},
        ):
            status, body = self.raw_request("GET", "/api/health", headers=headers)
            self.assertEqual(status, 403)
            self.assertIn("本机", body["error"])
            self.assertNotIn("requestToken", body)

    def test_health_is_the_only_headerless_api_get(self):
        host = {
            "Host": f"127.0.0.1:{self.httpd.server_port}",
            "Sec-Fetch-Site": "same-origin",
        }
        status, health = self.raw_request("GET", "/api/health", headers=host)
        self.assertEqual(status, 200)
        self.assertEqual(health["requestToken"], server.REQUEST_TOKEN)

        status, body = self.raw_request("GET", "/api/status", headers=host)
        self.assertEqual(status, 403)
        self.assertIn("令牌", body["error"])

        status, body = self.raw_request(
            "GET",
            "/api/status",
            headers={**host, "X-MOKU-Request-Token": server.REQUEST_TOKEN},
        )
        self.assertEqual(status, 200)
        self.assertIn("loggedIn", body)

    def test_json_body_requires_valid_bounded_object_length(self):
        cases = [
            ({"Content-Length": "-1"}, b"{}", 400),
            ({"Content-Length": "not-a-number"}, b"{}", 400),
            ({"Content-Length": "70000"}, b"{}", 413),
        ]
        for extra, body, expected in cases:
            status, payload = self.raw_request(
                "POST", "/api/pixiv/batch-download", body=body,
                headers=self.authorized_headers(**extra),
            )
            self.assertEqual(status, expected, (extra, payload))
            self.assertIn("error", payload)

        array_body = b"[]"
        status, payload = self.raw_request(
            "POST", "/api/pixiv/download", body=array_body,
            headers=self.authorized_headers(**{"Content-Length": str(len(array_body))}),
        )
        self.assertEqual(status, 400)
        self.assertIn("对象", payload["error"])

    def test_http_logs_never_include_query_parameters_or_tokens(self):
        with self.assertLogs("moku.http", level=logging.INFO) as captured:
            status, _body = self.raw_request(
                "GET", "/api/pixiv/image?token=top-secret-preview-token",
                headers={"Host": f"127.0.0.1:{self.httpd.server_port}"},
            )
        self.assertEqual(status, 403)
        rendered = "\n".join(captured.output)
        self.assertIn("GET /api/pixiv/image 403", rendered)
        self.assertNotIn("top-secret-preview-token", rendered)
        self.assertNotIn("?token=", rendered)

    def test_inflight_r18_image_is_revoked_before_response_after_logout(self):
        token_snapshot = dict(server.IMAGE_TOKENS)
        try:
            token = "inflight-r18-token"
            approved = (
                time.time() + 60,
                "77",
                "https://i.pximg.net/inflight.jpg",
                "r18",
            )
            server.IMAGE_TOKENS.clear()
            server.IMAGE_TOKENS[token] = approved

            def logout_while_reading(*_args, **_kwargs):
                server.clear_authorized_state()
                return b"image-bytes", "image/jpeg"

            with patch.object(server, "validated_session", return_value=True), patch.object(
                server, "pixiv_request", side_effect=logout_while_reading
            ):
                status, body = self.raw_request(
                    "GET",
                    f"/api/pixiv/image?token={token}",
                    headers={"Host": f"127.0.0.1:{self.httpd.server_port}"},
                )
            self.assertEqual(status, 403)
            self.assertIn("失效", body["error"])
        finally:
            server.IMAGE_TOKENS.clear()
            server.IMAGE_TOKENS.update(token_snapshot)

    def test_transport_exception_is_returned_as_structured_502(self):
        with patch.object(server, "search_pixiv_results", side_effect=http.client.RemoteDisconnected("closed")):
            status, body = self.raw_request(
                "GET", "/api/pixiv/search?tag=cat",
                headers={
                    "Host": f"127.0.0.1:{self.httpd.server_port}",
                    "Origin": self.base,
                    "X-MOKU-Request-Token": server.REQUEST_TOKEN,
                },
            )
        self.assertEqual(status, 502)
        self.assertIn("Pixiv", body["error"])
        self.assertNotIn("closed", body["error"])

    def test_active_search_lock_survives_session_drop_and_cleans_up_when_idle(self):
        key = (("cat",), "safe", "all", True)
        lock = server.search_session_lock(key)
        reference = weakref.ref(lock)
        with lock:
            server._drop_search_session(key)
            self.assertIs(server.search_session_lock(key), lock)
        del lock
        gc.collect()
        self.assertIsNone(reference())

    def test_active_search_session_is_not_evicted_until_request_releases_its_lock(self):
        snapshot_limit = server.MAX_SEARCH_SESSIONS
        snapshot_sessions = list(server.SEARCH_SESSIONS.items())
        try:
            server.MAX_SEARCH_SESSIONS = 1
            server.SEARCH_SESSIONS.clear()
            active_key = (("active",), "safe", "all", True)
            other_key = (("other",), "safe", "all", True)
            active_lock = server.search_session_lock(active_key)
            with active_lock:
                active = server._touch_search_session(active_key)
                active["items"].append({"id": "1"})
                server._touch_search_session(other_key)
                self.assertIs(server.SEARCH_SESSIONS.get(active_key), active)
                self.assertIn(active_key, server.SEARCH_SESSIONS)
            del active_lock
            gc.collect()
            server._touch_search_session(other_key)
            self.assertLessEqual(len(server.SEARCH_SESSIONS), 1)
        finally:
            server.MAX_SEARCH_SESSIONS = snapshot_limit
            server.SEARCH_SESSIONS.clear()
            server.SEARCH_SESSIONS.update(snapshot_sessions)

    def test_fixture_routes_are_disabled_unless_explicitly_enabled(self):
        with patch.object(server, "TEST_FIXTURES_ENABLED", False):
            status, body = self.raw_request(
                "GET", "/api/search?tag=fixture",
                headers={
                    "Host": f"127.0.0.1:{self.httpd.server_port}",
                    "X-MOKU-Request-Token": server.REQUEST_TOKEN,
                },
            )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not found")

    def test_fixture_helpers_work_only_after_explicit_opt_in(self):
        with patch.object(server, "TEST_FIXTURES_ENABLED", True):
            rows = server.fixture_records("fixture")
            image = server.fixture_artwork_svg(0, 0, "preview")
        self.assertEqual(len(rows), 24)
        self.assertIn("fixture", rows[0]["tags"])
        self.assertTrue(image.startswith(b"<svg"))

    def test_pixiv_cache_is_bounded_lru(self):
        old = server.MAX_PIXIV_CACHE_ITEMS
        snapshot = list(server.PIXIV_CACHE.items())
        try:
            server.MAX_PIXIV_CACHE_ITEMS = 2
            server.PIXIV_CACHE.clear()
            server.cache_pixiv_item({"id": "1", "restriction": "safe"})
            server.cache_pixiv_item({"id": "2", "restriction": "safe"})
            self.assertEqual(server.get_cached_pixiv_item("1")["id"], "1")
            server.cache_pixiv_item({"id": "3", "restriction": "safe"})
            self.assertEqual(list(server.PIXIV_CACHE), ["1", "3"])
        finally:
            server.MAX_PIXIV_CACHE_ITEMS = old
            server.PIXIV_CACHE.clear()
            server.PIXIV_CACHE.update(snapshot)

    def test_shared_pixiv_state_survives_concurrent_cache_and_token_mutations(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn("PIXIV_STATE_LOCK = threading.RLock()", source)
        for function in (
            server.cache_pixiv_item,
            server.get_cached_pixiv_item,
            server.prune_image_tokens,
            server.prune_search_image_tokens,
            server.authorize_image_proxy,
            server.approved_image_url,
            server.clear_authorized_state,
        ):
            self.assertIn("PIXIV_STATE_LOCK", inspect.getsource(function))

        old_limit = server.MAX_PIXIV_CACHE_ITEMS
        cache_snapshot = list(server.PIXIV_CACHE.items())
        token_snapshot = dict(server.IMAGE_TOKENS)
        try:
            server.MAX_PIXIV_CACHE_ITEMS = 32
            server.PIXIV_CACHE.clear()
            server.IMAGE_TOKENS.clear()

            def mutate(worker: int) -> None:
                for index in range(120):
                    artwork_id = str(worker * 1000 + index)
                    server.cache_pixiv_item({"id": artwork_id, "restriction": "safe"})
                    server.get_cached_pixiv_item(artwork_id)
                    server.authorize_image_proxy(
                        "/api/pixiv/image?url=" + urllib.parse.quote(
                            f"https://i.pximg.net/{artwork_id}.jpg", safe=""
                        ),
                        artwork_id,
                        "safe",
                    )
                    if index % 9 == 0:
                        server.prune_image_tokens(now=time.time())

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(mutate, range(8)))

            self.assertLessEqual(len(server.PIXIV_CACHE), server.MAX_PIXIV_CACHE_ITEMS)
            self.assertLessEqual(len(server.IMAGE_TOKENS), server.MAX_IMAGE_TOKENS)
        finally:
            server.MAX_PIXIV_CACHE_ITEMS = old_limit
            server.PIXIV_CACHE.clear()
            server.PIXIV_CACHE.update(cache_snapshot)
            server.IMAGE_TOKENS.clear()
            server.IMAGE_TOKENS.update(token_snapshot)

    def test_expired_cached_image_authorization_refreshes_before_download(self):
        item = {
            "id": "9", "restriction": "safe", "pageImages": [{
                "regular": "/api/pixiv/image?token=expired",
                "original": "/api/pixiv/image?token=expired",
            }],
        }
        old_cache = list(server.PIXIV_CACHE.items())
        old_tokens = dict(server.IMAGE_TOKENS)
        try:
            server.PIXIV_CACHE.clear()
            server.IMAGE_TOKENS.clear()
            server.cache_pixiv_item(item)
            server.IMAGE_TOKENS["expired"] = (time.time() - 1, "9", "https://i.pximg.net/old.jpg", "safe")
            fresh = {**item, "pageImages": [{
                "regular": "/api/pixiv/image?token=fresh",
                "original": "/api/pixiv/image?token=fresh",
            }]}
            with patch.object(server, "pixiv_detail", return_value=fresh) as refresh:
                result = server.pixiv_item_for_download("9", allow_r18=False)
            self.assertEqual(result, fresh)
            refresh.assert_called_once_with("9", allow_r18=False)
        finally:
            server.PIXIV_CACHE.clear()
            server.PIXIV_CACHE.update(old_cache)
            server.IMAGE_TOKENS.clear()
            server.IMAGE_TOKENS.update(old_tokens)

    def test_code_generation_changes_with_source_and_frozen_executable_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a.py").write_text("one", encoding="utf-8")
            first = server.compute_code_generation(root=root, files=("a.py",), frozen=False)
            (root / "a.py").write_text("two", encoding="utf-8")
            second = server.compute_code_generation(root=root, files=("a.py",), frozen=False)
            self.assertNotEqual(first, second)
            exe = root / "MOKU.exe"
            exe.write_bytes(b"exe-one")
            frozen_first = server.compute_code_generation(executable=exe, frozen=True)
            exe.write_bytes(b"exe-two")
            frozen_second = server.compute_code_generation(executable=exe, frozen=True)
            self.assertNotEqual(frozen_first, frozen_second)

    def test_product_startup_does_not_require_external_edge_executable(self):
        source = Path(moku_app.__file__).read_text(encoding="utf-8")
        self.assertNotIn("def find_edge", source)
        self.assertNotIn("--app=", source)
        self.assertNotIn("subprocess.Popen", source)


if __name__ == "__main__":
    unittest.main()
