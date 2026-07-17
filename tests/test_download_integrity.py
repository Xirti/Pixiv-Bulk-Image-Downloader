from __future__ import annotations

import json
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import server


PNG = b"\x89PNG\r\n\x1a\nMOKU-TEST"


class BatchDownloadIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.token = "batch-test-token"
        self.artwork_id = "990001"
        self.remote_urls = [
            f"https://i.pximg.net/test/{self.artwork_id}_p0.png",
            f"https://i.pximg.net/test/{self.artwork_id}_p1.png",
        ]
        self._old_cache = dict(server.PIXIV_CACHE)
        self._old_tokens = dict(server.IMAGE_TOKENS)
        server.PIXIV_CACHE.clear()
        server.IMAGE_TOKENS.clear()
        server.PIXIV_CACHE[self.artwork_id] = {
            "id": self.artwork_id,
            "restriction": "safe",
            "source": "pixiv",
            "title": "批量完整性测试",
            "pageImages": [
                {
                    "regular": f"/api/pixiv/image?token={self.token}-{index}",
                    "original": f"/api/pixiv/image?token={self.token}-{index}",
                }
                for index in range(2)
            ],
        }
        for index, remote in enumerate(self.remote_urls):
            server.IMAGE_TOKENS[f"{self.token}-{index}"] = (
                9_999_999_999.0,
                self.artwork_id,
                remote,
                "safe",
            )

    def tearDown(self):
        server.PIXIV_CACHE.clear()
        server.PIXIV_CACHE.update(self._old_cache)
        server.IMAGE_TOKENS.clear()
        server.IMAGE_TOKENS.update(self._old_tokens)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    def post(self, payload: dict, root: Path) -> tuple[int, dict]:
        body = json.dumps({**payload, "saveRoot": str(root)}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.httpd.server_port}/api/pixiv/batch-download",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Host": f"127.0.0.1:{self.httpd.server_port}",
                "Origin": f"http://127.0.0.1:{self.httpd.server_port}",
                "X-MOKU-Request-Token": server.REQUEST_TOKEN,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def post_single(self, root: Path, create_folder: bool = False) -> tuple[int, dict]:
        body = json.dumps({
            "id": self.artwork_id,
            "quality": "regular",
            "saveRoot": str(root),
            "createFolder": create_folder,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.httpd.server_port}/api/pixiv/download",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.httpd.server_port}",
                "X-MOKU-Request-Token": server.REQUEST_TOKEN,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_batch_download_honors_create_folder_false(self):
        with tempfile.TemporaryDirectory(prefix="moku-batch-folder-test-") as raw_root, patch.object(
            server, "pixiv_request", return_value=(PNG, "image/png")
        ):
            root = Path(raw_root)
            status, body = self.post(
                {
                    "groups": [{"id": self.artwork_id, "pages": [0]}],
                    "quality": "regular",
                    "createFolder": False,
                },
                root,
            )
            self.assertEqual(status, 200)
            saved = root / Path(body["saved"][0])
            self.assertEqual(saved.parent, root)
            self.assertTrue(saved.exists())

    def test_batch_failure_publishes_no_partial_files(self):
        calls = 0

        def fail_second(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise urllib.error.URLError("injected failure")
            return PNG, "image/png"

        with tempfile.TemporaryDirectory(prefix="moku-batch-partial-test-") as raw_root, patch.object(
            server, "pixiv_request", side_effect=fail_second
        ):
            root = Path(raw_root)
            status, body = self.post(
                {
                    "groups": [{"id": self.artwork_id, "pages": [0, 1]}],
                    "quality": "regular",
                    "createFolder": True,
                },
                root,
            )
            self.assertEqual(status, 502)
            self.assertIn("失败", body["error"])
            self.assertEqual([path for path in root.rglob("*") if path.is_file()], [])

    def test_single_download_publishes_all_pages_atomically(self):
        source = Path(server.__file__).read_text(encoding="utf-8")
        single = source[source.index("def _post_pixiv_download"):source.index("def _post_fixture_download")]
        self.assertLess(
            single.index("shutil.rmtree"),
            single.index("return self.send_json(response_payload, response_status)"),
        )
        calls = 0

        def fail_second(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise urllib.error.URLError("injected failure")
            return PNG, "image/png"

        with tempfile.TemporaryDirectory(prefix="moku-single-partial-test-") as raw_root, patch.object(
            server, "pixiv_request", side_effect=fail_second
        ):
            root = Path(raw_root)
            status, body = self.post_single(root)
            self.assertEqual(status, 502)
            self.assertIn("失败", body["error"])
            self.assertEqual([path for path in root.rglob("*") if path.is_file()], [])


if __name__ == "__main__":
    unittest.main()
