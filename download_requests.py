from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import sqlite3
from pathlib import Path
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


Response = tuple[dict, int]


class DownloadRequestError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class _Request:
    fingerprint: str
    active: bool
    touched: float
    result: Response | None = None


class DownloadRequests:
    """Bounded recovery for a download whose HTTP response may be lost.

    An active duplicate observes pending; a completed success is replayed.
    Failed attempts may retry, but an ID can never change its request or account.
    No network work or file publication happens under the registry lock.
    """

    def __init__(
        self, *, max_entries: int = 64, ttl_seconds: float = 1800,
        clock: Callable[[], float] = time.monotonic,
        path: Path | None = None,
    ) -> None:
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("invalid download recovery limits")
        self._limit = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[str, _Request] = OrderedDict()
        self._lock = threading.Lock()
        self._path = Path(path) if path is not None else None

    def _journal_connection(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=2)
        connection.execute("CREATE TABLE IF NOT EXISTS completed (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL, files TEXT NOT NULL)")
        return connection

    def _read_success(self, request_id: str, fingerprint: str) -> Response | None:
        if self._path is None or not self._path.exists():
            return None
        connection = self._journal_connection()
        try:
            row = connection.execute("SELECT fingerprint,result,files FROM completed WHERE id=?", (request_id,)).fetchone()
        finally:
            connection.close()
        if not row:
            return None
        if row[0] != fingerprint:
            raise DownloadRequestError("下载任务内容或账户已变更，请重新发起下载", 409)
        payload = json.loads(row[1])
        if payload.get("skippedPages") and not payload.get("skippedFiles"):
            # Older receipts omitted skipped files; check the directory again.
            return None
        for path, size, mtime in json.loads(row[2]):
            try:
                info = Path(path).stat()
            except FileNotFoundError:
                return None
            if info.st_size != size or info.st_mtime_ns != mtime:
                return None
        return payload, 200

    def _write_success(self, request_id: str, fingerprint: str, result: Response, root: Path) -> None:
        if self._path is None:
            return
        files = []
        for relative in dict.fromkeys(result[0].get("saved", []) + result[0].get("skippedFiles", [])):
            file = root / relative
            info = file.stat()
            files.append([str(file), info.st_size, info.st_mtime_ns])
        connection = self._journal_connection()
        try:
            with connection:
                connection.execute("INSERT OR REPLACE INTO completed VALUES (?,?,?,?)",
                                   (request_id, fingerprint, json.dumps(result[0], ensure_ascii=False), json.dumps(files, ensure_ascii=False)))
                connection.execute("DELETE FROM completed WHERE rowid NOT IN (SELECT rowid FROM completed ORDER BY rowid DESC LIMIT 2000)")
        finally:
            connection.close()

    def run(
        self, request_id: object, route: str, body: dict,
        operation: Callable[[], Response], *, scope: str = "", durable_scope: str | None = None,
        output_root: Path | None = None,
    ) -> Response:
        # Old clients without an ID retain the existing synchronous behaviour.
        if request_id is None and "requestId" not in body:
            return operation()
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", request_id):
            raise DownloadRequestError("下载任务编号无效")
        normalized = {key: value for key, value in body.items() if key != "requestId"}
        fingerprint = hashlib.sha256(json.dumps(
            [route, scope, normalized], sort_keys=True, ensure_ascii=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        durable_fingerprint = hashlib.sha256(json.dumps(
            [route, durable_scope if durable_scope is not None else scope, normalized],
            sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        now = self._clock()
        with self._lock:
            for key, entry in list(self._entries.items()):
                if not entry.active and now - entry.touched >= self._ttl:
                    del self._entries[key]
            entry = self._entries.get(request_id)
            if entry is not None:
                if entry.fingerprint != fingerprint:
                    # A completed receipt can outlive re-login to the same
                    # account. Active/failed attempts still belong to their epoch.
                    if not entry.active and entry.result is not None and durable_scope is not None:
                        try:
                            restored = self._read_success(request_id, durable_fingerprint)
                        except (OSError, sqlite3.Error):
                            restored = None
                        if restored is not None:
                            return restored
                    raise DownloadRequestError("下载任务内容或账户已变更，请重新发起下载", 409)
                self._entries.move_to_end(request_id)
                if entry.active:
                    return {"pending": True, "retryAfterMs": 1000}, 202
                if entry.result is not None:
                    if self._path is None or entry.result[0].get("recoveryWarning"):
                        return entry.result
                    try:
                        restored = self._read_success(request_id, durable_fingerprint)
                    except (OSError, sqlite3.Error):
                        return entry.result
                    if restored is not None:
                        return restored
                    entry.result = None
                entry.active = True
            else:
                try:
                    restored = self._read_success(request_id, durable_fingerprint)
                except (OSError, sqlite3.Error):
                    restored = None
                if restored is not None:
                    return restored
                while len(self._entries) >= self._limit:
                    idle = next((key for key, row in self._entries.items() if not row.active), None)
                    if idle is None:
                        raise DownloadRequestError("下载任务繁忙，请稍后重试", 429)
                    del self._entries[idle]
                entry = _Request(fingerprint, True, now)
                self._entries[request_id] = entry

        result = None
        try:
            result = operation()
            if result[1] == 200:
                try:
                    raw_root = str(body.get("saveRoot") or "").strip()
                    self._write_success(request_id, durable_fingerprint, result,
                                        Path(raw_root).expanduser() if raw_root else Path(output_root or "."))
                except (OSError, sqlite3.Error):
                    result[0]["recoveryWarning"] = "文件已保存，但重启后可能需要重新检查这批下载"
            return result
        finally:
            # Record publication before the caller tries writing the response:
            # a disconnected client must not lose an already-completed result.
            with self._lock:
                entry.result = result if result is not None and result[1] == 200 else None
                entry.active = False
                entry.touched = self._clock()
