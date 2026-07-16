import unittest
from pixiv_adapter import build_search_url, normalize_detail


class PixivNetworkModelTests(unittest.TestCase):
    def test_search_url_encodes_tag_and_forces_safe_mode(self):
        url = build_search_url("猫 耳", 2)
        self.assertIn("%E7%8C%AB%20%E8%80%B3", url)
        self.assertIn("mode=safe", url)
        self.assertIn("p=2", url)

    def test_detail_rejects_login_only_and_returns_proxy_pages(self):
        raw = {"illustId":"93172108","title":"猫","userId":"2","userName":"作者","tags":{"tags":[{"tag":"猫"}]},"width":100,"height":80,"pageCount":1,"bookmarkCount":3,"createDate":"2021-01-01","xRestrict":0,"isMasked":False,"isUnlisted":False,"visibilityScope":0,"isLoginOnly":False,"urls":{"regular":"https://i.pximg.net/a.jpg","original":"https://i.pximg.net/b.jpg"}}
        item = normalize_detail(raw, [{"width":100,"height":80,"urls":{"regular":"https://i.pximg.net/a.jpg","original":"https://i.pximg.net/b.jpg"}}])
        self.assertEqual(item["pages"], 1)
        self.assertIn("/api/pixiv/image?", item["pageImages"][0]["original"])
        raw["isLoginOnly"] = True
        with self.assertRaises(Exception):
            normalize_detail(raw, [])

if __name__ == "__main__": unittest.main()
