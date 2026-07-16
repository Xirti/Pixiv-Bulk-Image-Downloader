import threading

import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import server

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"


def _search_row(artwork_id):
    return {
        "id": str(artwork_id), "title": "t", "userName": "u", "userId": "9",
        "url": "https://i.pximg.net/a.jpg", "tags": [], "pageCount": 1,
        "width": 10, "height": 20, "xRestrict": 0, "isUnlisted": False,
        "isMasked": False, "visibilityScope": 0,
    }


class RemainingResponsivenessTests(unittest.TestCase):
    def setUp(self):
        server.HISTORY_CACHE.clear()
        server.clear_authorized_state()

    def tearDown(self):
        server.HISTORY_CACHE.clear()
        server.clear_authorized_state()

    def test_gallery_reveal_cannot_hide_a_large_dynamic_results_section(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("gallery.classList.add(\"in\")", source)
        style = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        self.assertNotIn(".reveal{opacity:0", style)

    def test_distinct_history_queries_do_not_share_a_network_lock(self):
        first_started = threading.Event()
        release_first = threading.Event()
        second_done = threading.Event()
        errors = []

        def fake_pixiv_json(url):
            tag = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
            if tag == "a":
                first_started.set()
                release_first.wait(timeout=3)
            artwork_id = 101 if tag == "a" else 202
            return {"body": {"illustManga": {"total": 1, "lastPage": 1, "data": [_search_row(artwork_id)]}}}

        def run(tag, done=None):
            try:
                server.extend_history(tag, "safe", 1)
            except Exception as exc:
                errors.append(exc)
            finally:
                if done: done.set()

        with patch.object(server, "pixiv_json", side_effect=fake_pixiv_json):
            first = threading.Thread(target=run, args=("a",), daemon=True)
            second = threading.Thread(target=run, args=("b", second_done), daemon=True)
            first.start()
            self.assertTrue(first_started.wait(timeout=1))
            second.start()
            completed_while_first_blocked = second_done.wait(timeout=0.5)
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertTrue(completed_while_first_blocked)
        self.assertEqual(errors, [])

    def test_concurrent_auth_checks_use_local_session_without_network(self):
        results = []

        def worker(barrier):
            barrier.wait(timeout=2)
            results.append(server.validated_session())

        barrier = threading.Barrier(6)
        threads = [threading.Thread(target=worker, args=(barrier,), daemon=True) for _ in range(5)]
        with patch.object(server, "session_cookie_header", return_value={"Cookie": "PHPSESSID=validvalue"}), patch.object(
            server, "pixiv_request", side_effect=AssertionError("remote probe must not run")
        ) as request:
            for thread in threads: thread.start()
            barrier.wait(timeout=2)
            for thread in threads: thread.join(timeout=2)

        self.assertEqual(request.call_count, 0)
        self.assertEqual(results, [True] * 5)


if __name__ == "__main__":
    unittest.main()
