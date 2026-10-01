import unittest
from pathlib import Path

from pixiv_adapter import (
    PixivPolicyError,
    build_ugoira_meta_url,
    is_allowed_pixiv_url,
    normalize_detail,
    normalize_search_item,
    safe_download_name,
)


class PixivAdapterTests(unittest.TestCase):
    def test_allows_only_https_pixiv_api_and_image_hosts(self):
        self.assertTrue(is_allowed_pixiv_url("https://www.pixiv.net/ajax/illust/93172108"))
        self.assertTrue(is_allowed_pixiv_url("https://i.pximg.net/img-original/a.jpg", image_only=True))
        self.assertFalse(is_allowed_pixiv_url("http://i.pximg.net/a.jpg", image_only=True))
        self.assertFalse(is_allowed_pixiv_url("https://i.pximg.net.evil.example/a.jpg", image_only=True))
        self.assertFalse(is_allowed_pixiv_url("https://127.0.0.1/a.jpg"))

    def test_image_cdn_requires_image_only_flag(self):
        self.assertFalse(is_allowed_pixiv_url("https://i.pximg.net/a.jpg"))
        self.assertTrue(is_allowed_pixiv_url("https://i.pximg.net/a.jpg", image_only=True))

    def test_normalizes_public_safe_search_item(self):
        raw = {
            "id": "93172108", "title": "<猫>", "description": "<p>柔软<br>夜色</p>",
            "userId": "42", "userName": "作者", "tags": ["猫", "原创"],
            "width": 2400, "height": 1800, "pageCount": 5,
            "bookmarkCount": 123, "createDate": "2021-10-02T18:47:29+09:00",
            "url": "https://i.pximg.net/c/250x250_80_a2/img-master/example.jpg",
            "xRestrict": 0, "isMasked": False, "isUnlisted": False,
            "visibilityScope": 0,
        }
        item = normalize_search_item(raw)
        self.assertEqual(item["id"], "93172108")
        self.assertEqual(item["title"], "<猫>")
        self.assertEqual(item["description"], "柔软 夜色")
        self.assertEqual(item["pages"], 5)
        self.assertEqual(item["source"], "pixiv")
        self.assertTrue(item["thumb"].startswith("/api/pixiv/image?"))

    def test_rejects_restricted_or_masked_item(self):
        base = {"id": "1", "url": "https://i.pximg.net/a.jpg", "xRestrict": 1}
        with self.assertRaises(PixivPolicyError):
            normalize_search_item(base)
        base["xRestrict"] = 0
        base["isMasked"] = True
        with self.assertRaises(PixivPolicyError):
            normalize_search_item(base)

    def test_download_name_is_stable_and_has_no_path_components(self):
        name = safe_download_name("93172108", 2, "jpg")
        self.assertEqual(name, "93172108_p2.jpg")
        self.assertEqual(Path(name).name, name)
        with self.assertRaises(ValueError):
            safe_download_name("../bad", 0, "jpg")

    def test_ugoira_detail_tolerates_missing_original_and_exposes_optional_exports(self):
        raw = {
            "illustId": "990101", "title": "动图", "userName": "作者",
            "userId": "7", "tags": {"tags": [{"tag": "うごイラ"}]},
            "width": 770, "height": 1120, "pageCount": 1,
            "bookmarkCount": 5, "createDate": "2024-02-03T10:00:00+09:00",
            "illustType": 2, "aiType": 1,
            "xRestrict": 0, "isUnlisted": False, "isLoginOnly": False,
            "isMasked": False, "visibilityScope": 0,
        }
        pages = [{
            "width": 770, "height": 1120,
            "urls": {
                "regular": "https://i.pximg.net/c/540x540/img-master/990101_p0.jpg",
                "original": None,
            },
        }]
        item = normalize_detail(raw, pages)
        self.assertEqual(item["workType"], "ugoira")
        self.assertEqual(item["pages"], 1)
        self.assertEqual(item["pageImages"][0]["original"], "")
        self.assertTrue(item["pageImages"][0]["regular"].startswith("/api/pixiv/image?"))
        self.assertTrue(item["thumb"].startswith("/api/pixiv/image?"))
        self.assertEqual(item["qualities"][0]["id"], "original")
        self.assertIn("清晰度", item["qualities"][0]["label"])
        self.assertIn("ZIP", item["formats"][0]["label"])
        self.assertEqual([format["id"] for format in item["formats"]], ["source", "gif", "mp4"])

    def test_ugoira_meta_url_stays_on_the_approved_ajax_host(self):
        self.assertEqual(
            build_ugoira_meta_url("990101"),
            "https://www.pixiv.net/ajax/illust/990101/ugoira_meta?lang=zh",
        )
        with self.assertRaises(PixivPolicyError):
            build_ugoira_meta_url("../escape")


if __name__ == "__main__":
    unittest.main()
