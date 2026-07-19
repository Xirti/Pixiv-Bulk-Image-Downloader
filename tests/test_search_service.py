from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from search_service import (
    SearchInputError,
    SearchPageCache,
    build_search_tag_groups,
    parse_search_tags,
    prefetch_item_count,
    resolve_source_modes,
)


class SearchServiceTests(unittest.TestCase):
    def test_semicolon_separated_tags_are_deduplicated_and_spaces_stay_inside_tag(self):
        self.assertEqual(
            parse_search_tags("  猫；耳机；猫；星 夜  "),
            ("猫", "耳机", "星 夜"),
        )
        self.assertEqual(build_search_tag_groups("猫 夜景"), (("猫 夜景",),))

    def test_tag_parser_bounds_target_count_and_length(self):
        tags = parse_search_tags("；".join(["a" * 80, "b", "c", "d", "e", "f", "g"]))
        self.assertEqual(len(tags), 6)
        self.assertEqual(tags[0], "a" * 60)
        self.assertEqual(parse_search_tags("   "), ("原创",))

    def test_all_scope_expands_to_safe_and_r18_only_when_authorized(self):
        self.assertEqual(resolve_source_modes("safe", authorized=False), ("safe",))
        self.assertEqual(resolve_source_modes("r18", authorized=True), ("r18",))
        self.assertEqual(resolve_source_modes("all", authorized=True), ("safe", "r18"))
        with self.assertRaises(SearchInputError):
            resolve_source_modes("all", authorized=False)
        with self.assertRaises(SearchInputError):
            resolve_source_modes("r18", authorized=False)

    def test_prefetch_count_includes_three_pages_ahead(self):
        self.assertEqual(prefetch_item_count(1, per_page=36, ahead=3), 144)
        self.assertEqual(prefetch_item_count(5, per_page=36, ahead=3), 288)

    def test_page_cache_exposes_preloaded_range_and_evicts_far_behind(self):
        cache = SearchPageCache(keep_behind=2, max_sessions=3)
        key = ("猫", "safe", "all", True)
        cache.store_pages(key, current_page=1, pages={page: [page] for page in range(1, 5)})
        self.assertEqual(cache.available_pages(key), [1, 2, 3, 4])
        self.assertEqual(cache.get_page(key, 4), [4])

        cache.store_pages(key, current_page=5, pages={page: [page] for page in range(3, 9)})
        self.assertEqual(cache.available_pages(key), [3, 4, 5, 6, 7, 8])
        self.assertIsNone(cache.get_page(key, 1))
        self.assertEqual(cache.preloaded_through(key), 8)

    def test_page_cache_evicts_old_search_sessions_by_lru(self):
        cache = SearchPageCache(keep_behind=1, max_sessions=2)
        first = ("a",)
        second = ("b",)
        third = ("c",)
        cache.store_pages(first, 1, {1: [1]})
        cache.store_pages(second, 1, {1: [2]})
        self.assertEqual(cache.get_page(first, 1), [1])
        cache.store_pages(third, 1, {1: [3]})
        self.assertIsNone(cache.get_page(second, 1))
        self.assertEqual(cache.get_page(first, 1), [1])

    def test_page_cache_is_thread_safe_across_distinct_sessions(self):
        cache = SearchPageCache(keep_behind=2, max_sessions=12)
        errors = []

        def worker(index):
            try:
                key = (f"tag-{index}",)
                for page in range(1, 80):
                    cache.store_pages(key, page, {page: [index, page]})
                    cache.get_page(key, page)
                    cache.available_pages(key)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_search_sessions_have_isolated_history_cursor_state(self):
        import server
        first = (("猫",), "safe", "all", True)
        second = (("猫",), "safe", "manga", True)
        first_state = server._history_state("猫", "safe", namespace=first)
        first_state["baseOffset"] = 252
        second_state = server._history_state("猫", "safe", namespace=second)
        self.assertIsNot(first_state, second_state)
        self.assertEqual(second_state["baseOffset"], 0)

    def test_dropping_search_session_removes_its_private_history_sources(self):
        import server
        key = (("猫",), "safe", "all", True)
        server.SEARCH_SESSIONS[key] = {"items": []}
        server._history_state("猫", "safe", namespace=key)
        server._drop_search_session(key)
        self.assertFalse(any(isinstance(row, tuple) and len(row) == 4 and row[1] == key for row in server.HISTORY_CACHE))

    def test_history_trim_uses_only_offsets_from_its_own_search_session(self):
        import server
        first = (("猫",), "safe", "all", True)
        second = (("猫",), "safe", "manga", True)
        state = server._history_state("猫", "safe", namespace=first)
        state["items"] = [{"id": str(index)} for index in range(400)]
        state["ids"] = {str(index) for index in range(400)}
        server.SEARCH_SOURCE_OFFSETS[(first, "猫", "safe")] = 360
        server.SEARCH_SOURCE_OFFSETS[(second, "猫", "safe")] = 1

        server._trim_history_source(first, "猫", "safe")

        self.assertEqual(state["baseOffset"], 144)
        self.assertEqual(state["items"][0]["id"], "144")


