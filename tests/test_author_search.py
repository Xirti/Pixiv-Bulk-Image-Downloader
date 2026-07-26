from __future__ import annotations

import time
import threading
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
        self.assertEqual(parse_search_query("uid:42"), SearchQuery("uid", "42"))
        self.assertEqual(parse_search_query("UID： 42"), SearchQuery("uid", "42"))
        self.assertEqual(parse_search_query("author:目标画师"), SearchQuery("author", "目标画师"))
        self.assertEqual(parse_search_query("AUTHOR： 目标画师"), SearchQuery("author", "目标画师"))
        with self.assertRaises(SearchInputError):
            parse_search_query("pid:not-a-number")
        with self.assertRaises(SearchInputError):
            parse_search_query("uid:not-a-number")
        with self.assertRaises(SearchInputError):
            parse_search_query("pid:" + "1" * 60 + "suffix")
        with self.assertRaises(SearchInputError):
            parse_search_query("uid:" + "1" * 60 + "suffix")

    def test_pid_search_returns_one_pixiv_detail(self):
        item = {
            "id": "42", "title": "目标作品", "artist": "目标画师",
            "restriction": "safe", "workType": "manga", "aiGenerated": False,
        }
        with patch.object(server, "pixiv_item_for_download", return_value=item) as detail:
            result = server.search_pixiv_results(
                "pid:42", "safe", 1, "manga", False, authorized=False,
            )

        detail.assert_called_once()
        call_args, call_kwargs = detail.call_args
        self.assertEqual(call_args, ("42",))
        self.assertFalse(call_kwargs["allow_r18"])
        self.assertIsNone(call_kwargs["authorization_epoch"])
        self.assertFalse(call_kwargs["cancel_event"].is_set())
        self.assertTrue(call_kwargs["require_thumb"])
        self.assertEqual(result["searchType"], "pid")
        self.assertEqual(result["targetArtworkId"], "42")
        self.assertEqual(result["items"], [item])
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["hasMore"])

    def test_pid_search_applies_type_ai_and_safety_filters(self):
        item = {
            "id": "42", "title": "目标作品", "artist": "目标画师",
            "restriction": "safe", "workType": "ugoira", "aiGenerated": True,
        }
        with patch.object(server, "pixiv_item_for_download", return_value=item):
            type_filtered = server.search_pixiv_results(
                "pid:42", "safe", 1, "manga", True, authorized=False,
            )
            ai_filtered = server.search_pixiv_results(
                "pid:42", "safe", 1, "all", False, authorized=False,
            )
            safety_filtered = server.search_pixiv_results(
                "pid:42", "r18", 1, "all", True,
                authorized=True, authorization_epoch=7,
            )

        self.assertEqual(type_filtered["items"], [])
        self.assertEqual(ai_filtered["items"], [])
        self.assertEqual(safety_filtered["items"], [])
        self.assertEqual(type_filtered["total"], 0)
        self.assertEqual(ai_filtered["total"], 0)
        self.assertEqual(safety_filtered["total"], 0)

    def test_pid_detail_cache_requires_a_current_thumbnail_token(self):
        item = {
            "id": "42",
            "thumb": "/api/pixiv/image?token=missing-thumb",
            "pageImages": [{
                "regular": "/api/pixiv/image?token=page-regular",
                "original": "/api/pixiv/image?token=page-original",
            }],
        }
        expires = time.time() + 60
        tokens = {
            "page-regular": (expires, "42", "https://i.pximg.net/regular.jpg", "safe"),
            "page-original": (expires, "42", "https://i.pximg.net/original.jpg", "safe"),
        }

        with patch.dict(server.IMAGE_TOKENS, tokens, clear=True):
            self.assertFalse(server._item_image_tokens_current(item))

    def test_download_cache_does_not_refresh_for_an_unused_thumbnail(self):
        item = {
            "id": "42", "restriction": "safe",
            "thumb": "/api/pixiv/image?token=expired-thumb",
            "pageImages": [{
                "regular": "/api/pixiv/image?token=page-regular",
                "original": "/api/pixiv/image?token=page-original",
            }],
        }
        expires = time.time() + 60
        tokens = {
            "page-regular": (expires, "42", "https://i.pximg.net/regular.jpg", "safe"),
            "page-original": (expires, "42", "https://i.pximg.net/original.jpg", "safe"),
        }

        with patch.object(server, "get_cached_pixiv_item", return_value=item), patch.dict(
            server.IMAGE_TOKENS, tokens, clear=True,
        ), patch.object(server, "pixiv_detail") as remote_detail:
            result = server.pixiv_item_for_download("42", allow_r18=False)

        self.assertIs(result, item)
        remote_detail.assert_not_called()

    def test_pid_detail_rejects_a_mismatched_upstream_artwork_id(self):
        detail = {
            "illustId": "43", "title": "wrong work", "userId": "2",
            "userName": "artist", "tags": {"tags": []},
            "xRestrict": 0, "isUnlisted": False, "isLoginOnly": False,
            "isMasked": False, "visibilityScope": 0,
        }
        with patch.object(server, "search_pixiv_json", side_effect=[
            {"body": detail}, {"body": []},
        ]):
            with self.assertRaisesRegex(server.PixivPolicyError, "作品 ID 不匹配"):
                server.pixiv_detail("42", cancel_event=threading.Event())

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
            second = server.search_pixiv_results("uid:42", "safe", 2, "all", True, authorized=False)
            first = server.search_pixiv_results("uid:42", "safe", 1, "all", True, authorized=False)
            third = server.search_pixiv_results("uid:42", "safe", 3, "all", True, authorized=False)
        self.assertEqual(len(second["items"]), 36)
        self.assertEqual(len(first["items"]), 36)
        self.assertEqual(first["items"][0]["id"], ids[0])
        self.assertEqual(len(third["items"]), 3)
        self.assertFalse(third["hasMore"])

    def test_author_first_page_exposes_exhausted_partial_second_page(self):
        ids = [str(7000 - index) for index in range(53)]
        with patch.object(server, "resolve_author_user", return_value=("42", "目标画师")), patch.object(
            server, "load_user_profile_ids", return_value=ids,
        ), patch.object(
            server, "load_user_profile_works",
            side_effect=lambda _user_id, artwork_ids: [self.raw(artwork_id) for artwork_id in artwork_ids],
        ):
            first = server.search_pixiv_results(
                "author:目标画师", "safe", 1, "all", True, authorized=False,
            )
            second = server.search_pixiv_results(
                "author:目标画师", "safe", 2, "all", True, authorized=False,
            )

        self.assertEqual(first["total"], 53)
        self.assertEqual(len(first["items"]), 36)
        self.assertEqual(first["availablePages"], [1, 2])
        self.assertEqual(first["preloadedThrough"], 2)
        self.assertEqual(len(second["items"]), 17)
        self.assertFalse(second["hasMore"])

    def test_user_search_bounds_profile_work_requests_and_keeps_cursor(self):
        ids = [str(9000 - index) for index in range(1000)]
        calls = []
        with patch.object(server, "load_user_profile_ids", return_value=ids), patch.object(
            server, "load_user_profile_works",
            side_effect=lambda _user_id, artwork_ids: calls.append(list(artwork_ids)) or [],
        ):
            result = server.search_pixiv_results(
                "uid:42", "safe", 1, "ugoira", True, authorized=False,
            )

        self.assertEqual(len(calls), server.MAX_USER_SEARCH_REQUESTS)
        session_key = ("user", "uid", "42", "42", "safe", "ugoira", True, False)
        self.assertEqual(
            server.SEARCH_SESSIONS[session_key]["profileOffset"],
            server.MAX_USER_SEARCH_REQUESTS * 48,
        )
        self.assertTrue(result["hasMore"])
        self.assertTrue(result["budgetExhausted"])


if __name__ == "__main__":
    unittest.main()
