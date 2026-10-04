"""Local successful downloads. No network, credentials, or image copies."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def history_path() -> Path:
    override = os.getenv("MOKU_LIBRARY_DIR")
    directory = Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / "MOKU" / "library"
    return directory / "downloads.sqlite3"


class DownloadHistory:
    """Lazy SQLite store; one atomic record() call per committed request.

    Replaying the same operation/artwork is idempotent. New downloads use new
    operation IDs. Storage errors propagate so callers can warn without retrying
    downloads. The limit counts artwork records, not files.
    """

    def __init__(self, path: Path, *, max_records: int = 5000):
        if max_records < 1:
            raise ValueError("history limit must be positive")
        self.path = Path(path)
        self.max_records = max_records
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute("PRAGMA secure_delete=ON")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in {0, 1, 2}:
                    raise ValueError("unsupported history schema")
                # Non-reused IDs keep stale UI selections from deleting newer
                # records after the last row is removed. Migrate atomically.
                if version == 1:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("ALTER TABLE downloads RENAME TO downloads_v1")
                connection.execute("""CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL,
                    completed_at TEXT NOT NULL, artwork_id TEXT NOT NULL,
                    title TEXT NOT NULL, artist TEXT NOT NULL, work_type TEXT NOT NULL,
                    quality TEXT NOT NULL, format TEXT NOT NULL,
                    pages TEXT NOT NULL, files TEXT NOT NULL,
                    UNIQUE(operation, artwork_id)
                )""")
                if version == 1:
                    connection.execute("INSERT INTO downloads SELECT * FROM downloads_v1")
                    connection.execute("DROP TABLE downloads_v1")
                connection.execute("PRAGMA user_version=2")
                yield connection
        finally:
            connection.close()

    def record(self, operation: str, rows: list[dict]) -> None:
        if not rows:
            return
        if not isinstance(operation, str) or not 1 <= len(operation) <= 128:
            raise ValueError("invalid history operation")
        completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        values = []
        for row in rows:
            artwork_id = str(row["artworkId"])
            pages, files = row["pages"], row["files"]
            if not artwork_id.isdigit() or len(artwork_id) > 30:
                raise ValueError("invalid history artwork")
            if not pages or len(pages) > 200 or any(type(page) is not int or page < 0 for page in pages):
                raise ValueError("invalid history pages")
            if not files or len(files) > 202 or any(not isinstance(file, str) or not file or len(file) > 32768 for file in files):
                raise ValueError("invalid history files")
            if row["quality"] not in {"regular", "original"} or row["format"] not in {"source", "gif", "mp4"}:
                raise ValueError("invalid history format")
            values.append((
                operation, completed_at, artwork_id, str(row.get("title") or "")[:1000],
                str(row.get("artist") or "")[:500], str(row.get("workType") or "illustration")[:30],
                row["quality"], row["format"], json.dumps(sorted(set(pages))),
                json.dumps(files, ensure_ascii=False),
            ))
        with self._lock, self._connection() as connection:
            connection.executemany("""INSERT OR IGNORE INTO downloads
                (operation, completed_at, artwork_id, title, artist, work_type, quality, format, pages, files)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
            connection.execute("""DELETE FROM downloads WHERE id IN
                (SELECT id FROM downloads ORDER BY id DESC LIMIT -1 OFFSET ?)""", (self.max_records,))

    def list(self, *, page: int = 1, per_page: int = 20, query: str = "") -> dict:
        if type(page) is not int or not 1 <= page <= 100000 or type(per_page) is not int or not 1 <= per_page <= 50:
            raise ValueError("invalid history pagination")
        if not isinstance(query, str) or len(query) > 120:
            raise ValueError("invalid history query")
        result = {"items": [], "total": 0, "page": 1, "pages": 0, "limit": self.max_records}
        with self._lock:
            if not self.path.exists():
                return result
            query = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = "WHERE title LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\' OR artwork_id LIKE ? ESCAPE '\\'" if query else ""
            params = (f"%{query}%",) * 3 if query else ()
            with self._connection() as connection:
                total = connection.execute(f"SELECT COUNT(*) FROM downloads {where}", params).fetchone()[0]
                pages = (total + per_page - 1) // per_page
                page = min(page, max(1, pages))
                rows = connection.execute(
                    f"SELECT * FROM downloads {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                    (*params, per_page, (page - 1) * per_page),
                ).fetchall()
                result.update(total=total, page=page, pages=pages)
                result["items"] = [{
                    "id": row["id"], "completedAt": row["completed_at"], "artworkId": row["artwork_id"],
                    "title": row["title"], "artist": row["artist"], "workType": row["work_type"],
                    "quality": row["quality"], "format": row["format"],
                    "pages": json.loads(row["pages"]), "files": json.loads(row["files"]),
                } for row in rows]
        return result

    def delete(self, ids: list[int]) -> int:
        if (not isinstance(ids, list) or not 1 <= len(ids) <= self.max_records
                or any(type(row_id) is not int or not 1 <= row_id <= 2**63 - 1 for row_id in ids)):
            raise ValueError("invalid history IDs")
        with self._lock:
            if not self.path.exists():
                return 0
            with self._connection() as connection:
                cursor = connection.executemany("DELETE FROM downloads WHERE id=?", ((row_id,) for row_id in set(ids)))
                return cursor.rowcount

    def first_file(self, row_id: int) -> str | None:
        if type(row_id) is not int or not 1 <= row_id <= 2**63 - 1:
            raise ValueError("invalid history ID")
        with self._lock:
            if not self.path.exists():
                return None
            with self._connection() as connection:
                row = connection.execute("SELECT files FROM downloads WHERE id=?", (row_id,)).fetchone()
                return json.loads(row[0])[0] if row else None

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                with self._connection() as connection:
                    connection.execute("DELETE FROM downloads")
