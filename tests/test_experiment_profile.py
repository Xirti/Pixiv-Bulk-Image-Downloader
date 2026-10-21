import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import auth_store
import download_history
import experiment_profile
import moku_app
import server


class ExperimentIsolationTests(unittest.TestCase):
    def test_credential_namespace_is_independent(self):
        self.assertEqual(auth_store.TARGET, "MOKU.GlassLab01.Pixiv.PHPSESSID")
        self.assertNotEqual(auth_store.TARGET, "MOKU.Pixiv.PHPSESSID")

    def test_defaults_live_in_experimental_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}, clear=True):
                base = Path(directory) / experiment_profile.DATA_FOLDER
                self.assertEqual(download_history.history_path(), base / 'library/downloads.sqlite3')
                self.assertEqual(moku_app.runtime_directory(), base / 'runtime')

    def test_explicit_test_directories_still_work(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"MOKU_LIBRARY_DIR": directory, "MOKU_RUNTIME_DIR": directory}):
                self.assertEqual(download_history.history_path(), Path(directory) / 'downloads.sqlite3')
                self.assertEqual(moku_app.runtime_directory(), Path(directory))

    def test_backend_identity_and_lock_are_independent(self):
        self.assertEqual(server.APPLICATION_ID, 'MOKU.PixivTagGallery.GlassLab01')
        self.assertEqual(moku_app.MUTEX_NAME, experiment_profile.MUTEX_NAME)
        self.assertNotIn('PixivTagGallery.Backend', moku_app.MUTEX_NAME)

    def test_browser_handshake_matches_experimental_backend(self):
        frontend = (server.ROOT / 'web/app.js').read_text(encoding='utf-8')
        self.assertIn('data.applicationId !== "' + server.APPLICATION_ID + '"', frontend)

    def test_optical_assets_participate_in_fingerprint(self):
        names = set(server.CODE_GENERATION_FILES)
        self.assertIn('web/glass-lab.js', names)
        self.assertIn('web/vendor/lens/internal/renderer.js', names)
        self.assertIn('experiment_profile.py', names)
        self.assertTrue(all((server.ROOT / name).is_file() for name in names))


if __name__ == '__main__':
    unittest.main()
