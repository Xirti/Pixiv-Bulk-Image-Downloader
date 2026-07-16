import unittest
from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from pixiv_adapter import PixivPolicyError, build_search_url, normalize_search_item


class HistoryAndR18Tests(unittest.TestCase):
    def _item(self, restrict=0):
        return {
            "id": "123", "title": "t", "userName": "u", "userId": "9",
            "url": "https://i.pximg.net/a.jpg", "tags": [], "pageCount": 1,
            "width": 10, "height": 20, "xRestrict": restrict,
            "isUnlisted": False, "isMasked": False, "visibilityScope": 0,
        }

    def test_search_url_supports_bounded_date_and_r18_mode(self):
        url = build_search_url("猫", 2, mode="r18", start_date=date(2026, 7, 1), end_date=date(2026, 7, 12))
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["mode"], ["r18"])
        self.assertEqual(query["scd"], ["2026-07-01"])
        self.assertEqual(query["ecd"], ["2026-07-12"])

    def test_search_url_rejects_unknown_mode(self):
        with self.assertRaises(PixivPolicyError):
            build_search_url("猫", 1, mode="r18g")

    def test_r18_item_requires_explicit_authorized_scope(self):
        with self.assertRaises(PixivPolicyError):
            normalize_search_item(self._item(1))
        item = normalize_search_item(self._item(1), allow_r18=True)
        self.assertEqual(item["restriction"], "r18")

    def test_r18_scope_does_not_allow_r18g(self):
        with self.assertRaises(PixivPolicyError):
            normalize_search_item(self._item(2), allow_r18=True)

    def test_r18_search_must_return_actual_r18_rows(self):
        import server
        with patch.object(server, "pixiv_json", return_value={"body":{"illustManga":{"total":1,"lastPage":1,"data":[self._item(0)]}}}):
            server.HISTORY_CACHE.clear()
            with self.assertRaises(PixivPolicyError):
                server.extend_history("猫", "r18", 1, allow_r18=True)

    def test_empty_history_respects_shared_request_budget(self):
        import server

        calls = []

        def empty(_url):
            calls.append(1)
            return {"body": {"illustManga": {"total": 0, "lastPage": 0, "data": []}}}

        key = ("预算空结果", "safe")
        server.HISTORY_CACHE.pop(key, None)
        with patch.object(server, "pixiv_json", side_effect=empty):
            state = server.extend_history(*key, 36)
        self.assertLessEqual(len(calls), server.MAX_HISTORY_REQUESTS)
        self.assertTrue(state["budgetExhausted"])
        self.assertTrue(state["queue"] or not state["exhausted"])
        server.HISTORY_CACHE.pop(key, None)
        server._HISTORY_LOCKS.pop(key, None)


if __name__ == "__main__":
    unittest.main()
