import tempfile
from collections import Counter
import sqlite3
import unittest
from unittest.mock import patch
from pathlib import Path


class LocalCatalogTests(unittest.TestCase):
    def test_indicators_check_shared_directories_once_but_files_on_every_read(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "pictures" / "123"
            folder.mkdir(parents=True)
            files = [folder / f"123_p{page}.png" for page in range(200)]
            for file in files:
                file.write_bytes(b"image")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", list(range(200)), "original", "source", root, files)
            calls = Counter()
            original = Path.lstat

            def counted(path, *args, **kwargs):
                calls[path] += 1
                return original(path, *args, **kwargs)

            with patch.object(Path, "lstat", counted):
                self.assertEqual(catalog.downloaded_pages("123"), set(range(200)))
            self.assertEqual(calls[folder], 1)
            self.assertTrue(all(calls[file] == 1 for file in files))
            files[10].unlink()
            files[20].write_bytes(b"changed size")
            self.assertEqual(catalog.downloaded_pages("123"), set(range(200)) - {10, 20})
            # Actual download deduplication retains its full path checks.
            calls.clear()
            with patch.object(Path, "lstat", counted):
                self.assertEqual(catalog.existing("123", [0, 1], "original", "source", root), {0, 1})
            self.assertEqual(calls[folder], 2)

    def test_existing_catalog_can_be_read_without_database_writes(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "image.png"
            file.write_bytes(b"image")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0], "original", "source", root, [file])
            connect = sqlite3.connect
            def read_only(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connection.execute("PRAGMA query_only=ON")
                return connection
            with patch("local_catalog.sqlite3.connect", side_effect=read_only):
                self.assertEqual(catalog.downloaded_pages_many(["123", "456"]), {"123": {0}, "456": set()})
                self.assertEqual(catalog.existing("123", [0], "original", "source", root), {0})

    def test_unreadable_files_are_not_silently_treated_as_missing(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "image.png"
            file.write_bytes(b"image")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0], "original", "source", root, [file])
            with patch.object(Path, "lstat", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    catalog.existing("123", [0], "original", "source", root)

    def test_actual_file_identity_quality_format_directory_and_missing_files(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = LocalCatalog(root / "catalog.db")
            file = root / "123_p0.png"
            file.write_bytes(b"image")
            catalog.record("123", [0], "original", "source", root, [file])
            self.assertEqual(catalog.existing("123", [0, 1], "original", "source", root), {0})
            self.assertEqual(catalog.existing("123", [0], "regular", "source", root), set())
            self.assertEqual(catalog.existing("123", [0], "original", "gif", root), set())
            self.assertEqual(catalog.existing("123", [0], "original", "source", root / "other"), set())
            # Reopening the store is a restart, not a memory cache hit.
            catalog = LocalCatalog(root / "catalog.db")
            self.assertEqual(catalog.existing("123", [0], "original", "source", root), {0})
            file.unlink()
            self.assertEqual(catalog.existing("123", [0], "original", "source", root), set())

    def test_ugoira_source_requires_both_zip_and_manifest(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / "animation.zip", root / "frames.json"]
            for file in files:
                file.write_bytes(b"test")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0], "original", "source", root, files, animation=True)
            self.assertEqual(catalog.existing("123", [0], "original", "source", root), {0})
            files[1].unlink()
            self.assertEqual(catalog.existing("123", [0], "original", "source", root), set())

    def test_changed_file_is_not_counted_as_downloaded(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file = root / "image.png"
            file.write_bytes(b"original")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0], "original", "source", root, [file])
            file.write_bytes(b"changed!")
            self.assertEqual(catalog.existing("123", [0], "original", "source", root), set())
