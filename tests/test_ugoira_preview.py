import shutil
import subprocess
import unittest
from pathlib import Path


class UgoiraPreviewTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_preview_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--test", str(root / "tests" / "ugoira_preview.test.js")],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
