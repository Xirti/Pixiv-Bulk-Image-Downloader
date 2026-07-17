from __future__ import annotations

import unittest
from unittest.mock import patch

import server
from search_service import SearchInputError, SearchQuery, parse_search_query


class AuthorSearchRegressionTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()

    def tearDown(self):
        server.reset_search_caches()

    @staticmethod
    def raw(artwork_id: str, user_id: str = "42") -> dict:
        return {
            "id": artwork_id,
            "title": f"work-{artwork_id}",
            "userName": "目标画师" if user_id == "42" else "其他画师",
            "userId": user_id,
            "url": f"https://i.pximg.net/{artwork_id}.jpg",
            "tags": ["原创"],
            "pageCount": 1,
            "width": 100,
            "height": 200,
            "bookmarkCount": 1,
            "createDate": "2026-07-16",
            "xRestrict": 0,
            "isUnlisted": False,
            "isMasked": False,
            "visibilityScope": 0,
            "illustType": 0,
            "aiType": 1,
        }

    def test_query_prefixes_accept_ascii_and_fullwidth_colons(self):
        self.assertEqual(parse_search_query("pid:42"), SearchQuery("pid", "42"))
        self.assertEqual(parse_search_query("PID： 42"), SearchQuery("pid", "42"))
        self.assertEqual(parse_search_query("author:目标画师"), SearchQuery("author", "目标画师"))
        self.assertEqual(parse_search_query("AUTHOR： 目标画师"), SearchQuery("author", "目标画师"))
        with self.assertRaises(SearchInputError):
            parse_search_query("pid:not-a-number")

    def test_legacy_nested_user_preview_parser_remains_bounded(self):
        payload = {
            "body": {
                "userPreviews": [
                    {"user": {"userId": "42", "name": "目标画师"}, "illusts": []},
                    {"user": {"userId": "99", "name": "相似名称"}, "illusts": []},
                ]
            }
        }
        self.assertEqual(server._user_rows(payload["body"])[0]["userId"], "42")

    def test_author_resolution_supports_current_ajax_shape(self):
        payload = {
            "body": {
                "page": {"userIds": [42, 99]},
                "users": [
                    {"id": "42", "name": "目标画师"},
                    {"id": "99", "name": "相似名称"},
                ],
            }
        }
        with patch.object(server, "pixiv_json", return_value=payload):
            self.assertEqual(server.resolve_author_user("目标画师"), ("42", "目标画师"))

    def test_author_resolution_uses_current_ajax_search_users_route(self):
        payload = {
            "body": {
                "page": {"userIds": [42]},
                "users": [{"id": "42", "name": "目标画师"}],
            }
        }
        requested = []
        with patch.object(
            server, "pixiv_json",
            side_effect=lambda url: requested.append(url) or payload,
        ):
            self.assertEqual(server.resolve_author_user("目标画师"), ("42", "目标画师"))
        self.assertEqual(len(requested), 1)
        self.assertIn("/ajax/search/users?", requested[0])
        self.assertIn("nick=", requested[0])

    def test_author_search_fetches_only_resolved_user_works(self):
        with patch.object(server, "resolve_author_user", return_value=("42", "目标画师")), patch.object(
            server, "load_user_profile_ids", return_value=["3", "2", "1"]
        ), patch.object(
            server, "load_user_profile_works",
            return_value=[self.raw("3"), self.raw("2", "99"), self.raw("1")],
        ):
            result = server.search_pixiv_results(
                "author:目标画师", "safe", 1, "all", True, authorized=False
            )
        self.assertEqual(result["searchType"], "author")
        self.assertEqual(result["targetUserId"], "42")
        self.assertEqual([row["id"] for row in result["items"]], ["3", "1"])
        self.assertTrue(all(row["userId"] == "42" for row in result["items"]))

    def test_user_search_pages_forward_and_back_without_empty_results(self):
        ids = [str(5000 - index) for index in range(75)]
        with patch.object(server, "load_user_profile_ids", return_value=ids), patch.object(
            server, "load_user_profile_works",
            side_effect=lambda _user_id, artwork_ids: [self.raw(artwork_id) for artwork_id in artwork_ids],
        ):
            second = server.search_pixiv_results("pid:42", "safe", 2, "all", True, authorized=False)
            first = server.search_pixiv_results("pid:42", "safe", 1, "all", True, authorized=False)
            third = server.search_pixiv_results("pid:42", "safe", 3, "all", True, authorized=False)
        self.assertEqual(len(second["items"]), 36)
        self.assertEqual(len(first["items"]), 36)
        self.assertEqual(first["items"][0]["id"], ids[0])
        self.assertEqual(len(third["items"]), 3)
        self.assertFalse(third["hasMore"])


if __name__ == "__main__":
    unittest.main()
