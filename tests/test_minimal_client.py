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
        self.assertNotIn("filter:", STYLE)
        self.assertNotIn("requestAnimationFrame", APP)
        self.assertNotIn("IntersectionObserver", APP)
        self.assertNotIn("bindRipple", APP)
        self.assertNotIn("previewMode", HTML)
        self.assertNotIn("motion", HTML)

    def test_browser_launcher_waits_for_http_and_keeps_diagnostics(self):
        launcher = (ROOT / "launch-moku.ps1").read_text(encoding="utf-8-sig")
        wrapper = (ROOT / "启动MOKU.cmd").read_text(encoding="utf-8-sig")
        self.assertIn("/api/health", launcher)
        self.assertIn("Invoke-WebRequest", launcher)
        self.assertIn("Start-Process", launcher)
        self.assertIn("launcher.log", launcher)
        self.assertIn("launch-moku.ps1", wrapper)


if __name__ == "__main__":
    unittest.main()
