from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EDGE = any((Path(directory) / "Microsoft/Edge/Application/msedge.exe").is_file() for directory in (
    os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
    os.environ.get("ProgramFiles", "C:/Program Files"),
))


class TextContextMenuTests(unittest.TestCase):
    @unittest.skipUnless(EDGE and importlib.util.find_spec("playwright"), "Edge and Playwright required for real popup checks")
    def test_actual_right_click_popup_and_text_operations(self):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/text_context_menu_browser_probe.py")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
            env={**os.environ, "PYTHONIOENCODING":"utf-8"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"cutCopyPasteSelectAll": true', result.stdout)


if __name__ == "__main__":
    unittest.main()
