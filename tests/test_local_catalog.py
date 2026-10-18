import json
import tempfile
from collections import Counter
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch
from pathlib import Path


class LocalCatalogTests(unittest.TestCase):
    def test_misdirected_catalog_row_does_not_skip_a_file_outside_the_target_directory(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            original, unrelated = output / "123.png", root / "456.png"
            original.write_bytes(b"image")
            unrelated.write_bytes(b"unrelated")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0], "original", "source", output, [original])
            catalog.record("456", [0], "original", "source", root, [unrelated])
            with closing(sqlite3.connect(catalog.path)) as connection, connection:
                connection.execute("UPDATE artifacts SET files=(SELECT files FROM artifacts WHERE artwork='456') WHERE artwork='123'")
            self.assertEqual(catalog.existing_files("123", [0], "original", "source", output), {})

    def test_malformed_index_rows_do_not_hide_valid_files_or_block_deduplication(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / f"123_p{page}.png" for page in range(2)]
            for file in files:
                file.write_bytes(b"image")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0, 1], "original", "source", root, files)
            for encoded in ("{broken", "null", "{}", '[null]', '[{}]',
                            json.dumps([{"path": str(files[1]), "size": "5", "mtime": 0}]),
                            json.dumps([{"path": "bad\0path", "size": 5, "mtime": 0}])):
                with self.subTest(encoded=encoded):
                    with closing(sqlite3.connect(catalog.path)) as connection, connection:
                        connection.execute("UPDATE artifacts SET files=? WHERE page=1", (encoded,))
                    self.assertEqual(catalog.downloaded_pages("123"), {0})
                    self.assertEqual(catalog.existing("123", [0, 1], "original", "source", root), {0})

    def test_file_replaced_by_directory_is_stale_without_hiding_other_artifacts(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / f"123_p{page}.png" for page in range(2)]
            for file in files:
                file.write_bytes(b"image")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0, 1], "original", "source", root, files)
            files[1].unlink()
            files[1].mkdir()
            self.assertEqual(catalog.downloaded_pages_many(["123", "456"]), {"123": {0}, "456": set()})
            self.assertEqual(catalog.existing("123", [0, 1], "original", "source", root), {0})

    def test_indicator_isolates_unreadable_file_but_download_checks_remain_strict(self):
        from local_catalog import LocalCatalog
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / f"123_p{page}.png" for page in range(2)]
            for file in files:
                file.write_bytes(b"image")
            catalog = LocalCatalog(root / "catalog.db")
            catalog.record("123", [0, 1], "original", "source", root, files)
            original = Path.lstat

            def unreadable(path, *args, **kwargs):
                if path == files[1]:
                    raise PermissionError("denied")
                return original(path, *args, **kwargs)

            with patch.object(Path, "lstat", unreadable):
                self.assertEqual(catalog.downloaded_pages("123"), {0})
                with self.assertRaises(PermissionError):
                    catalog.existing("123", [0, 1], "original", "source", root)

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
