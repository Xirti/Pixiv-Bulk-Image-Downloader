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

    def test_deck_cards_never_overlap_and_lock_without_recentering(self):
        deck_css = STYLE[STYLE.index(".deck{"):STYLE.index(".detail article")]
        self.assertIn("display:flex", deck_css)
        self.assertIn("flex:1 1 0", deck_css)
        self.assertNotIn("position:absolute;width:56%", deck_css)
        self.assertIn("toggleDeckCard(card)", APP)
        self.assertIn("if (lockedDeckPage !== null && lockedDeckPage !== page) return", APP)
        self.assertIn("其他牌保持不动", APP)
        self.assertNotIn("focusDeckStack", APP)


if __name__ == "__main__":
    unittest.main()
