import threading
import unittest
from unittest.mock import patch

import server


class AuthSessionValidationTests(unittest.TestCase):
    def setUp(self):
        server.clear_authorized_state()

    def tearDown(self):
        server.clear_authorized_state()

    def test_local_session_is_authorized_without_remote_self_probe(self):
        with patch.object(
            server, "session_cookie_header", return_value={"Cookie": "PHPSESSID=localvalue"}
        ), patch.object(
            server, "pixiv_request", side_effect=AssertionError("remote self probe is not required")
        ):
            self.assertTrue(server.validated_session(force=True))
            status = server.auth_status_snapshot()

        self.assertTrue(status["loggedIn"])
        self.assertEqual(status["authState"], "authorized")

    def test_missing_local_session_is_not_logged_in(self):
        with patch.object(server, "session_cookie_header", return_value={}):
            self.assertFalse(server.validated_session(force=True))

    def test_local_session_is_logged_in_without_remote_probe(self):
        with patch.object(server, "session_cookie_header", return_value={"Cookie": "PHPSESSID=validvalue"}), patch.object(
            server, "pixiv_request", side_effect=AssertionError("remote probe must not run")
        ):
            self.assertTrue(server.validated_session(force=True))

    def test_clear_authorized_state_revokes_r18_tokens_and_caches(self):
        server.IMAGE_TOKENS["r"] = (9999999999.0, "1", "https://i.pximg.net/a.jpg", "r18")
        server.IMAGE_TOKENS["s"] = (9999999999.0, "2", "https://i.pximg.net/b.jpg", "safe")
        server.PIXIV_CACHE["1"] = {"restriction": "r18"}
        server.PIXIV_CACHE["2"] = {"restriction": "safe"}
        server.HISTORY_CACHE[("tag", "r18")] = {"items": []}
        server.HISTORY_CACHE[("tag", "safe")] = {"items": []}
        r18_key = (("tag",), "r18", "all", True)
        all_key = (("tag",), "all", "all", True)
        safe_key = (("tag",), "safe", "all", True)
        for key in (r18_key, all_key, safe_key):
            server.SEARCH_SESSIONS[key] = {"items": []}
            server.SEARCH_PAGE_CACHE.store_pages(key, 1, {1: [key]})
        server.clear_authorized_state()
        self.assertNotIn("r", server.IMAGE_TOKENS)
        self.assertIn("s", server.IMAGE_TOKENS)
        self.assertNotIn("1", server.PIXIV_CACHE)
        self.assertIn("2", server.PIXIV_CACHE)
        self.assertNotIn(("tag", "r18"), server.HISTORY_CACHE)
        self.assertIn(("tag", "safe"), server.HISTORY_CACHE)
        self.assertNotIn(r18_key, server.SEARCH_SESSIONS)
        self.assertNotIn(all_key, server.SEARCH_SESSIONS)
        self.assertIn(safe_key, server.SEARCH_SESSIONS)
        self.assertIsNone(server.SEARCH_PAGE_CACHE.get_page(r18_key, 1))
        self.assertIsNone(server.SEARCH_PAGE_CACHE.get_page(all_key, 1))
        self.assertIsNotNone(server.SEARCH_PAGE_CACHE.get_page(safe_key, 1))

    def test_logout_generation_blocks_inflight_restricted_search_commit(self):
        started = threading.Event()
        released = threading.Event()
        errors = []

        def restricted_source(*_args, **_kwargs):
            started.set()
            released.wait(timeout=3)
            return {"rows": [], "hasMore": False, "budgetExhausted": False, "truncatedDates": []}

        def run_search():
            try:
                server.search_pixiv_results(
                    "猫", "all", 1, "all", True, authorized=True,
                    authorization_epoch=server.authorization_generation(),
                )
            except Exception as exc:
                errors.append(exc)

        with patch.object(server, "load_search_source", side_effect=restricted_source):
            worker = threading.Thread(target=run_search)
            worker.start()
            self.assertTrue(started.wait(timeout=2))
            server.clear_authorized_state()
            released.set()
            worker.join(timeout=3)

        self.assertFalse(worker.is_alive())
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], server.AuthorizationRevokedError)
        self.assertFalse(any(
            server.search_session_scope(key) in {"r18", "all"}
            for key in server.SEARCH_SESSIONS
        ))

    def test_history_cache_structure_is_guarded_during_parallel_sessions_and_clear(self):
        errors = []

        def mutate(worker: int):
            try:
                for index in range(120):
                    server._history_state("猫", "safe", ("session", worker, index))
                    if index % 9 == 0:
                        server.clear_authorized_state()
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=mutate, args=(index,)) for index in range(6)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
