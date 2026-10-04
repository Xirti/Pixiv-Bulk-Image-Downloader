import http.client
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server
from download_history import DownloadHistory
from download_requests import DownloadRequests
from local_catalog import LocalCatalog
from workspace_store import WorkspaceStore


class DownloadCatalogHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.catalog = LocalCatalog(self.root / "catalog.db")
        self.history = DownloadHistory(self.root / "history.db")
        self.workspace = WorkspaceStore(self.root / "workspace.db")
        self.patches = [
            patch.object(server, "LOCAL_CATALOG", self.catalog, create=True),
            patch.object(server, "DOWNLOAD_HISTORY", self.history),
            patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests()),
            patch.object(server, "WORKSPACE_STORE", self.workspace),
            patch.object(server, "session_cookie_header", return_value={}),
            patch.object(server, "ensure_network_opener_current"),
            patch.object(server, "validated_authorization", return_value=(False, None)),
            patch.object(server, "pixiv_item_for_download", side_effect=lambda id, **kw: {
                "id": id, "title": "Cat", "artist": "Artist", "restriction": "safe", "workType": "illustration",
                "pageImages": [{"original": "https://i.pximg.net/a.png", "regular": "https://i.pximg.net/b.png"}] * 3,
            }),
            patch.object(server, "approved_image_url", side_effect=lambda url, id: url),
            patch.object(server, "pixiv_request", return_value=(b"\x89PNG\r\n\x1a\nMOKU-CATALOG", "image/png")),
        ]
        for p in self.patches:
            p.start()
        self.network = self.patches[-1].target.pixiv_request
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.worker.join(3)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def post(self, changes=None, endpoint="/api/pixiv/download", *, expected=200):
        body = {"id": "77", "pages": [0, 2], "quality": "original", "createFolder": False,
                "saveRoot": str(self.output), "requestId": uuid.uuid4().hex, **(changes or {})}
        if endpoint.startswith("/api/workspace/") or body.get("queueId"):
            body.setdefault("scope", "public")
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            connection.request("POST", endpoint, json.dumps(body), {"Content-Type": "application/json", "X-MOKU-Request-Token": server.REQUEST_TOKEN})
            response = connection.getresponse()
            data = json.loads(response.read())
            self.assertEqual(response.status, expected, data)
            return data
        finally:
            connection.close()

    def test_skip_existing_and_redownload_missing_file_after_history_deletion(self):
        first = self.post()
        self.assertEqual(len(first["saved"]), 2)
        self.assertEqual(self.network.call_count, 2)
        self.history.clear()
        second = self.post()
        self.assertEqual(second["saved"], [])
        self.assertEqual(second["skippedPages"], 2)
        self.assertEqual(self.network.call_count, 2)
        (self.output / first["saved"][0]).unlink()
        third = self.post()
        self.assertEqual(len(third["saved"]), 1)
        self.assertEqual(third["skippedPages"], 1)
        self.assertEqual(self.network.call_count, 3)

    def test_different_quality_directory_and_force_download_are_not_skipped(self):
        self.post()
        self.assertEqual(self.post({"quality": "regular"})["skippedPages"], 0)
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(self.post({"saveRoot": str(other)})["skippedPages"], 0)
        self.assertEqual(self.post({"skipExisting": False})["skippedPages"], 0)
        self.assertEqual(self.network.call_count, 8)

    def test_batch_can_mix_already_saved_and_new_pages(self):
        self.post()
        result = self.post({"groups": [{"id": "77", "pages": [0, 1, 2]}, {"id": "88", "pages": [0]}]}, "/api/pixiv/batch-download")
        self.assertEqual(result["pages"], 4)
        self.assertEqual(result["skippedPages"], 2)
        self.assertEqual(len(result["saved"]), 2)
        self.assertEqual(self.network.call_count, 4)

    def test_open_history_uses_registered_directory_not_arbitrary_input(self):
        self.post()
        row = self.history.list()["items"][0]
        with patch.object(server.os, "startfile") as open_directory:
            result = self.post({"id": row["id"], "path": "C:/not-allowed"}, "/api/library/history/open")
            self.assertTrue(result["ok"])
            open_directory.assert_called_once_with(self.output)

    def test_workspace_rejects_stale_account_writes(self):
        basket = [{"id": "77", "pages": [0], "item": {"pages": 3, "restriction": "safe"}}]
        self.post({"basket": basket, "revision": 0}, "/api/workspace/basket")
        with patch.object(server, "workspace_scope", return_value="different-account"):
            self.post({"basket": [], "revision": 1}, "/api/workspace/basket", expected=409)
        restored = self.workspace.load("public")
        self.assertEqual(restored["revision"], 1)
        self.assertEqual(restored["basket"][0]["id"], "77")

    def test_queue_restart_replays_lost_response_without_repeating_download(self):
        request_id = uuid.uuid4().hex
        task_id = uuid.uuid4().hex
        queued = {"id": task_id, "kind": "batch", "taskOptions": {"quality": "original", "ugoiraFormat": "source",
                  "saveRoot": str(self.output), "createFolder": False, "skipExisting": False},
                  "remainingChunks": [{"groups": [{"id": "77", "pages": [0]}], "pageCount": 1,
                                       "requestId": request_id, "context": {"kind": "tags", "value": "cat"}}],
                  "completedBatches": 0, "totalBatches": 1}
        self.post({"task": queued}, "/api/workspace/task")
        journal = self.root / "requests.db"
        changes = {"queueId": task_id, "groups": [{"id": "77", "pages": [0]}], "requestId": request_id, "skipExisting": False}
        with patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests(path=journal)):
            first = self.post(changes, "/api/pixiv/batch-download")
        with patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests(path=journal)), patch.object(server, "WORKSPACE_STORE", WorkspaceStore(self.workspace.path)):
            restored = server.WORKSPACE_STORE.load("public")
            self.assertEqual(restored["tasks"][0]["status"], "paused")
            self.assertEqual(self.post(changes, "/api/pixiv/batch-download"), first)
            self.assertEqual(self.network.call_count, 1)
            with patch.object(server, "workspace_scope", return_value="different-account"):
                self.post(changes, "/api/pixiv/batch-download", expected=403)
                self.assertFalse(server.WORKSPACE_STORE.has_task(task_id, "different-account"))
                self.assertEqual(self.network.call_count, 1)
