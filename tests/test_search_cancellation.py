from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import server


class SearchCancellationTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()

    def tearDown(self):
        server.reset_search_caches()

    def test_cancel_tombstone_covers_cancel_before_search_registration(self):
        request_id = "search-cancel-before-register"

        self.assertFalse(server.cancel_search_request(request_id))
        event = server.register_search_request(request_id)

        self.assertTrue(event.is_set())
        server.release_search_request(request_id, event)
        self.assertNotIn(request_id, server.SEARCH_REQUESTS)

    def test_duplicate_active_request_id_is_rejected_without_losing_cancellation(self):
        request_id = "search-duplicate-active-id"
        event = server.register_search_request(request_id)
        try:
            with self.assertRaises(server.SearchRequestConflictError):
                server.register_search_request(request_id)

            self.assertTrue(server.cancel_search_request(request_id))
            self.assertTrue(event.is_set())
        finally:
            server.release_search_request(request_id, event)

    def test_active_request_registry_has_a_hard_limit(self):
        original_limit = server.MAX_TRACKED_SEARCH_REQUESTS
        first = second = None
        try:
            server.MAX_TRACKED_SEARCH_REQUESTS = 2
            first = server.register_search_request("search-capacity-first")
            second = server.register_search_request("search-capacity-second")

            with self.assertRaises(server.SearchRequestLimitError):
                server.register_search_request("search-capacity-third")
            self.assertFalse(server.cancel_search_request("search-capacity-unknown"))
            self.assertEqual(sum(
                bool(entry["active"]) for entry in server.SEARCH_REQUESTS.values()
            ), 2)
            self.assertTrue(server.SEARCH_REQUESTS["search-capacity-unknown"]["event"].is_set())
        finally:
            if first is not None:
                server.release_search_request("search-capacity-first", first)
            if second is not None:
                server.release_search_request("search-capacity-second", second)
            server.MAX_TRACKED_SEARCH_REQUESTS = original_limit

    def test_cancel_before_registration_survives_a_full_active_registry(self):
        original_limit = server.MAX_TRACKED_SEARCH_REQUESTS
        blocker = future = None
        try:
            server.MAX_TRACKED_SEARCH_REQUESTS = 1
            blocker = server.register_search_request("search-capacity-blocker")
            self.assertFalse(server.cancel_search_request("search-capacity-future"))

            server.release_search_request("search-capacity-blocker", blocker)
            blocker = None
            future = server.register_search_request("search-capacity-future")
            self.assertTrue(future.is_set())
        finally:
            if blocker is not None:
                server.release_search_request("search-capacity-blocker", blocker)
            if future is not None:
                server.release_search_request("search-capacity-future", future)
            server.MAX_TRACKED_SEARCH_REQUESTS = original_limit

    def test_search_network_read_checks_cancellation_after_response(self):
        cancel_event = threading.Event()
        calls = []

        def fake_pixiv_json(url, **kwargs):
            calls.append((url, kwargs))
            cancel_event.set()
            return {"body": {}}

        with patch.object(server, "pixiv_json", side_effect=fake_pixiv_json):
            with self.assertRaises(server.SearchCancelledError):
                server.search_pixiv_json("https://www.pixiv.net/ajax/search/artworks/test", cancel_event)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], {
            "timeout": server.SEARCH_REMOTE_TIMEOUT_SECONDS,
            "attempts": 1,
            "cancel_event": cancel_event,
        })

    def test_search_network_poll_timeout_does_not_hide_a_racing_success(self):
        payload = {"body": {"ok": True}}

        class CompletedAfterPollTimeout:
            def __init__(self):
                self.polled = False

            def add_done_callback(self, callback):
                callback(self)

            def result(self, timeout=None):
                if timeout is not None and not self.polled:
                    self.polled = True
                    raise server.FutureTimeoutError()
                return payload

            @staticmethod
            def done():
                return True

        future = CompletedAfterPollTimeout()
        with patch.object(server.SEARCH_NETWORK_POOL, "submit", return_value=future):
            result = server.search_pixiv_json(
                "https://www.pixiv.net/ajax/search/artworks/test",
                threading.Event(),
            )

        self.assertEqual(result, payload)

    def test_search_network_prefers_cancellation_over_a_racing_failure(self):
        cancel_event = threading.Event()

        class FailedWhileCancelling:
            def add_done_callback(self, callback):
                callback(self)

            def result(self, timeout=None):
                cancel_event.set()
                raise OSError("connection failed during cancellation")

        future = FailedWhileCancelling()
        with patch.object(server.SEARCH_NETWORK_POOL, "submit", return_value=future):
            with self.assertRaises(server.SearchCancelledError):
                server.search_pixiv_json(
                    "https://www.pixiv.net/ajax/search/artworks/test",
                    cancel_event,
                )

    def test_search_network_pool_caps_process_wide_pixiv_requests(self):
        release_requests = threading.Event()
        four_started = threading.Event()
        state_lock = threading.Lock()
        calls = 0
        active = 0
        peak = 0
        errors = []

        def blocking_pixiv_json(_url, **_kwargs):
            nonlocal calls, active, peak
            with state_lock:
                calls += 1
                active += 1
                peak = max(peak, active)
                if calls >= server.MAX_SEARCH_NETWORK_WORKERS:
                    four_started.set()
            try:
                release_requests.wait(timeout=3)
                return {"body": {}}
            finally:
                with state_lock:
                    active -= 1

        def run_request(index):
            try:
                server.search_pixiv_json(
                    f"https://www.pixiv.net/ajax/search/artworks/test-{index}",
                    threading.Event(),
                )
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=run_request, args=(index,)) for index in range(8)]
        try:
            with patch.object(server, "pixiv_json", side_effect=blocking_pixiv_json):
                for worker in workers:
                    worker.start()
                self.assertTrue(four_started.wait(timeout=2))
                self.assertFalse(release_requests.wait(timeout=0.1))
                with state_lock:
                    self.assertEqual(calls, server.MAX_SEARCH_NETWORK_WORKERS)
                    self.assertLessEqual(peak, server.MAX_SEARCH_NETWORK_WORKERS)
                release_requests.set()
                for worker in workers:
                    worker.join(timeout=3)
        finally:
            release_requests.set()
            for worker in workers:
                worker.join(timeout=3)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(calls, 8)
        self.assertEqual(peak, server.MAX_SEARCH_NETWORK_WORKERS)

    def test_cancelled_search_stops_waiting_for_a_blocked_connect_worker(self):
        request_started = threading.Event()
        release_request = threading.Event()
        worker_finished = threading.Event()
        cancel_event = threading.Event()
        errors = []

        def blocked_pixiv_json(_url, **_kwargs):
            request_started.set()
            try:
                release_request.wait(timeout=3)
                return {"body": {}}
            finally:
                worker_finished.set()

        def run_request():
            try:
                server.search_pixiv_json(
                    "https://www.pixiv.net/ajax/search/artworks/blocked-connect",
                    cancel_event,
                )
            except Exception as exc:
                errors.append(exc)

        caller = threading.Thread(target=run_request)
        try:
            with patch.object(server, "pixiv_json", side_effect=blocked_pixiv_json):
                caller.start()
                self.assertTrue(request_started.wait(timeout=1))
                cancel_event.set()
                caller.join(timeout=1)
                self.assertFalse(caller.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], server.SearchCancelledError)
                release_request.set()
                self.assertTrue(worker_finished.wait(timeout=1))
        finally:
            release_request.set()
            caller.join(timeout=3)

    def test_cancellation_closes_a_blocked_search_response(self):
        cancel_event = threading.Event()
        read_started = threading.Event()
        read_released = threading.Event()
        errors = []

        class Headers:
            @staticmethod
            def get_content_type():
                return "application/json"

            @staticmethod
            def get(_name):
                return None

        class BlockingResponse:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

            @staticmethod
            def geturl():
                return "https://www.pixiv.net/ajax/search/artworks/test"

            def read(self, _size):
                read_started.set()
                read_released.wait(timeout=3)
                return b""

            @staticmethod
            def close():
                read_released.set()

        def run_request():
            try:
                server.pixiv_request(
                    "https://www.pixiv.net/ajax/search/artworks/test",
                    attempts=1,
                    timeout=5,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                errors.append(exc)

        with patch.object(server.PIXIV_OPENER, "open", return_value=BlockingResponse()):
            worker = threading.Thread(target=run_request)
            worker.start()
            self.assertTrue(read_started.wait(timeout=1))
            cancel_event.set()
            worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], server.SearchCancelledError)

    def test_cancellation_interrupts_a_real_blocked_socket_read(self):
        response_started = threading.Event()
        release_response = threading.Event()
        cancel_event = threading.Event()
        errors = []

        class SlowHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.flush()
                response_started.set()
                release_response.wait(timeout=3)
                try:
                    self.wfile.write(b"{}")
                except OSError:
                    pass

            def log_message(self, *_args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()

        def run_request():
            try:
                server.pixiv_request(
                    f"http://127.0.0.1:{httpd.server_port}/slow",
                    attempts=1,
                    timeout=5,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                errors.append(exc)

        try:
            with patch.object(server, "is_allowed_pixiv_url", return_value=True), patch.object(
                server, "PIXIV_OPENER", urllib.request.build_opener(),
            ):
                worker = threading.Thread(target=run_request)
                worker.start()
                self.assertTrue(response_started.wait(timeout=1))
                cancel_event.set()
                worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], server.SearchCancelledError)
        finally:
            release_response.set()
            httpd.shutdown()
            httpd.server_close()
            server_thread.join(timeout=3)

    def test_waiting_for_same_search_session_lock_is_cancellable(self):
        session_key = ("lock-cancel",)
        lock = server.search_session_lock(session_key)
        lock.acquire()
        cancel_event = threading.Event()
        started = threading.Event()
        errors = []

        def wait_for_lock():
            started.set()
            try:
                with server.locked_search_session(session_key, cancel_event):
                    pass
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=wait_for_lock)
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        cancel_event.set()
        worker.join(timeout=1)
        lock.release()

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], server.SearchCancelledError)

    def test_author_search_discards_results_behind_the_page_window(self):
        ids = [str(100000 - index) for index in range(500)]

        def works(_user_id, artwork_ids, **_kwargs):
            return [
                {
                    "id": artwork_id,
                    "title": artwork_id,
                    "userId": "42",
                    "userName": "artist",
                    "tags": ["cat"],
                    "width": 100,
                    "height": 100,
                    "pageCount": 1,
                    "bookmarkCount": 1,
                    "createDate": "2026-07-25T00:00:00+09:00",
                    "url": f"https://i.pximg.net/{artwork_id}.jpg",
                    "xRestrict": 0,
                    "isMasked": False,
                    "isUnlisted": False,
                    "visibilityScope": 0,
                    "illustType": 0,
                    "aiType": 1,
                }
                for artwork_id in artwork_ids
            ]

        key = ("user", "uid", "42", "42", "safe", "all", True, False)
        with patch.object(server, "load_user_profile_ids", return_value=ids), patch.object(
            server, "load_user_profile_works", side_effect=works,
        ):
            result = server.search_pixiv_results(
                "uid:42", "safe", 10, "all", True,
                authorized=False,
                cancel_event=threading.Event(),
            )
            trimmed_session = server.SEARCH_SESSIONS[key]
            trimmed_base_index = trimmed_session["baseIndex"]
            trimmed_item_count = len(trimmed_session["items"])

            first_page = server.search_pixiv_results(
                "uid:42", "safe", 1, "all", True,
                authorized=False,
                cancel_event=threading.Event(),
            )

        self.assertEqual(result["total"], 384)
        self.assertEqual(trimmed_base_index, 108)
        self.assertEqual(trimmed_item_count, 276)
        self.assertEqual(result["availablePages"][0], 4)
        self.assertEqual(
            [item["id"] for item in first_page["items"]],
            ids[:server.SEARCH_PER_PAGE],
        )

    def test_cancelled_history_response_does_not_commit_rows(self):
        cancel_event = threading.Event()
        namespace = ("cancel-test",)

        def cancel_during_request(_url, **_kwargs):
            cancel_event.set()
            return {
                "body": {
                    "illustManga": {
                        "total": 1,
                        "lastPage": 1,
                        "data": [{"id": "123"}],
                    },
                },
            }

        with patch.object(server, "pixiv_json", side_effect=cancel_during_request):
            with self.assertRaises(server.SearchCancelledError):
                server.extend_history(
                    "猫", "safe", 1,
                    namespace=namespace,
                    cancel_event=cancel_event,
                )

        state = server._history_state("猫", "safe", namespace=namespace)
        self.assertEqual(state["items"], [])
        self.assertFalse(state["queue"][0]["initialized"])

    def test_cancelled_user_batch_does_not_advance_profile_cursor(self):
        cancel_event = threading.Event()
        ids = [str(9000 - index) for index in range(80)]

        def cancel_during_batch(_user_id, _artwork_ids, **_kwargs):
            cancel_event.set()
            return []

        with patch.object(server, "load_user_profile_ids", return_value=ids), patch.object(
            server,
            "load_user_profile_works",
            side_effect=cancel_during_batch,
        ):
            with self.assertRaises(server.SearchCancelledError):
                server.search_pixiv_results(
                    "uid:42", "safe", 1, "all", True,
                    authorized=False,
                    cancel_event=cancel_event,
                )

        key = ("user", "uid", "42", "42", "safe", "all", True, False)
        self.assertEqual(server.SEARCH_SESSIONS[key]["profileOffset"], 0)
        self.assertEqual(server.SEARCH_SESSIONS[key]["items"], [])

    def test_cancelled_tag_search_stops_before_another_source_request(self):
        cancel_event = threading.Event()
        calls = 0

        def cancel_during_source(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            cancel_event.set()
            return {
                "rows": [],
                "nextOffset": 0,
                "hasMore": True,
                "budgetExhausted": False,
                "truncatedDates": [],
            }

        with patch.object(server, "load_search_source", side_effect=cancel_during_source):
            with self.assertRaises(server.SearchCancelledError):
                server.search_pixiv_results(
                    "猫", "safe", 1, "all", True,
                    authorized=False,
                    cancel_event=cancel_event,
                )

        self.assertEqual(calls, 1)

    def test_cancelled_multi_source_round_does_not_advance_offsets_or_lose_rows(self):
        cancel_event = threading.Event()
        groups = (("cat", "kitty"),)
        session_key = ("tags", groups, "safe", "all", True, True)
        row = {
            "id": "123", "title": "cat", "userId": "42", "userName": "artist",
            "tags": ["cat"], "width": 100, "height": 100, "pageCount": 1,
            "bookmarkCount": 10, "createDate": "2026-07-25T00:00:00+09:00",
            "url": "https://i.pximg.net/c/250x250_80_a2/example.jpg",
            "xRestrict": 0, "isMasked": False, "isUnlisted": False,
            "visibilityScope": 0, "illustType": 0, "aiType": 1,
        }

        def cancel_after_first_source(_session, tag, *_args, **_kwargs):
            self.assertEqual(tag, "cat")
            cancel_event.set()
            return {
                "rows": [row], "nextOffset": 1, "hasMore": True,
                "budgetExhausted": False, "truncatedDates": [],
            }

        with patch.object(server, "build_search_tag_groups", return_value=groups), patch.object(
            server, "load_search_source", side_effect=cancel_after_first_source,
        ):
            with self.assertRaises(server.SearchCancelledError):
                server.search_pixiv_results(
                    "cat", "safe", 1, "all", True,
                    authorized=False, fuzzy=True, cancel_event=cancel_event,
                )

        self.assertEqual(server.SEARCH_SESSIONS[session_key]["items"], [])
        self.assertEqual(server.SEARCH_SESSIONS[session_key]["sourceDone"], {})
        self.assertFalse(any(key[0] == session_key for key in server.SEARCH_SOURCE_OFFSETS))

        cancel_event.clear()

        def replay_sources(_session, tag, *_args, **_kwargs):
            rows = [row] if tag == "cat" else []
            return {
                "rows": rows, "nextOffset": len(rows), "hasMore": False,
                "budgetExhausted": False, "truncatedDates": [],
            }

        with patch.object(server, "build_search_tag_groups", return_value=groups), patch.object(
            server, "load_search_source", side_effect=replay_sources,
        ):
            result = server.search_pixiv_results(
                "cat", "safe", 1, "all", True,
                authorized=False, fuzzy=True, cancel_event=cancel_event,
            )

        self.assertEqual([item["id"] for item in result["items"]], ["123"])
        self.assertEqual(server.SEARCH_SOURCE_OFFSETS[(session_key, "cat", "safe")], 1)

    def test_reset_cannot_be_followed_by_stale_author_page_or_token_commits(self):
        store_entered = threading.Event()
        release_store = threading.Event()
        reset_started = threading.Event()
        reset_finished = threading.Event()
        errors = []
        session_key = ("user", "uid", "42", "42", "safe", "all", True, False)
        row = {
            "id": "123", "title": "work", "userId": "42", "userName": "artist",
            "tags": ["cat"], "width": 100, "height": 100, "pageCount": 1,
            "bookmarkCount": 10, "createDate": "2026-07-25T00:00:00+09:00",
            "url": "https://i.pximg.net/c/250x250_80_a2/example.jpg",
            "xRestrict": 0, "isMasked": False, "isUnlisted": False,
            "visibilityScope": 0, "illustType": 0, "aiType": 1,
        }
        real_store_pages = server.SEARCH_PAGE_CACHE.store_pages

        def blocking_store_pages(*args, **kwargs):
            store_entered.set()
            release_store.wait(timeout=3)
            return real_store_pages(*args, **kwargs)

        def run_search():
            try:
                server.search_pixiv_results(
                    "uid:42", "safe", 1, "all", True,
                    authorized=False, cancel_event=threading.Event(),
                )
            except Exception as exc:
                errors.append(exc)

        def run_reset():
            reset_started.set()
            server.reset_search_caches()
            reset_finished.set()

        with patch.object(server, "load_user_profile_ids", return_value=["123"]), patch.object(
            server, "load_user_profile_works", return_value=[row],
        ), patch.object(
            server.SEARCH_PAGE_CACHE, "store_pages", side_effect=blocking_store_pages,
        ):
            search_thread = threading.Thread(target=run_search)
            search_thread.start()
            self.assertTrue(store_entered.wait(timeout=2))
            reset_thread = threading.Thread(target=run_reset)
            reset_thread.start()
            self.assertTrue(reset_started.wait(timeout=2))
            reset_finished.wait(timeout=0.1)
            release_store.set()
            search_thread.join(timeout=3)
            reset_thread.join(timeout=3)

        self.assertFalse(search_thread.is_alive())
        self.assertFalse(reset_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertNotIn(session_key, server.SEARCH_SESSIONS)
        self.assertIsNone(server.SEARCH_PAGE_CACHE.get_page(session_key, 1))
        self.assertFalse(any(
            len(entry) >= 6 and entry[4] == session_key
            for entry in server.IMAGE_TOKENS.values()
        ))

    def test_cancel_endpoint_signals_the_registered_search(self):
        request_id = "search-cancel-endpoint-test"
        event = server.register_search_request(request_id)
        httpd = server.LocalThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{httpd.server_port}/api/pixiv/search/cancel",
                method="POST",
                data=json.dumps({"requestId": request_id}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-MOKU-Request-Token": server.REQUEST_TOKEN,
                },
            )
            with patch.object(server, "ensure_network_opener_current"):
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read())

            self.assertEqual(payload, {"ok": True, "cancelled": True})
            self.assertTrue(event.is_set())
        finally:
            server.release_search_request(request_id, event)
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
