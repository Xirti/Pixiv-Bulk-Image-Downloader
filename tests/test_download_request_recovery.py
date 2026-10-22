from __future__ import annotations

import http.client
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server


class DownloadRequestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        # The mocked publisher's completed artifact must exist for receipt checks.
        (Path(self.root.name) / "saved.jpg").write_bytes(b"downloaded fixture image")
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.payload = {
            "id": "77", "pages": [0], "quality": "regular",
            "createFolder": False, "saveRoot": self.root.name,
            "requestId": uuid.uuid4().hex,
        }

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.worker.join(timeout=3)
        self.root.cleanup()

    def post(self, payload, *, path="/api/pixiv/download", timeout=3):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=timeout)
        try:
            connection.request("POST", path, json.dumps(payload), {
                "Content-Type": "application/json",
                "X-MOKU-Request-Token": server.REQUEST_TOKEN,
            })
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def artwork(self):
        return {"id": "77", "restriction": "safe", "pageImages": [{"regular": "/unused"}]}

    def test_disconnected_request_is_coalesced_then_replayed_after_success(self):
        for path in ("/api/pixiv/download", "/api/pixiv/batch-download"):
            with self.subTest(path=path):
                started = threading.Event()
                finish = threading.Event()
                completed = threading.Event()
                calls = []
                payload = {**self.payload, "requestId": uuid.uuid4().hex}
                if "batch" in path:
                    payload["groups"] = [{"id": "77", "pages": [0]}]

                def stage(*_args, **_kwargs):
                    calls.append(1)
                    started.set()
                    if len(calls) == 1:
                        finish.wait(3)
                    completed.set()
                    return ["saved.jpg"], False

                with patch.object(server, "ensure_network_opener_current"), patch.object(
                    server, "validated_authorization", return_value=(False, None),
                ), patch.object(server, "DOWNLOAD_TASK_SLOTS", threading.BoundedSemaphore(1)), patch.object(
                    server, "pixiv_item_for_download", side_effect=lambda *_a, **_k: self.artwork()), patch.object(
                    server, "_stage_and_publish_download", side_effect=stage,
                ):
                    try:
                        with self.assertRaises(TimeoutError):
                            self.post(payload, path=path, timeout=0.05)
                        self.assertTrue(started.wait(1))
                        status, pending = self.post(payload, path=path)
                        self.assertEqual(status, 202, pending)
                        self.assertTrue(pending["pending"])
                    finally:
                        finish.set()
                    self.assertTrue(completed.wait(1))
                    # Wait for the result record, not just the staged operation.
                    for _ in range(20):
                        status, result = self.post(payload, path=path)
                        if status != 202:
                            break
                        threading.Event().wait(0.01)
                    self.assertEqual(status, 200, result)
                    self.assertEqual(result["saved"], ["saved.jpg"])
                    self.assertEqual(calls, [1], "retry repeated the completed network/save operation")

    def test_request_id_rejects_changed_body_or_account_but_allows_same_account_relogin(self):
        with patch.object(server, "workspace_scope", return_value="account-a") as owner_scope, patch.object(
            server, "ensure_network_opener_current"), patch.object(
            server, "validated_authorization", return_value=(True, 11),
        ) as authorization, patch.object(
            server, "pixiv_item_for_download", return_value=self.artwork()), patch.object(
            server, "_stage_and_publish_download", return_value=(["saved.jpg"], False),
        ) as stage:
            self.assertEqual(self.post(self.payload)[0], 200)
            changed = {**self.payload, "quality": "original"}
            self.assertEqual(self.post(changed)[0], 409)
            self.assertEqual(self.post(self.payload, path="/api/pixiv/batch-download")[0], 409)
            authorization.return_value = (True, 12)
            self.assertEqual(self.post(self.payload)[0], 200)
            owner_scope.return_value = "account-b"
            authorization.return_value = (True, 13)
            self.assertEqual(self.post(self.payload)[0], 409)
            self.assertEqual(stage.call_count, 1)

    def test_failed_operation_can_retry_with_the_same_request_id(self):
        with patch.object(server, "ensure_network_opener_current"), patch.object(
            server, "validated_authorization", return_value=(False, None),
        ), patch.object(server, "pixiv_item_for_download", return_value=self.artwork()), patch.object(
            server, "_stage_and_publish_download", side_effect=[OSError("offline"), (["saved.jpg"], False)],
        ) as stage:
            self.assertEqual(self.post(self.payload)[0], 502)
            self.assertEqual(self.post(self.payload)[0], 200)
            self.assertEqual(self.post(self.payload)[0], 200)
            self.assertEqual(stage.call_count, 2)

    def test_invalid_request_id_is_rejected_before_network_work(self):
        with patch.object(server, "ensure_network_opener_current") as network:
            for value in (None, 123, "short", "x" * 97, "a" * 20 + "/"):
                self.assertEqual(self.post({**self.payload, "requestId": value})[0], 400)
            network.assert_not_called()


class DownloadRegistryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows directory junction required")
    def test_completed_receipt_cannot_follow_a_replacement_directory_junction(self):
        from download_requests import DownloadRequests

        for mode in ("memory", "restart", "write_failed", "read_failed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                saved, moved = root / "saved", root / "moved"
                saved.mkdir()
                (saved / "image.png").write_bytes(b"image")
                journal = root / "receipts.sqlite3"
                body = {"saveRoot": str(saved), "requestId": "junction-receipt-000001"}
                calls = []
                def operation():
                    calls.append(1)
                    server.Handler._save_root(body)
                    return {"saved": ["image.png"], "pages": 1}, 200
                registry = DownloadRequests(path=journal)
                if mode == "write_failed":
                    with patch.object(registry, "_write_success", side_effect=OSError("journal unavailable")):
                        registry.run(body["requestId"], "/single", body, operation, scope="public")
                else:
                    registry.run(body["requestId"], "/single", body, operation, scope="public")
                self.assertTrue(saved.resolve().is_relative_to(root))
                self.assertTrue(moved.resolve().is_relative_to(root))
                saved.rename(moved)
                result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(saved), str(moved)],
                                        capture_output=True)
                if result.returncode:
                    self.skipTest("Windows junction creation is unavailable")
                try:
                    if mode == "restart":
                        registry = DownloadRequests(path=journal)
                    with patch.object(registry, "_read_success", wraps=registry._read_success) as read:
                        if mode == "read_failed":
                            read.side_effect = sqlite3.OperationalError("journal unavailable")
                        with self.assertRaisesRegex(server.RequestInputError, "链接|重解析"):
                            registry.run(body["requestId"], "/single", body, operation, scope="public")
                    self.assertEqual(calls, [1, 1])
                finally:
                    saved.rmdir()

    def test_corrupt_durable_receipts_recheck_and_repair_only_the_matching_request(self):
        from pathlib import Path
        from download_requests import DownloadRequestError, DownloadRequests

        corruptions = [
            ("result", "{bad json"), ("result", "[]"),
            ("result", json.dumps({"saved": "saved.png", "pages": 1})),
            ("result", json.dumps({"saved": ["saved.png"], "pages": "bad"})),
            ("files", "{bad json"), ("files", "null"),
            ("files", "[]"), ("files", '[["bad path"]]'),
            ("files", '[["bad path", "bad size", 0]]'),
        ]
        for column, encoded in corruptions:
            for restart in (False, True):
                with self.subTest(column=column, encoded=encoded, restart=restart), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    file = root / "saved.png"
                    file.write_bytes(b"saved")
                    journal = root / "requests.db"
                    body = {"saveRoot": str(root), "requestId": "corrupt-request-000001"}
                    calls = []
                    def operation():
                        # Model normal download deduplication: the file exists,
                        # so recovering the request reports a skip, not a rewrite.
                        calls.append(1)
                        return {"saved": [], "skippedFiles": ["saved.png"], "skippedPages": 1, "pages": 1}, 200
                    registry = DownloadRequests(path=journal)
                    registry.run(body["requestId"], "/single", body, operation, scope="account-a")
                    initial_state = file.stat()
                    connection = sqlite3.connect(journal)
                    try:
                        with connection:
                            connection.execute(f"UPDATE completed SET {column}=? WHERE id=?", (encoded, body["requestId"]))
                    finally:
                        connection.close()
                    with self.assertRaises(DownloadRequestError) as conflict:
                        DownloadRequests(path=journal).run(body["requestId"], "/single", body, operation, scope="account-b")
                    self.assertEqual(conflict.exception.status, 409)
                    self.assertEqual(calls, [1])
                    if restart:
                        registry = DownloadRequests(path=journal)
                    result = registry.run(body["requestId"], "/single", body, operation, scope="account-a")
                    self.assertEqual(result[1], 200)
                    self.assertEqual(calls, [1, 1])
                    self.assertEqual(file.read_bytes(), b"saved")
                    self.assertEqual(file.stat().st_mtime_ns, initial_state.st_mtime_ns)
                    replay = DownloadRequests(path=journal).run(body["requestId"], "/single", body, operation, scope="account-a")
                    self.assertEqual(replay, result)
                    self.assertEqual(calls, [1, 1])

    def test_durable_success_survives_registry_restart_and_checks_files(self):
        from pathlib import Path
        from download_requests import DownloadRequests
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "saved.png"
            file.write_bytes(b"saved")
            path = root / "requests.db"
            body = {"saveRoot": str(root), "requestId": "durable-request-000001"}
            calls = []
            def operation():
                calls.append(1)
                file.write_bytes(b"saved")
                return {"saved": ["saved.png"], "pages": 1}, 200
            first = DownloadRequests(path=path).run(body["requestId"], "/single", body, operation, scope="epoch-1", durable_scope="public")
            second = DownloadRequests(path=path).run(body["requestId"], "/single", body, operation, scope="epoch-2", durable_scope="public")
            self.assertEqual(second, first)
            self.assertEqual(calls, [1])
            file.unlink()
            DownloadRequests(path=path).run(body["requestId"], "/single", body, operation, scope="epoch-3", durable_scope="public")
            self.assertEqual(calls, [1, 1])
            with self.assertRaises(ValueError):
                DownloadRequests(path=path).run(body["requestId"], "/single", body, operation, durable_scope="other-account")

    def test_registry_eviction_expiration_and_active_records_are_bounded(self):
        from download_requests import DownloadRequests

        now = [0.0]
        registry = DownloadRequests(max_entries=2, ttl_seconds=10, clock=lambda: now[0])
        first = threading.Event()
        release = threading.Event()
        results = []
        calls = []

        def slow():
            first.set()
            release.wait(3)
            return {"ok": True}, 200

        def run(key):
            calls.append(key)
            return {"id": key}, 200

        worker = threading.Thread(target=lambda: results.append(registry.run("a" * 16, "/single", {}, slow)))
        worker.start()
        try:
            self.assertTrue(first.wait(1))
            registry.run("b" * 16, "/single", {}, lambda: run("b"))
            registry.run("c" * 16, "/single", {}, lambda: run("c"))
            self.assertEqual(registry.run("a" * 16, "/single", {}, slow)[1], 202)
            now[0] = 20
            self.assertEqual(registry.run("a" * 16, "/single", {}, slow)[1], 202)
            registry.run("c" * 16, "/single", {}, lambda: run("c"))
            self.assertEqual(calls, ["b", "c", "c"])
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [({"ok": True}, 200)])

    def test_unidentified_requests_and_exceptions_do_not_poison_retries(self):
        from download_requests import DownloadRequests

        registry = DownloadRequests()
        calls = []
        def operation():
            calls.append(1)
            return {}, 200
        registry.run(None, "/single", {}, operation)
        registry.run(None, "/single", {}, operation)
        self.assertEqual(len(calls), 2)
        with self.assertRaisesRegex(RuntimeError, "injected"):
            registry.run("a" * 16, "/single", {}, lambda: (_ for _ in ()).throw(RuntimeError("injected")))
        self.assertEqual(registry.run("a" * 16, "/single", {}, operation)[1], 200)

    def test_full_active_registry_rejects_new_identity_but_keeps_existing_one(self):
        from download_requests import DownloadRequestError, DownloadRequests

        registry = DownloadRequests(max_entries=1)
        started, finish = threading.Event(), threading.Event()
        def operation():
            started.set()
            finish.wait(3)
            return {"ok": True}, 200
        worker = threading.Thread(target=lambda: registry.run("a" * 16, "/single", {}, operation))
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            with self.assertRaises(DownloadRequestError) as raised:
                registry.run("b" * 16, "/single", {}, operation)
            self.assertEqual(raised.exception.status, 429)
            self.assertEqual(registry.run("a" * 16, "/single", {}, operation)[1], 202)
        finally:
            finish.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
