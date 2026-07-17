from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import server


class ReleaseAuditSecurityTests(unittest.TestCase):
    def setUp(self):
        server.reset_search_caches()

    def tearDown(self):
        server.reset_search_caches()

    def test_health_token_requires_explicit_trusted_request_context(self):
        handler = SimpleNamespace(
            client_address=("127.0.0.1", 12345),
            headers={"Host": "127.0.0.1:8765"},
        )
        self.assertFalse(server.health_request_may_disclose_token(handler))
        handler.headers["Sec-Fetch-Site"] = "same-origin"
        self.assertTrue(server.health_request_may_disclose_token(handler))

    def test_pixiv_request_rejects_malformed_content_length_as_policy_error(self):
        class Headers:
            def get_content_type(self):
                return "application/json"

            def get(self, name):
                return "not-a-number" if name == "Content-Length" else None

        class Response:
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://www.pixiv.net/ajax/test"

            def read(self, _amount):
                return b"{}"

        opener = SimpleNamespace(open=lambda *_args, **_kwargs: Response())
        with patch.object(server, "PIXIV_OPENER", opener):
            with self.assertRaisesRegex(server.PixivPolicyError, "Content-Length"):
                server.pixiv_request("https://www.pixiv.net/ajax/test", attempts=1)

    def test_user_search_parses_current_authenticated_ajax_shape(self):
        payload = {
            "body": {
                "page": {"userIds": [42, 99]},
                "users": [
                    {"id": "99", "name": "相似名称"},
                    {"id": "42", "name": "目标画师"},
                ],
            }
        }
        with patch.object(server, "pixiv_json", return_value=payload):
            self.assertEqual(server.resolve_author_user("目标画师"), ("42", "目标画师"))

    def test_user_search_does_not_accept_user_missing_from_page_ids(self):
        payload = {
            "body": {
                "page": {"userIds": [99]},
                "users": [
                    {"id": "42", "name": "目标画师"},
                    {"id": "99", "name": "相似名称"},
                ],
            }
        }
        with patch.object(server, "pixiv_json", return_value=payload):
            with self.assertRaises(server.SearchInputError):
                server.resolve_author_user("目标画师")

    def test_download_response_exposes_relative_names_not_absolute_paths(self):
        root = server.DOWNLOADS.resolve()
        files = [root / "作品_42" / "作品_42_p0.jpg"]
        self.assertEqual(server.public_saved_files(root, files), ["作品_42/作品_42_p0.jpg"])

    def test_download_public_paths_reject_files_outside_save_root(self):
        root = server.DOWNLOADS.resolve()
        with self.assertRaises(server.PixivPolicyError):
            server.public_saved_files(root, [root.parent / "outside.jpg"])


if __name__ == "__main__":
    unittest.main()
