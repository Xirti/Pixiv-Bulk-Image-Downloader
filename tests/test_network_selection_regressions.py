import json
import os
import socket
import time
import threading
import urllib.request
import urllib.parse
import unittest

from unittest.mock import patch

import server


class NetworkSelectionRegressionTests(unittest.TestCase):
    def setUp(self):
        server.PIXIV_PROXY = ""
        server.PIXIV_NETWORK_FINGERPRINT = None
        server.PIXIV_NETWORK_CHECKED_AT = 0.0
        server.PIXIV_OPENER = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), server.NoRedirectHandler,
        )

    def test_disabled_stored_proxy_is_not_activated_by_tcp_listener(self):
        state = {
            "mode": "direct-or-tun",
            "proxyEnabled": False,
            "proxyServer": "",
            "proxyStored": "127.0.0.1:7890",
            "pac": "",
            "environmentProxy": False,
        }
        with patch.object(server, "windows_proxy_state", return_value=state), \
             patch.object(socket, "create_connection", side_effect=AssertionError("disabled proxy must not be probed")), \
             patch.dict(os.environ, {"HTTPS_PROXY": "", "https_proxy": ""}, clear=False):
            self.assertEqual(server.refresh_network_opener(), "")
            self.assertEqual(server.PIXIV_PROXY, "")


    def test_rejected_environment_proxy_cannot_be_reintroduced_by_urllib_defaults(self):
        state = {
            "mode": "manual-env",
            "proxyEnabled": False,
            "proxyServer": "",
            "proxyStored": "",
            "pac": "",
            "environmentProxy": True,
        }
        with patch.object(server, "windows_proxy_state", return_value=state), \
             patch.dict(os.environ, {
                 "HTTPS_PROXY": "http://remote.example:8080",
                 "https_proxy": "",
                 "HTTP_PROXY": "http://remote.example:8080",
                 "ALL_PROXY": "http://remote.example:8080",
             }, clear=False):
            self.assertEqual(server.refresh_network_opener(), "")

        proxy_handlers = [
            handler for handler in server.PIXIV_OPENER.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(proxy_handlers, [])
        self.assertEqual(server.PIXIV_PROXY, "")


    def test_accepted_loopback_proxy_is_the_only_proxy_in_the_opener(self):
        state = {
            "mode": "system-proxy",
            "proxyEnabled": True,
            "proxyServer": "127.0.0.1:7890",
            "proxyStored": "127.0.0.1:7890",
            "pac": "",
            "environmentProxy": False,
        }
        with patch.object(server, "windows_proxy_state", return_value=state), \
             patch.dict(os.environ, {
                 "HTTPS_PROXY": "", "https_proxy": "",
                 "HTTP_PROXY": "http://remote.example:8080",
                 "ALL_PROXY": "http://remote.example:8080",
             }, clear=False):
            self.assertEqual(server.refresh_network_opener(), "http://127.0.0.1:7890")

        proxy_handlers = [
            handler for handler in server.PIXIV_OPENER.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {
            "http": "http://127.0.0.1:7890",
            "https": "http://127.0.0.1:7890",
        })


    def test_unchanged_network_selection_does_not_rebuild_opener(self):
        state = {
            "mode": "system-proxy", "proxyEnabled": True,
            "proxyServer": "127.0.0.1:7890", "proxyStored": "127.0.0.1:7890",
            "pac": "", "environmentProxy": False,
        }
        server.PIXIV_NETWORK_FINGERPRINT = None
        server.PIXIV_NETWORK_CHECKED_AT = 0.0
        with patch.object(server, "windows_proxy_state", return_value=state), \
             patch.dict(os.environ, {"HTTPS_PROXY": "", "https_proxy": ""}, clear=False), \
             patch.object(server.urllib.request, "build_opener", wraps=urllib.request.build_opener) as build:
            first = server.refresh_network_opener()
            second = server.refresh_network_opener()
        self.assertEqual(first, "http://127.0.0.1:7890")
        self.assertEqual(second, first)
        self.assertEqual(build.call_count, 1)

    def test_search_http_request_refreshes_network_without_prior_diagnosis(self):
        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        payload = {
            "tag": "猫 犬", "tags": ["猫", "犬"], "scope": "safe", "page": 1,
            "pages": 1, "pageNumbers": [1], "availablePages": [1],
            "preloadedThrough": 1, "items": [], "perPage": 36, "total": 0,
            "hasMore": False, "budgetExhausted": False, "truncatedDates": [],
            "workType": "all", "includeAi": True, "mode": "fixture",
        }
        try:
            query = urllib.parse.urlencode({
                "tag": "猫 犬", "page": 1, "mode": "safe",
                "workType": "all", "includeAi": "true",
            })
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}/api/pixiv/search?{query}",
                headers={"X-MOKU-Request-Token": server.REQUEST_TOKEN},
            )
            with patch.object(server, "ensure_network_opener_current") as ensure, \
                 patch.object(server, "search_pixiv_results", return_value=payload):
                with opener.open(request, timeout=5) as response:
                    data = json.loads(response.read())
            self.assertEqual(data["tags"], ["猫", "犬"])
            ensure.assert_called_once_with()
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)

    def test_network_diagnose_refreshes_route_before_checks_without_leaking_proxy_address(self):
        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        events = []
        state = {
            "mode": "system-proxy", "proxyEnabled": True,
            "proxyServer": "127.0.0.1:7890", "proxyStored": "127.0.0.1:7890",
            "pac": "http://127.0.0.1/proxy.pac", "environmentProxy": False,
        }
        checks = [{"name": "pixiv", "ok": True}, {"name": "cdn", "ok": True}]
        try:
            with patch.object(server, "windows_proxy_state", return_value=state), patch.object(
                server, "refresh_network_opener", side_effect=lambda: events.append("refresh") or "http://127.0.0.1:7890"
            ), patch.object(
                server, "run_network_diagnostic_checks", side_effect=lambda: events.append("checks") or checks
            ):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/api/network/diagnose",
                    headers={"X-MOKU-Request-Token": server.REQUEST_TOKEN},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read())
            self.assertEqual(events, ["refresh", "checks"])
            self.assertTrue(payload["proxySelected"])
            self.assertEqual(payload["summary"]["routeLabel"], "Windows 系统代理")
            for secret_field in ("selectedProxy", "proxyServer", "proxyStored", "pac"):
                self.assertNotIn(secret_field, payload)
                self.assertNotIn("127.0.0.1:7890", json.dumps(payload, ensure_ascii=False))
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)


    def test_human_network_summary_reports_system_proxy_success(self):
        state = {"mode": "system-proxy", "proxyEnabled": True, "proxyServer": "127.0.0.1:7890", "pac": ""}
        summary = server.human_network_summary(state, "http://127.0.0.1:7890", [
            {"name": "pixiv", "ok": True, "ms": 80},
            {"name": "cdn", "ok": True, "ms": 120},
        ])
        self.assertEqual(summary["routeLabel"], "Windows 系统代理")
        self.assertEqual(summary["headline"], "当前网络可以使用 Pixiv")
        self.assertIn("主站和图片", summary["guidance"])

    def test_human_network_summary_explains_direct_failure_without_claiming_vpn(self):
        state = {"mode": "direct-or-tun", "proxyEnabled": False, "proxyServer": "", "pac": ""}
        summary = server.human_network_summary(state, "", [
            {"name": "pixiv", "ok": False, "errorKind": "timeout"},
            {"name": "cdn", "ok": False, "errorKind": "timeout"},
        ])
        self.assertEqual(summary["routeLabel"], "直连 / TUN")
        self.assertEqual(summary["headline"], "当前网络无法连接 Pixiv")
        self.assertIn("系统代理或 TUN", summary["guidance"])
        self.assertNotIn("VPN 已关闭", summary["guidance"])

    def test_human_network_summary_distinguishes_unusable_system_proxy_and_cdn_failure(self):
        invalid = server.human_network_summary(
            {"mode": "system-proxy", "proxyEnabled": True, "proxyServer": "example.com:8080", "pac": ""},
            "", [{"name": "pixiv", "ok": False}, {"name": "cdn", "ok": False}],
        )
        self.assertIn("本机 HTTP 代理", invalid["guidance"])
        partial = server.human_network_summary(
            {"mode": "system-proxy", "proxyEnabled": True, "proxyServer": "127.0.0.1:7890", "pac": ""},
            "http://127.0.0.1:7890",
            [{"name": "pixiv", "ok": True}, {"name": "cdn", "ok": False}],
        )
        self.assertEqual(partial["headline"], "Pixiv 主站可用，但图片线路异常")

    def test_network_diagnosis_probes_are_anonymous_single_attempt_and_parallel(self):
        calls = []

        def slow_probe(url, image_only=False, **kwargs):
            calls.append({"url": url, "image_only": image_only, **kwargs})
            time.sleep(0.2)
            return b"ok", "image/jpeg" if image_only else "text/html"

        started = time.monotonic()
        with patch.object(server, "pixiv_request", side_effect=slow_probe):
            checks = server.run_network_diagnostic_checks()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.34, f"probes ran serially in {elapsed:.3f}s")
        self.assertEqual([row["name"] for row in checks], ["pixiv", "cdn"])
        self.assertTrue(all(row["ok"] for row in checks))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call.get("anonymous") is True for call in calls))
        self.assertTrue(all(call.get("attempts") == 1 for call in calls))
        self.assertTrue(all(call.get("timeout") == 7 for call in calls))



if __name__ == "__main__":
    unittest.main()
