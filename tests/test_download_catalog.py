import http.client
import json
import sqlite3
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

    def queued_request(self, request_id, endpoint, *, scope="public"):
        task_id = uuid.uuid4().hex
        options = {"quality": "original", "ugoiraFormat": "source", "saveRoot": str(self.output),
                   "createFolder": False, "skipExisting": False}
        single = endpoint == "/api/pixiv/download"
        queued = {"id": task_id, "kind": "single" if single else "batch",
                  "taskOptions": {"endpoint": endpoint, "body": {"id": "77", "pages": [0], **options}} if single else options,
                  "remainingChunks": [{"groups": [{"id": "77", "pages": [0]}], "pageCount": 1,
                                       "requestId": request_id, "context": {"kind": "tags", "value": "cat"}}],
                  "completedBatches": 0, "totalBatches": 1}
        self.post({"task": queued, "scope": scope}, "/api/workspace/task")
        return {"queueId": task_id, "scope": scope, "pages": [0], "groups": [{"id": "77", "pages": [0]}],
                "requestId": request_id, **options}

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

    def test_catalog_checks_a_full_result_page_with_one_database_read(self):
        ids = [str(100 + index) for index in range(36)]
        for artwork_id in ids:
            file = self.output / f"{artwork_id}.png"
            file.write_bytes(b"image")
            self.catalog.record(artwork_id, [0], "original", "source", self.output, [file])
        (self.output / f"{ids[0]}.png").unlink()
        connect = sqlite3.connect
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            with patch("local_catalog.sqlite3.connect", wraps=connect) as reads:
                connection.request("GET", "/api/library/catalog?ids=" + ",".join(ids),
                                   headers={"X-MOKU-Request-Token": server.REQUEST_TOKEN})
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 200, result)
                self.assertEqual(result["pages"], {id: [] if id == ids[0] else [0] for id in ids})
                self.assertEqual(reads.call_count, 1, "one preview page reopened the catalog for every work")
        finally:
            connection.close()

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

    def test_account_switch_during_workspace_read_does_not_return_old_private_basket(self):
        cookie = [{"Cookie": "PHPSESSID=111_original_session"}]
        started = threading.Event()
        result = []
        connect = sqlite3.connect
        with patch.object(server, "session_cookie_header", side_effect=lambda: cookie[0]):
            owner = server.workspace_scope()
            private = {"id": "123", "pages": [0], "item": {"pages": 1, "restriction": "r18"}}
            self.workspace.save_basket([private], owner, revision=0)

            def read_workspace():
                connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
                try:
                    connection.request("GET", "/api/workspace", headers={"X-MOKU-Request-Token": server.REQUEST_TOKEN})
                    response = connection.getresponse()
                    result.append((response.status, json.loads(response.read())))
                finally:
                    connection.close()

            def signal_read(*args, **kwargs):
                if Path(args[0]) == self.workspace.path and threading.current_thread() is not threading.main_thread():
                    started.set()
                return connect(*args, **kwargs)

            database = connect(self.workspace.path)
            try:
                database.execute("BEGIN EXCLUSIVE")
                with patch("workspace_store.sqlite3.connect", side_effect=signal_read):
                    reader = threading.Thread(target=read_workspace)
                    reader.start()
                    self.assertTrue(started.wait(2), "workspace read did not reach SQLite")
                    with server.SEARCH_SESSION_LOCKS_GUARD:
                        cookie[0] = {"Cookie": "PHPSESSID=222_new_session"}
                    database.rollback()
                    reader.join(5)
                    self.assertFalse(reader.is_alive())
            finally:
                database.close()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0][0], 409, result[0])
            self.assertNotIn("basket", result[0][1])
            self.assertEqual(len(self.workspace.load(owner)["basket"]), 1)

    def test_workspace_reports_capacity_without_overwriting_other_accounts_choices(self):
        private = {"id": "123", "pages": list(range(600)), "item": {"pages": 600, "restriction": "r18"}}
        public = {"id": "456", "pages": list(range(400)), "item": {"pages": 900}}
        self.workspace.save_basket([private, public], "account-A", revision=0)
        with patch.object(server, "workspace_scope", return_value="account-B"):
            result = self.post({"scope": "account-B", "revision": 1,
                                "basket": [{**public, "pages": list(range(900))}]},
                               "/api/workspace/basket", expected=409)
        self.assertTrue(result["basketCapacity"])
        self.assertEqual(self.workspace.load("account-A")["revision"], 1)

    def test_lost_response_recovery_checks_skipped_files_and_only_replaces_missing_page(self):
        for endpoint in ("/api/pixiv/download", "/api/pixiv/batch-download"):
            for requested in ([0, 2], [0, 1, 2]):
                with self.subTest(endpoint=endpoint, requested=requested):
                    directory = self.root / uuid.uuid4().hex
                    directory.mkdir()
                    original = self.post({"saveRoot": str(directory)})
                    request = {"saveRoot": str(directory), "requestId": uuid.uuid4().hex,
                               "pages": requested, "groups": [{"id": "77", "pages": requested}]}
                    journal = directory / "requests.db"
                    with patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests(path=journal)):
                        completed = self.post(request, endpoint)
                    before_retry = self.network.call_count
                    with patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests(path=journal)):
                        self.assertEqual(self.post(request, endpoint), completed)
                    self.assertEqual(self.network.call_count, before_retry)
                    (directory / original["saved"][0]).unlink()
                    with patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests(path=journal)):
                        resumed = self.post(request, endpoint)
                    self.assertEqual(len(resumed["saved"]), 1)
                    self.assertEqual(resumed["skippedPages"], len(requested) - 1)
                    self.assertEqual(self.network.call_count, before_retry + 1)

    def test_recovery_in_the_same_process_rechecks_deleted_skipped_file(self):
        original = self.post()
        request = {"requestId": uuid.uuid4().hex}
        with patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests(path=self.root / "requests.db")):
            self.assertEqual(self.post(request)["skippedPages"], 2)
            (self.output / original["saved"][0]).unlink()
            resumed = self.post(request)
        self.assertEqual(len(resumed["saved"]), 1)
        self.assertEqual(resumed["skippedPages"], 1)

    def test_queue_restart_replays_lost_response_without_repeating_download(self):
        request_id = uuid.uuid4().hex
        changes = self.queued_request(request_id, "/api/pixiv/batch-download")
        task_id = changes["queueId"]
        journal = self.root / "requests.db"
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

    def test_same_account_relogin_recovers_success_without_repeating_forced_download(self):
        for endpoint in ("/api/pixiv/download", "/api/pixiv/batch-download"):
            with self.subTest(endpoint=endpoint), patch.object(server, "workspace_scope", return_value="account-A"), \
                    patch.object(server, "validated_authorization", return_value=(True, 1)) as authorization, \
                    patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests(path=self.root / uuid.uuid4().hex)):
                changes = self.queued_request(uuid.uuid4().hex, endpoint, scope="account-A")
                completed = self.post(changes, endpoint)
                before_retry = self.network.call_count
                authorization.return_value = (True, 2)
                self.assertEqual(self.post(changes, endpoint), completed)
                self.assertEqual(self.network.call_count, before_retry)
                self.post({**changes, "quality": "regular"}, endpoint, expected=409)
                with patch.object(server, "workspace_scope", return_value="account-B"):
                    self.post(changes, endpoint, expected=403)

    def test_queue_account_is_bound_before_network_and_through_publication(self):
        for endpoint in ("/api/pixiv/download", "/api/pixiv/batch-download"):
            for phase in ("before_network", "before_publish"):
                with self.subTest(endpoint=endpoint, phase=phase):
                    owner = ["public"]
                    with patch.object(server, "workspace_scope", side_effect=lambda: owner[0]):
                        changes = self.queued_request(uuid.uuid4().hex, endpoint)
                        before_files = {file.name: file.read_bytes() for file in self.output.iterdir()}
                        def switch_owner(*_args, **_kwargs):
                            owner[0] = "account-B"
                            return b"\x89PNG\r\n\x1a\nMOKU-CATALOG", "image/png"
                        target = "ensure_network_opener_current" if phase == "before_network" else "pixiv_request"
                        with patch.object(server, target, side_effect=switch_owner):
                            self.post(changes, endpoint, expected=403)
                        self.assertEqual({file.name: file.read_bytes() for file in self.output.iterdir()}, before_files)
