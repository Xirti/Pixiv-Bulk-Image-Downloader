import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "web" / "app.js"
STYLE = ROOT / "web" / "style.css"
LAUNCHER = ROOT / "launch-moku.ps1"
MOKU_APP = ROOT / "moku_app.py"


class FrontendStartupBudgetTests(unittest.TestCase):
    def test_startup_does_not_implicitly_fetch_gallery_or_background(self):
        source = APP.read_text(encoding="utf-8")
        self.assertNotIn("search('猫耳')", source)
        self.assertNotIn("setThemeMode(savedTheme)", source)
        self.assertIn("requestIdleCallback", source)

    def test_network_requests_have_timeout_and_search_is_cancellable(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("AbortController", source)
        self.assertIn("fetchJson", source)
        self.assertIn("searchController.abort()", source)
        self.assertIn("finally", source)

    def test_first_search_does_not_automatically_fetch_artwork_detail(self):
        source = APP.read_text(encoding="utf-8")
        self.assertNotIn("renderPagination();select(0)", source)
        self.assertIn("选择一件作品查看详情", source)

    def test_css_has_no_remote_font_import_or_full_results_blur(self):
        source = STYLE.read_text(encoding="utf-8")
        self.assertNotIn("fonts.googleapis.com", source)
        self.assertNotIn(".results{padding:90px 5vw;background:var(--section-wash);backdrop-filter:blur", source)

    def test_desktop_uses_disposable_webview2_profile_and_clears_foreign_override(self):
        source = MOKU_APP.read_text(encoding="utf-8")
        self.assertIn('os.environ.pop("WEBVIEW2_USER_DATA_FOLDER", None)', source)
        self.assertIn('os.environ["WEBVIEW2_USER_DATA_FOLDER"] = inherited', source)
        self.assertIn('"MOKU" / "WebView2Sessions"', source)
        self.assertIn('mkdtemp(prefix="session-"', source)
        self.assertIn("remove_webview_profile(storage_path)", source)
        self.assertIn("launch_desktop(url", source)

    def test_conservative_mode_disables_continuous_decorative_work(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn('document.documentElement.classList.add("conservative")', source)
        self.assertNotIn("setInterval(star", source)

    def test_css_removes_expensive_visual_effects(self):
        source = STYLE.read_text(encoding="utf-8")
        self.assertNotIn("backdrop-filter", source)
        self.assertNotIn("filter:", source)


if __name__ == "__main__":
    unittest.main()
