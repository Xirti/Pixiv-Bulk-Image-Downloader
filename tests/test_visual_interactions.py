import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


class VisualInteractionTests(unittest.TestCase):
    def test_removed_background_and_ripple_stay_removed(self):
        self.assertNotIn("background/random", SERVER)
        self.assertNotIn("bindRipple", APP)
        self.assertNotIn("preview-grid", STYLE)


if __name__ == "__main__":
    unittest.main()
