from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
SUBJECT_TESTS = ROOT / "subject_mvp0" / "tests"


def subject_mvp0_test_command() -> list[str]:
    return [sys.executable, "-B", "-m", "pytest", str(SUBJECT_TESTS), "-q"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-only", action="store_true", help="Run only the MOKU application tests")
    parser.add_argument("--subject-mvp0", action="store_true", help="Also run the optional subject_mvp0 tests")
    args = parser.parse_args()
    if args.app_only and args.subject_mvp0:
        parser.error("--app-only and --subject-mvp0 cannot be combined")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Download tests must never append to the current user's actual library.
    previous_library = os.environ.get("MOKU_LIBRARY_DIR")
    with tempfile.TemporaryDirectory(prefix="moku-test-library-") as directory:
        os.environ["MOKU_LIBRARY_DIR"] = directory
        try:
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
    if not result.wasSuccessful():
        return 1
    if args.app_only or not args.subject_mvp0:
        return 0
    if not SUBJECT_TESTS.is_dir():
        print(f"Optional subject test directory is missing: {SUBJECT_TESTS}", file=sys.stderr)
        return 1
    subject = subprocess.run(subject_mvp0_test_command(), cwd=ROOT, check=False)
    return subject.returncode


if __name__ == "__main__":
    raise SystemExit(main())
