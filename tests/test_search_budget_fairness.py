from __future__ import annotations

import unittest
from collections import Counter
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import server
from search_aliases import aliases_for


class SearchBudgetFairnessTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()
        server.mark_authorized_session()
        self.epoch = server.authorization_generation()

    def tearDown(self):
        server.clear_authorized_state()
        server.reset_search_caches()

    @staticmethod
    def row(tag, *, restriction=0):
        return {
            "id": "987654", "title": "fixture", "userName": "artist", "userId": "9",
            "url": "https://i.pximg.net/fixture.jpg", "tags": [tag],
            "pageCount": 1, "width": 10, "height": 10, "xRestrict": restriction,
            "isUnlisted": False, "isMasked": False, "visibilityScope": 0,
            "illustType": 0, "aiType": 1, "createDate": "2026-09-29",
        }

    @staticmethod
    def block(rows):
        return {"body": {"illustManga": {"total": len(rows), "lastPage": 1 if rows else 0, "data": rows}}}

    def search(self, tag, scope="safe", *, fuzzy=False):
        return server.search_pixiv_results(
            tag, scope, 1, "all", True, authorized=scope != "safe", fuzzy=fuzzy,
            authorization_epoch=self.epoch if scope != "safe" else None,
        )

    def test_all_scope_still_searches_r18_when_safe_history_is_empty(self):
        calls = Counter()
        def response(url, _cancel):
            mode = parse_qs(urlsplit(url).query)["mode"][0]
            calls[mode] += 1
            return self.block([self.row("猫", restriction=1)] if mode == "r18" else [])
        with patch.object(server, "search_pixiv_json", side_effect=response):
            result = self.search("猫", "all")
        self.assertEqual([row["id"] for row in result["items"]], ["987654"])
        self.assertGreater(calls["r18"], 0)
        self.assertLessEqual(sum(calls.values()), server.MAX_HISTORY_REQUESTS)

    def test_fuzzy_search_reaches_later_alias_even_if_earlier_aliases_are_empty(self):
        calls = Counter()
        def response(url, _cancel):
            tag = parse_qs(urlsplit(url).query)["word"][0]
            calls[tag] += 1
            return self.block([self.row("hatsune miku")] if tag == "hatsune miku" else [])
        with patch.object(server, "search_pixiv_json", side_effect=response):
            result = self.search("初音ミク", fuzzy=True)
        self.assertEqual([row["id"] for row in result["items"]], ["987654"])
        self.assertEqual(len(calls), 4)
        self.assertLessEqual(sum(calls.values()), server.MAX_HISTORY_REQUESTS)

    def test_aliases_prioritize_the_user_spelling_and_keep_equivalent_terms(self):
        for spelling in ("初音ミク", "蕾姆", "碧蓝档案", "Hatsune Miku"):
            with self.subTest(spelling=spelling):
                self.assertEqual(aliases_for(spelling)[0], spelling)
        self.assertEqual(set(aliases_for("初音ミク")), {"miku", "初音未来", "初音ミク", "hatsune miku"})

    def test_slow_first_source_cannot_take_the_entire_time_budget(self):
        now = [0.0]
        calls = Counter()
        def response(url, _cancel):
            mode = parse_qs(urlsplit(url).query)["mode"][0]
            calls[mode] += 1
            now[0] += 6
            return self.block([self.row("猫", restriction=1)] if mode == "r18" else [])
        with patch.object(server.time, "monotonic", side_effect=lambda: now[0]), patch.object(
            server, "search_pixiv_json", side_effect=response,
        ):
            result = self.search("猫", "all")
        self.assertTrue(result["items"])
        self.assertGreater(calls["r18"], 0)
        self.assertLess(calls["safe"], server.MAX_HISTORY_SECONDS / 6)

    def test_more_sources_than_request_slots_rotate_across_searches(self):
        calls = Counter()
        def response(url, _cancel):
            query = parse_qs(urlsplit(url).query)
            calls[(query["word"][0], query["mode"][0])] += 1
            return self.block([])
        with patch.object(server, "search_pixiv_json", side_effect=response):
            query = "miku;saber;asuna;honkai star rail"
            self.search(query, "all", fuzzy=True)
            first_count = sum(calls.values())
            self.search(query, "all", fuzzy=True)
        self.assertLessEqual(first_count, server.MAX_HISTORY_REQUESTS)
        self.assertLessEqual(sum(calls.values()) - first_count, server.MAX_HISTORY_REQUESTS)
        self.assertEqual(len(calls), 32, "later aliases are permanently starved by earlier empty sources")


if __name__ == "__main__":
    unittest.main()