class SearchAggregationTests(unittest.TestCase):
    def setUp(self):
        import server
        server.reset_search_caches()

    def tearDown(self):
        import server
        server.reset_search_caches()

    @staticmethod
    def _raw(artwork_id: int, tag: str, restriction: int, day: int, *, all_tags=None) -> dict:
        return {
            "id": str(artwork_id), "title": f"{tag}-{artwork_id}", "userName": "u", "userId": "9",
            "url": f"https://i.pximg.net/{artwork_id}.jpg", "tags": list(all_tags or [tag]), "pageCount": 1,
            "width": 10, "height": 20, "xRestrict": restriction, "isUnlisted": False,
            "isMasked": False, "visibilityScope": 0, "illustType": 0, "aiType": 1,
            "createDate": f"2026-07-{day:02d}",
        }

    def test_multi_tag_all_scope_requires_all_tags_and_deduplicates(self):
        import server
        calls = []

        def fake_source(_session_key, tag, mode, _need, _allow_r18, _budget):
            calls.append((tag, mode))
            restriction = 1 if mode == "r18" else 0
            base = (1000 if tag == "猫" else 2000) + (500 if mode == "r18" else 0)
            rows = [
                self._raw(base + index, tag, restriction, 28 - index % 20, all_tags=["猫", "夜景"])
                for index in range(45)
            ]
            rows.append(self._raw(9999, tag, restriction, 30, all_tags=["猫", "夜景"]))
            return {"rows": rows, "hasMore": False, "budgetExhausted": False, "truncatedDates": []}

        with patch.object(server, "load_search_source", side_effect=fake_source):
            result = server.search_pixiv_results("猫；夜景", "all", 1, "all", True, authorized=True)

        self.assertEqual(set(calls), {("猫", "safe"), ("猫", "r18"), ("夜景", "safe"), ("夜景", "r18")})
        self.assertEqual(result["tags"], ["猫", "夜景"])
        self.assertEqual(result["tag"], "猫；夜景")
        self.assertTrue(all({"猫", "夜景"}.issubset(item["tags"]) for item in result["items"]))
        self.assertEqual(result["scope"], "all")
        self.assertEqual(len({item["id"] for item in result["items"]}), len(result["items"]))
        self.assertTrue({item["restriction"] for item in result["items"]}.issuperset({"safe", "r18"}))
        self.assertEqual(result["availablePages"], [1, 2, 3, 4])
        self.assertEqual(result["preloadedThrough"], 4)


    def test_multi_tag_paging_keeps_and_query_and_evicts_only_stale_preview_tokens(self):
        import urllib.parse
        import server

        def fake_source(session_key, tag, mode, need_count, allow_r18, budget):
            base = 10000 if tag == "猫" else 20000
            rows = [
                self._raw(base - index, tag, 0, 28 - index % 20, all_tags=["猫", "夜景"])
                for index in range(600)
            ]
            offset_key = (session_key, tag, mode)
            offset = server.SEARCH_SOURCE_OFFSETS.get(offset_key, 0)
            selected = rows[offset:min(need_count, len(rows))]
            server.SEARCH_SOURCE_OFFSETS[offset_key] = offset + len(selected)
            return {"rows": selected, "hasMore": need_count < len(rows), "budgetExhausted": False, "truncatedDates": []}

        server.IMAGE_TOKENS.clear()
        with patch.object(server, "load_search_source", side_effect=fake_source):
            first = server.search_pixiv_results("猫；夜景", "safe", 1, "all", True, authorized=False)
            first_tokens = {
                urllib.parse.parse_qs(urllib.parse.urlsplit(item["thumb"]).query)["token"][0]
                for item in first["items"]
            }
            self.assertEqual(first["tags"], ["猫", "夜景"])
            self.assertEqual(first["tag"], "猫；夜景")
            self.assertEqual(len(server.IMAGE_TOKENS), len(first["items"]))
            self.assertEqual(first["availablePages"], [1, 2, 3, 4])

            second = server.search_pixiv_results("猫；夜景", "safe", 2, "all", True, authorized=False)
            second_tokens = {
                urllib.parse.parse_qs(urllib.parse.urlsplit(item["thumb"]).query)["token"][0]
                for item in second["items"]
            }
            deep = server.search_pixiv_results("猫；夜景", "safe", 8, "all", True, authorized=False)

        self.assertEqual(second["tags"], ["猫", "夜景"])
        self.assertEqual(second["tag"], "猫；夜景")
        self.assertTrue(any("猫" in item["tags"] for item in second["items"]))
        self.assertTrue(any("夜景" in item["tags"] for item in second["items"]))
        self.assertEqual(deep["tags"], ["猫", "夜景"])
        self.assertTrue(first_tokens.isdisjoint(server.IMAGE_TOKENS))
        self.assertTrue(second_tokens.issubset(server.IMAGE_TOKENS))
        key = ("tags", (("猫",), ("夜景",)), "safe", "all", True, False)
        self.assertIsNone(server.SEARCH_PAGE_CACHE.get_page(key, 1))
        self.assertIsNotNone(server.SEARCH_PAGE_CACHE.get_page(key, 2))

    def test_search_preview_tokens_use_no_store_http_cache(self):
        import server
        self.assertEqual(server.image_token_cache_control((0, "1", "url", "safe", ("search",), 2)), "no-store")
        self.assertEqual(server.image_token_cache_control((0, "1", "url", "safe")), "private,max-age=3600")

    def test_forward_navigation_evicts_pages_far_behind_current_page(self):
        import server

        def fake_source(session_key, tag, mode, need_count, allow_r18, budget):
            rows = [self._raw(10000 - index, tag, 1 if mode == "r18" else 0, 28 - index % 20) for index in range(600)]
            offset_key = (session_key, tag, mode)
            offset = server.SEARCH_SOURCE_OFFSETS.get(offset_key, 0)
            selected = rows[offset:min(need_count, len(rows))]
            server.SEARCH_SOURCE_OFFSETS[offset_key] = offset + len(selected)
            return {"rows": selected, "hasMore": need_count < len(rows), "budgetExhausted": False, "truncatedDates": []}

        with patch.object(server, "load_search_source", side_effect=fake_source):
            first = server.search_pixiv_results("猫", "safe", 1, "all", True, authorized=False)
            tenth = server.search_pixiv_results("猫", "safe", 10, "all", True, authorized=False)

        self.assertEqual(first["availablePages"], [1, 2, 3, 4])
        self.assertEqual(tenth["availablePages"], list(range(4, 14)))
        self.assertEqual(tenth["preloadedThrough"], 13)
        key = ("tags", (("猫",),), "safe", "all", True, False)
        self.assertIsNone(server.SEARCH_PAGE_CACHE.get_page(key, 3))
        self.assertIsNotNone(server.SEARCH_PAGE_CACHE.get_page(key, 4))
        retained = server.SEARCH_SESSIONS[key]["items"]
        self.assertEqual(
            server.SEARCH_SESSIONS[key]["seen"],
            {row["id"] for row in retained},
        )

    def test_requesting_evicted_first_page_restarts_the_search_session(self):
        import server

        def fake_source(session_key, tag, mode, need_count, allow_r18, budget):
            rows = [self._raw(10000 - index, tag, 1 if mode == "r18" else 0, 28 - index % 20) for index in range(600)]
            offset_key = (session_key, tag, mode)
            offset = server.SEARCH_SOURCE_OFFSETS.get(offset_key, 0)
            selected = rows[offset:min(need_count, len(rows))]
            server.SEARCH_SOURCE_OFFSETS[offset_key] = offset + len(selected)
            return {"rows": selected, "hasMore": need_count < len(rows), "budgetExhausted": False, "truncatedDates": []}

        with patch.object(server, "load_search_source", side_effect=fake_source):
            original = server.search_pixiv_results("猫", "safe", 1, "all", True, authorized=False)
            server.search_pixiv_results("猫", "safe", 10, "all", True, authorized=False)
            restarted = server.search_pixiv_results("猫", "safe", 1, "all", True, authorized=False)

        self.assertEqual(restarted["page"], 1)
        self.assertEqual(restarted["availablePages"], [1, 2, 3, 4])
        self.assertEqual(
            [row["id"] for row in restarted["items"]],
            [row["id"] for row in original["items"]],
        )

    def test_ui_offers_all_scope_and_explains_semicolon_separated_tags(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        app = (root / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('<option value="all" disabled>全部类型（全年龄 + R-18，需授权）</option>', html)
        self.assertIn("多个标签用 ; 或 ； 分开", html)
        self.assertIn("availablePages", app)
        self.assertIn("preloadedThrough", app)
        self.assertIn("firstAvailablePage", app)
        self.assertIn("currentPage <= firstAvailablePage", app)
        self.assertIn("}, 90000);", app)


if __name__ == "__main__":
    unittest.main()
