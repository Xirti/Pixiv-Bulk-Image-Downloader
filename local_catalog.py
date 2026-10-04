"""Index actual downloaded artifacts, independently of removable history."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path


class LocalCatalog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            with connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1}:
                    raise ValueError("unsupported catalog schema")
                connection.execute("""CREATE TABLE IF NOT EXISTS artifacts (
                    artwork TEXT NOT NULL, page INTEGER NOT NULL, quality TEXT NOT NULL,
                    format TEXT NOT NULL, directory TEXT NOT NULL, files TEXT NOT NULL,
                    PRIMARY KEY(artwork, page, quality, format, directory)
                )""")
                connection.execute("PRAGMA user_version=1")
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _directory(directory: Path) -> str:
        return os.path.normcase(os.path.abspath(directory))

    @staticmethod
    def _file_state(path: Path) -> dict:
        path = Path(os.path.abspath(path))
        # Never turn an unreadable directory into an apparent missing file.
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("下载目录包含链接，请重新选择保存位置")
        if not stat.S_ISREG(info.st_mode):
            raise OSError("下载文件已变为非普通文件")
        return {"path": str(path), "size": info.st_size, "mtime": info.st_mtime_ns}

    def _present(self, encoded: str) -> bool:
        files = json.loads(encoded)
        try:
            return bool(files) and all(self._file_state(Path(file["path"])) == file for file in files)
        except (FileNotFoundError, NotADirectoryError):
            return False

    def record(self, artwork: str, pages: list[int], quality: str, format: str,
               directory: Path, files: list[Path], *, animation: bool = False) -> None:
        if (not str(artwork).isdigit() or quality not in {"original", "regular"}
                or format not in {"source", "gif", "mp4"} or not pages
                or any(type(page) is not int or page < 0 for page in pages)
                or (not animation and len(files) != len(pages)) or (animation and pages != [0])):
            raise ValueError("invalid catalog artifacts")
        directory_key = self._directory(directory)
        states = [self._file_state(file) for file in files]
        if not states:
            raise ValueError("empty catalog artifacts")
        rows = [(str(artwork), page, quality, format, directory_key,
                 json.dumps(states if animation else [states[index]], ensure_ascii=False))
                for index, page in enumerate(pages)]
        with self._lock, self._connection() as connection:
            connection.executemany("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?,?)", rows)

    def existing(self, artwork: str, pages: list[int], quality: str, format: str,
                 directory: Path) -> set[int]:
        if not self.path.exists():
            return set()
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT page,files FROM artifacts WHERE artwork=? AND quality=? AND format=? AND directory=?",
                                      (str(artwork), quality, format, self._directory(directory))).fetchall()
        wanted, present = set(pages), set()
        for page, encoded in rows:
            if page not in wanted:
                continue
            if self._present(encoded):
                present.add(page)
        return present

    def downloaded_pages(self, artwork: str) -> set[int]:
        """A lightweight artwork indicator across directories and qualities."""
        if not self.path.exists():
            return set()
        with self._lock, self._connection() as connection:
            rows = connection.execute("SELECT page,files FROM artifacts WHERE artwork=?", (str(artwork),)).fetchall()
        present = set()
        for page, encoded in rows:
            if page not in present and self._present(encoded):
                present.add(page)
        return present
