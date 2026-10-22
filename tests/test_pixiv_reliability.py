import http.client
import json
import threading
import unittest
from unittest.mock import patch

from pixiv_adapter import should_retry_status


class PixivReliabilityTests(unittest.TestCase):
    def test_invalid_remote_detail_returns_json_and_the_next_valid_request_succeeds(self):
        import server

        detail = {"illustId":"123", "xRestrict":0, "isUnlisted":False, "isLoginOnly":False}
        page = {"urls":{"regular":"https://i.pximg.net/123.jpg", "original":"https://i.pximg.net/123.png"}}
        pages = [page, None, page]

        def upstream(url, _event):
            return {"body":pages if "/pages?" in url else detail}

        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, "validated_authorization", return_value=(False, None)), \
                    patch.object(server, "get_cached_pixiv_item", return_value=None), \
                    patch.object(server, "search_pixiv_json", side_effect=upstream), \
                    patch.object(server, "cache_pixiv_item", side_effect=lambda item:item), \
                    patch.dict(server.IMAGE_TOKENS, clear=True), patch.object(httpd, "handle_error"):
                def request():
                    connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
                    try:
                        connection.request("GET", "/api/pixiv/artwork/123", headers={"X-MOKU-Request-Token":server.REQUEST_TOKEN})
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()

                status, data = request()
                self.assertEqual(status, 502)
                self.assertIn("图片页", data["error"])
                self.assertEqual(len(server.IMAGE_TOKENS), 0)
                pages = [page, page]
                status, data = request()
                self.assertEqual(status, 200)
                self.assertEqual(data["pages"], 2)
                self.assertEqual(len(data["pageImages"]), 2)
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=3)

    def test_only_transient_server_statuses_are_retryable(self):
        self.assertTrue(should_retry_status(500))
        self.assertTrue(should_retry_status(503))
        self.assertFalse(should_retry_status(401))
        self.assertFalse(should_retry_status(403))
        self.assertFalse(should_retry_status(429))


    def test_single_attempt_anonymous_probe_never_sleeps_after_failure(self):
        import urllib.error
        import server
        from unittest.mock import Mock, patch
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("offline")
        with patch.object(server, "PIXIV_OPENER", opener), patch.object(server.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.URLError):
                server.pixiv_request(
                    "https://www.pixiv.net/", anonymous=True, timeout=1, attempts=1,
                )
        sleep.assert_not_called()



if __name__ == "__main__": unittest.main()
