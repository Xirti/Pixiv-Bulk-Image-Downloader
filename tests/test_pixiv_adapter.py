import unittest
from pathlib import Path

from pixiv_adapter import (
    PixivPolicyError,
    is_allowed_pixiv_url,
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


if __name__ == "__main__":
    unittest.main()
