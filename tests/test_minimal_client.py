import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


class MinimalClientTests(unittest.TestCase):
    def test_custom_background_feature_is_removed_end_to_end(self):
        for source in (HTML, APP, STYLE, SERVER):
            self.assertNotIn("background/random", source)
            self.assertNotIn("imageMode", source)
            self.assertNotIn("artBackdrop", source)
            self.assertNotIn("SAFE_BACKGROUND_TAGS", source)

    def test_expensive_decorative_systems_are_removed(self):
        self.assertNotIn("backdrop-filter", STYLE)
        self.assertNotIn("filter:", STYLE.replace("filter:invert(1)", ""))
        scheduler = APP[APP.index("function schedulePaginationDockUpdate"):APP.index("async function select")]
        self.assertEqual(APP.count("requestAnimationFrame("), 1)
        self.assertIn("requestAnimationFrame(run)", scheduler)
        # The only observer restores visible basket thumbnails, not decoration.
        self.assertEqual(APP.count("new IntersectionObserver("), 1)
        thumbnails = APP[APP.index("function loadBasketThumbnails"):APP.index("function selectedGroups")]
        self.assertIn('root: $("#basketPage")', thumbnails)
        self.assertNotIn("bindRipple", APP)
        self.assertNotIn("previewMode", HTML)
        self.assertNotIn("motion", HTML)

    def test_lite_removes_brand_assets_and_loads_theme_before_styles(self):
        self.assertNotIn("brand-stage", HTML)
        self.assertFalse((ROOT / "web" / "brand-stage.js").exists())
        self.assertFalse(list((ROOT / "web" / "brand-logos").glob("*.svg")))
        self.assertLess(HTML.index('src="/theme.js"'), HTML.index('href="/style.css"'))

if __name__ == "__main__":
    unittest.main()
