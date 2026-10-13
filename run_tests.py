from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"


def main(targets: list[str] | None = None) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    # Download tests must never append to the current user's actual library.
    previous_library = os.environ.get("MOKU_LIBRARY_DIR")
    with tempfile.TemporaryDirectory(prefix="moku-test-library-") as directory:
        os.environ["MOKU_LIBRARY_DIR"] = directory
        try:
            if targets:
                suite = unittest.defaultTestLoader.loadTestsFromNames(
                    [name.removeprefix("tests.") for name in targets],
                )
            else:
                suite = unittest.defaultTestLoader.discover(
                    start_dir=str(TESTS),
                    pattern="test*.py",
                    top_level_dir=str(TESTS),
                )
            result = unittest.TextTestRunner(verbosity=2).run(suite)
        finally:
            if previous_library is None:
                os.environ.pop("MOKU_LIBRARY_DIR", None)
            else:
                os.environ["MOKU_LIBRARY_DIR"] = previous_library
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
