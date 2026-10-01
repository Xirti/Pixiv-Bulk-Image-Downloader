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


class FlashLandingTests(unittest.TestCase):
    def test_home_has_no_model_logo_or_decorative_script_requests(self):
        page = PageResources((ROOT / "web/index.html").read_text(encoding="utf-8"))
        self.assertFalse(
            [url for url in page.resources if "brand-logos" in url or "brand-stage" in url],
            "Flash still initializes model artwork on startup",
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


class FlashSearchTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
