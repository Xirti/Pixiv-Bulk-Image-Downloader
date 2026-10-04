from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import server
from download_history import DownloadHistory
from download_requests import DownloadRequests


def record(artwork_id="77", **changes):
    return {
        "artworkId": artwork_id, "title": "猫_100%", "artist": "画师",
        "workType": "illustration", "quality": "original", "format": "source",
        "pages": [2, 0], "files": ["C:\\art\\77_p0 (1).png", "C:\\art\\77_p2.png"],
        **changes,
    }


class DownloadHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "library" / "downloads.sqlite3"
        self.history = DownloadHistory(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_lazy_open_and_persistence(self):
        self.assertEqual(self.history.list()["total"], 0)
        self.history.clear()
        self.assertFalse(self.path.parent.exists())
        self.history.record("first", [record()])
        rows = DownloadHistory(self.path).list()["items"]
        self.assertEqual(rows[0]["pages"], [0, 2])
        self.assertEqual(rows[0]["files"], record()["files"])
        self.assertIn("+00:00", rows[0]["completedAt"])
        self.assertNotIn("operation", rows[0])

    def test_replay_dedupes_but_new_download_and_page_chunk_are_kept(self):
        self.history.record("first", [record(), record("88")])
        self.history.record("first", [record(), record("88")])
        self.history.record("second", [record(pages=[3])])
        self.assertEqual(self.history.list()["total"], 3)

    def test_cap_pagination_and_literal_search(self):
        history = DownloadHistory(self.path, max_records=3)
        for number in range(5):
            history.record(str(number), [record(str(number))])
        page = history.list(page=1, per_page=2)
        self.assertEqual([row["artworkId"] for row in page["items"]], ["4", "3"])
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["limit"], 3)
        self.assertEqual(history.list(page=99, per_page=2)["page"], 2)
        for query in ("100%", "猫_", "画师", "4"):
            self.assertGreater(history.list(query=query)["total"], 0)
        self.assertEqual(history.list(query="猫%not-matching")["total"], 0)
        self.assertEqual(history.list(query="' OR 1=1 --")["total"], 0)

    def test_clear_only_metadata_and_allows_new_records(self):
        image = Path(self.temp.name) / "existing.png"
        image.write_bytes(b"keep")
        self.history.record("first", [record(files=[str(image)])])
        self.history.clear()
        self.assertEqual(self.history.list()["total"], 0)
        self.assertEqual(image.read_bytes(), b"keep")
        self.history.record("second", [record()])
        self.assertEqual(self.history.list()["total"], 1)

    def test_concurrent_atomic_requests(self):
        def write(number):
            self.history.record(str(number), [record(str(number * 2)), record(str(number * 2 + 1))])
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(15)))
        self.assertEqual(self.history.list()["total"], 30)

    def test_invalid_request_is_atomic(self):
        with self.assertRaises(ValueError):
            self.history.record("first", [record(), record("88", pages=[True])])
        self.assertEqual(self.history.list()["total"], 0)
        for kwargs in ({"page": 0}, {"page": True}, {"query": "x" * 121}, {"per_page": 100}):
            with self.assertRaises(ValueError):
                self.history.list(**kwargs)

    def test_future_schema_is_not_overwritten(self):
        self.history.record("first", [record()])
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA user_version=2")
        connection.close()
        with self.assertRaises(ValueError):
            self.history.clear()
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM downloads").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        connection.close()


class DownloadHistoryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.history = DownloadHistory(self.root / "library" / "downloads.sqlite3")
        self.history_patch = patch.object(server, "DOWNLOAD_HISTORY", self.history)
        self.history_patch.start()
        self.httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.payload = {
            "id": "77", "pages": [0, 2], "quality": "original", "createFolder": False,
            "saveRoot": str(self.root), "requestId": "history-test-request-000001",
        }

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.worker.join(timeout=3)
        self.history_patch.stop()
        self.temp.cleanup()

    def request(self, path, body=None, *, token=True, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-MOKU-Request-Token"] = server.REQUEST_TOKEN
        if origin:
            headers["Origin"] = origin
        try:
            connection.request("POST" if body is not None else "GET", path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def artwork(self, artwork_id="77", **changes):
        return {"id": artwork_id, "title": "猫", "artist": "画师", "workType": "illustration", "restriction": "safe", "pageImages": [{}] * 4, **changes}

    def test_routes_are_local_protected_and_clear_requires_confirmation(self):
        self.history.record("first", [record()])
        with patch.object(server, "ensure_network_opener_current", side_effect=AssertionError("no network")), patch.object(server, "validated_authorization", side_effect=AssertionError("no account lookup")):
            self.assertEqual(self.request("/api/library/history", token=False)[0], 403)
            self.assertEqual(self.request("/api/library/history", origin="https://evil.example")[0], 403)
            self.assertEqual(self.request("/api/library/history")[1]["total"], 1)
            self.assertEqual(self.request("/api/library/history?page=0")[0], 400)
            self.assertEqual(self.request("/api/library/history/clear", {})[0], 400)
            self.assertEqual(self.request("/api/library/history/clear", {"confirm": True}, token=False)[0], 403)
            self.assertEqual(self.request("/api/library/history/clear", {"confirm": True}, origin="https://evil.example")[0], 403)
            self.assertEqual(self.request("/api/library/history/clear", {"confirm": True})[0], 200)
        self.assertEqual(self.history.list()["total"], 0)

    def test_success_replay_records_once_and_new_request_records_again(self):
        with patch.object(server, "ensure_network_opener_current"), patch.object(server, "validated_authorization", return_value=(False, None)), patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests()), patch.object(server, "pixiv_item_for_download", return_value=self.artwork()), patch.object(server, "_stage_and_publish_download", return_value=(["77_p0 (1).png", "77_p2.png"], False)) as publish:
            self.assertEqual(self.request("/api/pixiv/download", self.payload)[0], 200)
            self.assertEqual(self.request("/api/pixiv/download", self.payload)[0], 200)
            self.assertEqual(publish.call_count, 1)
            rows = self.history.list()["items"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["files"][0], str(self.root / "77_p0 (1).png"))
            self.assertEqual(rows[0]["pages"], [0, 2])
            self.assertEqual(self.request("/api/pixiv/download", {**self.payload, "requestId": "history-test-request-000002"})[0], 200)
            self.assertEqual(self.history.list()["total"], 2)

    def test_failed_publication_never_records_success(self):
        handler = object.__new__(server.Handler)
        with patch.object(server, "validated_authorization", return_value=(False, None)), patch.object(server, "pixiv_item_for_download", return_value=self.artwork()), patch.object(server, "_stage_and_publish_download", side_effect=OSError("offline")):
            self.assertEqual(handler._post_pixiv_download(self.payload)[1], 502)
        self.assertFalse(self.history.path.exists())

    def test_history_failure_warns_without_retrying_completed_download(self):
        with patch.object(server, "ensure_network_opener_current"), patch.object(server, "validated_authorization", return_value=(False, None)), patch.object(server, "DOWNLOAD_REQUESTS", DownloadRequests()), patch.object(server, "pixiv_item_for_download", return_value=self.artwork()), patch.object(server, "_stage_and_publish_download", return_value=(["saved.png"], False)) as publish, patch.object(self.history, "record", side_effect=sqlite3.OperationalError("locked")):
            first = self.request("/api/pixiv/download", self.payload)
            second = self.request("/api/pixiv/download", self.payload)
            self.assertEqual(first[0], 200)
            self.assertEqual(second, first)
            self.assertTrue(first[1]["historyWarning"])
            self.assertEqual(publish.call_count, 1)

    def test_batch_maps_actual_file_counts_not_page_counts(self):
        handler = object.__new__(server.Handler)
        def artwork(artwork_id, **_kwargs):
            return self.artwork(artwork_id, workType="ugoira" if artwork_id == "88" else "illustration")
        def stage(item, *_args, **_kwargs):
            return [object()] * (2 if item["workType"] == "ugoira" else 1)
        def publish(root, *, stage, **_kwargs):
            staged = []
            stage(root, staged)
            self.assertEqual(len(staged), 3)
            return ["77_p2.png", "88.zip", "88.json"], False
        payload = {**self.payload, "groups": [{"id": "77", "pages": [2]}, {"id": "88", "pages": [0]}]}
        with patch.object(server, "validated_authorization", return_value=(False, None)), patch.object(server, "pixiv_item_for_download", side_effect=artwork), patch.object(server, "stage_artwork_pages", side_effect=stage), patch.object(server, "_stage_and_publish_download", side_effect=publish):
            response, status = handler._post_pixiv_batch_download(payload)
        self.assertEqual(status, 200, response)
        rows = {row["artworkId"]: row for row in self.history.list()["items"]}
        self.assertEqual(rows["77"]["pages"], [2])
        self.assertEqual(rows["88"]["files"], [str(self.root / "88.zip"), str(self.root / "88.json")])

    def test_animation_gif_and_mp4_metadata(self):
        handler = object.__new__(server.Handler)
        for format in ("gif", "mp4"):
            with patch.object(server, "validated_authorization", return_value=(False, None)), patch.object(server, "pixiv_item_for_download", return_value=self.artwork(workType="ugoira")), patch.object(server, "_stage_and_publish_download", return_value=([f"saved.{format}"], False)):
                response, status = handler._post_pixiv_download({**self.payload, "pages": [0], "ugoiraFormat": format})
                self.assertEqual(status, 200, response)
        self.assertEqual({row["format"] for row in self.history.list()["items"]}, {"gif", "mp4"})
