from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import server


ROOT = Path(__file__).resolve().parents[1]


class PageResources(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.resources = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "img"} and attrs.get("src"):
            self.resources.append(attrs["src"])


class LiteLandingTests(unittest.TestCase):
    def test_home_has_no_model_logo_or_decorative_script_requests(self):
        page = PageResources((ROOT / "web/index.html").read_text(encoding="utf-8"))
        self.assertFalse(
            [url for url in page.resources if "brand-logos" in url or "brand-stage" in url],
            "Lite still initializes model artwork on startup",
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_global_theme_switch_survives_disabled_storage(self):
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "tests/theme.test.js")],
            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_selection_records_keep_capacity_and_origin_invariants(self):
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "tests/selection_store.test.js")],
            cwd=ROOT, text=True, encoding="utf-8", capture_output=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class LiteSearchTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()

    def tearDown(self):
        server.reset_search_caches()

    @staticmethod
    def row(number):
        return {
            "id": str(number), "title": "cat", "userName": "artist", "userId": "9",
            "url": f"https://i.pximg.net/{number}.jpg", "tags": ["cat"],
            "pageCount": 1, "width": 10, "height": 10, "xRestrict": 0,
            "isUnlisted": False, "isMasked": False, "visibilityScope": 0,
            "illustType": 0, "aiType": 1, "createDate": "2026-09-29",
        }

    def source(self, url, _cancel):
        page = int(parse_qs(urlsplit(url).query)["p"][0])
        return {"body": {"illustManga": {
            "total": 144, "lastPage": 4,
            "data": [self.row(i) for i in range((page - 1) * 36 + 1, page * 36 + 1)],
        }}}

    def test_first_page_is_delivered_without_waiting_for_later_source_pages(self):
        def only_first_page(url, cancel):
            self.assertEqual(
                parse_qs(urlsplit(url).query)["p"], ["1"],
                "usable first-page results were delayed by fetching the next source page",
            )
            return self.source(url, cancel)

        with patch.object(server, "search_pixiv_json", side_effect=only_first_page):
            result = server.search_pixiv_results("cat", "safe", 1, "all", True, authorized=False)
        self.assertEqual(len(result["items"]), 36)
        self.assertEqual(result["preloadedThrough"], 1)

    def test_prefetch_keeps_displayed_image_capabilities_and_returns_only_metadata(self):
        with patch.object(server, "search_pixiv_json", side_effect=self.source):
            first = server.search_pixiv_results("cat", "safe", 1, "all", True, authorized=False)
            displayed = first["items"][0]
            expected_url = server.approved_image_url(displayed["thumb"], displayed["id"])
            warmed = server.search_pixiv_results(
                "cat", "safe", 1, "all", True, authorized=False, prefetch=True,
            )
        self.assertEqual(server.approved_image_url(displayed["thumb"], displayed["id"]), expected_url)
        self.assertEqual(warmed["items"], [], "prefetch should not issue replacement display tokens")
        self.assertEqual(warmed["preloadedThrough"], 4)

    def test_ugoira_search_fetches_the_requested_type_instead_of_sampling_mixed_rows(self):
        remote_types = []
        def remote_search(url, _cancel):
            query = parse_qs(urlsplit(url).query)
            remote_type = query["type"][0]
            remote_types.append(remote_type)
            source_page = int(query["p"][0])
            rows = [
                {**self.row((source_page - 1) * 36 + i + 1),
                 "illustType": 2 if remote_type == "ugoira" or i == 0 else 0}
                for i in range(36)
            ]
            key = "illust" if "/search/illustrations/" in urlsplit(url).path else "illustManga"
            return {"body": {key: {"total": 72, "lastPage": 2, "data": rows}}}
        with patch.object(server, "search_pixiv_json", side_effect=remote_search):
            result = server.search_pixiv_results("cat", "safe", 1, "ugoira", True, authorized=False)
        self.assertEqual(len(result["items"]), 36, "animation search sampled mixed illustrations and ran out of budget")
        self.assertEqual(set(remote_types), {"ugoira"})
        self.assertTrue(all(item["workType"] == "ugoira" for item in result["items"]))

    def test_every_type_keeps_its_filter_and_stable_first_page_through_prefetch_and_paging(self):
        cases = (
            ("all", "artworks", "all", "illustManga", {"illustration", "manga", "ugoira"}),
            ("illustration", "illustrations", "illust", "illust", {"illustration"}),
            ("manga", "manga", "manga", "manga", {"manga"}),
            ("ugoira", "illustrations", "ugoira", "illust", {"ugoira"}),
        )
        for work_type, route, remote_type, block_key, expected_types in cases:
            with self.subTest(work_type=work_type):
                server.reset_search_caches()
                requests = []
                def remote(url, _cancel):
                    query = parse_qs(urlsplit(url).query)
                    requests.append(url)
                    self.assertIn(f"/search/{route}/", urlsplit(url).path)
                    self.assertEqual(query["type"], [remote_type])
                    self.assertEqual(query["mode"], ["safe"])
                    source_page = int(query["p"][0])
                    rows = []
                    for number in range((source_page - 1) * 60, source_page * 60):
                        kind = number % 3 if work_type == "all" else {"illustration": 0, "manga": 1, "ugoira": 2}[work_type]
                        rows.append({**self.row(1000 - number), "illustType": kind})
                    return {"body": {block_key: {"total": 180, "lastPage": 3, "data": rows}}}
                with patch.object(server, "search_pixiv_json", side_effect=remote):
                    first = server.search_pixiv_results("cat", "safe", 1, work_type, True, authorized=False)
                    server.search_pixiv_results("cat", "safe", 1, work_type, True, authorized=False, prefetch=True)
                    repeated = server.search_pixiv_results("cat", "safe", 1, work_type, True, authorized=False)
                    second = server.search_pixiv_results("cat", "safe", 2, work_type, True, authorized=False)
                self.assertEqual([row["id"] for row in first["items"]], [str(i) for i in range(1000, 964, -1)])
                self.assertEqual([row["id"] for row in repeated["items"]], [row["id"] for row in first["items"]])
                self.assertEqual(len(second["items"]), 36)
                self.assertFalse({row["id"] for row in first["items"]}.intersection(row["id"] for row in second["items"]))
                self.assertEqual({row["workType"] for row in first["items"]}, expected_types)
                self.assertTrue(requests)

    def test_literal_and_foreground_does_not_wait_for_a_redundant_source(self):
        calls = []
        def load(_key, tag, _mode, _target, _allow_r18, budget):
            calls.append(tag)
            budget["requests"] += 1
            if tag == "night":
                raise RuntimeError("redundant source unavailable")
            rows = [{**self.row(i), "tags": ["cat", "night"]} for i in range(36)]
            return {"rows": rows, "hasMore": True, "budgetExhausted": False, "truncatedDates": [], "nextOffset": 36}
        with patch.object(server, "load_search_source", side_effect=load):
            result = server.search_pixiv_results("cat;night", "safe", 1, "all", True, authorized=False)
        self.assertEqual(len(result["items"]), 36)
        self.assertEqual(calls, ["cat"])
        def warm_load(_key, tag, _mode, _target, _allow_r18, budget):
            calls.append(tag)
            budget["requests"] += 1
            rows = [{**self.row(len(calls) * 36 + i), "tags": ["cat", "night"]} for i in range(36)]
            return {"rows": rows, "hasMore": True, "budgetExhausted": False, "truncatedDates": [], "nextOffset": len(calls) * 36}
        with patch.object(server, "load_search_source", side_effect=warm_load):
            warmed = server.search_pixiv_results("cat;night", "safe", 1, "all", True, authorized=False, prefetch=True)
        self.assertEqual(calls[1], "night", "warm-up starved the next source after fast foreground delivery")
        self.assertIn("cat", calls[2:])
        self.assertEqual(warmed["items"], [])


if __name__ == "__main__":
    unittest.main()
