import unittest
from pathlib import Path
from unittest.mock import patch

import auth_store

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


class FeatureUpgradeTests(unittest.TestCase):
    def tearDown(self):
        auth_store.clear_memory_session()

    def test_temporary_session_never_writes_credential_manager(self):
        with patch.object(auth_store, "write_persistent_session") as persistent, patch.object(
            auth_store, "delete_persistent_session"
        ) as deleted:
            auth_store.store_session("temporary123", remember=False)
        persistent.assert_not_called()
        deleted.assert_called_once_with()
        self.assertEqual(auth_store.read_session(), "temporary123")

    def test_persistent_session_uses_credential_manager(self):
        with patch.object(auth_store, "write_persistent_session") as persistent:
            auth_store.store_session("persistent123", remember=True)
        persistent.assert_called_once_with("persistent123")

    def test_login_ui_exposes_explicit_remember_choice(self):
        self.assertIn('id="rememberLogin"', HTML)
        self.assertIn("pixiv_login(remember)", APP)

    def test_gallery_has_multi_select_and_batch_workspace(self):
        self.assertIn('id="selectionBar"', HTML)
        self.assertIn('id="batchWorkspace"', HTML)
        self.assertIn("selectedArtworkIds", APP)
        self.assertIn("openSelectionBasket", APP)
        self.assertIn("openBasketArtworkPicker", APP)

    def test_batch_download_endpoint_is_bounded_and_page_selective(self):
        self.assertIn('"/api/pixiv/batch-download"', SERVER)
        self.assertIn("DOWNLOAD_CHUNK_ARTWORKS", SERVER)
        self.assertIn("DOWNLOAD_CHUNK_PAGES", SERVER)
        self.assertIn("selected_pages", SERVER)

    def test_deck_click_locks_one_card_and_ignores_other_cards(self):
        self.assertIn("toggleDeckCard(card)", APP)
        self.assertIn("lockedDeckPage", APP)
        self.assertIn('row.classList.toggle("deck-inert", !selected)', APP)
        self.assertIn('row.setAttribute("aria-pressed", String(selected))', APP)


if __name__ == "__main__":
    unittest.main()
