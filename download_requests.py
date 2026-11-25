from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import sqlite3
from pathlib import Path
from collections import OrderedDict
from collections.abc import Callable
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass

from local_catalog import StaleArtifactError, artifact_file_state
from download_execution import (
    cancel_download_execution, download_execution, download_execution_active, download_migration_guard,
)
from download_history import DownloadHistory, ReceiptRow, create_receipt_table, receipt_journal_exists
from workspace_store import WorkspaceStore


Response = tuple[dict, int]
RECEIPT_LIMIT = 2000
_PUBLICATION_ID: ContextVar[tuple[str, str] | None] = ContextVar("download_publication_identity", default=None)


def _receipt_row(request_id: str, fingerprint: str, payload: dict, root: Path) -> ReceiptRow:
    files = []
    for relative in dict.fromkeys(payload.get("saved", []) + payload.get("skippedFiles", [])):
        info = artifact_file_state(root / relative)
        files.append([info["path"], info["size"], info["mtime"]])
    return request_id, fingerprint, json.dumps(payload, ensure_ascii=False), json.dumps(files, ensure_ascii=False)


def publication_receipt(payload: dict, root: Path) -> ReceiptRow | None:
    identity = _PUBLICATION_ID.get()
    if identity is not None:
        return _receipt_row(*identity, payload, root)
    return None


def _fingerprint(route: str, scope: str, body: dict) -> str:
    return hashlib.sha256(json.dumps(
        [route, scope, body], sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


class DownloadRequestError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _request_identity(request_id: object, route: str, body: dict, scope: str, receipt_scope: str) -> tuple[str, str, str]:
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", request_id):
        raise DownloadRequestError("下载任务编号无效")
    normalized = {key: value for key, value in body.items() if key != "requestId"}
    fingerprint = _fingerprint(route, scope, normalized)
    durable_fingerprint = fingerprint if receipt_scope == scope else _fingerprint(route, receipt_scope, normalized)
    return request_id, fingerprint, durable_fingerprint


@dataclass
class _Request:
    fingerprint: str
    active: bool
    touched: float
    result: Response | None = None
    durable_fingerprint: str = ""
    cancel: threading.Event | None = None
    scope: str = ""


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
        workspace: WorkspaceStore | None = None,
        history: DownloadHistory | None = None,
    ) -> None:
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("invalid download recovery limits")
        self._limit = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[str, _Request] = OrderedDict()
        self._lock = threading.Lock()
        self._path = Path(path) if path is not None else None
        self._workspace = workspace
        self._history = history

    def _journal_connection(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=2)
        try:
            create_receipt_table(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    def _legacy_fingerprints(self, route: str, body: dict, scope: str) -> tuple[str, ...]:
        if self._workspace is None:
            return ()
        aliases = self._workspace.prior_web_scopes(scope)
        normalized = {key: value for key, value in body.items() if key != "requestId"}
        return tuple(_fingerprint(route, alias, {**normalized, "scope": alias}
                                  if normalized.get("scope") == scope else normalized) for alias in aliases)

    def _read_success(self, request_id: str, fingerprints: tuple[str, ...]) -> Response | None:
        lookup = "SELECT fingerprint, result, files FROM completed WHERE id=?"
        recovery_error = None
        if self._history is not None:
            try:
                with self._history.recovery_journal() as connection:
                    row = connection.execute(lookup, (request_id,)).fetchone() if connection is not None else None
            except (OSError, sqlite3.Error, ValueError) as exc:
                recovery_error = exc
            else:
                if row is not None:
                    # A pending publication record belongs to the last attempt.
                    try:
                        return self._verify_receipt(row, fingerprints)
                    except DownloadRequestError as error:
                        if error.status != 503:
                            raise
                        # Another durable copy may still prove the same intent.
                        # Corruption alone never permits executing it again.
                        recovery_error = error
        if self._path is not None and receipt_journal_exists(self._path):
            connection = self._journal_connection()
            try:
                row = connection.execute(lookup, (request_id,)).fetchone()
            finally:
                connection.close()
            result = self._verify_receipt(row, fingerprints)
            if result is not None:
                return result
        if recovery_error is not None:
            if isinstance(recovery_error, ValueError):
                raise DownloadRequestError("暂时无法核对下载回执，请继续原任务重试", 503) from recovery_error
            raise recovery_error
        return None

    @staticmethod
    def _verify_receipt(row, fingerprints: tuple[str, ...]) -> Response | None:
        if not row:
            return None
        if row[0] not in fingerprints:
            raise DownloadRequestError("下载任务内容或账户已变更，请重新发起下载", 409)
        # Completion evidence must not authorize another execution when corrupt.
        try:
            payload, files = json.loads(row[1]), json.loads(row[2])
        except (TypeError, ValueError) as error:
            logging.getLogger("moku.http").warning("Download receipt could not be decoded: %s", error)
            raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
        if not isinstance(payload, dict) or not isinstance(files, list):
            raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
        paths = []
        for key in ("saved", "skippedFiles"):
            values = payload.get(key, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value or "\0" in value for value in values):
                raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
            paths.extend(values)
        for key in ("pages", "skippedPages", "artworks"):
            if key in payload and (type(payload[key]) is not int or payload[key] < 0):
                raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
        paths = list(dict.fromkeys(paths))
        if len(files) != len(paths):
            raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
        receipt_root = None
        seen_paths = set()
        for relative, file in zip(paths, files):
            if (not isinstance(file, list) or len(file) != 3
                    or not isinstance(file[0], str) or not file[0] or "\0" in file[0]
                    or type(file[1]) is not int or file[1] < 0 or type(file[2]) is not int):
                raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
            relative, path = Path(relative), Path(file[0])
            if (relative.anchor or not relative.parts or ".." in relative.parts
                    or not path.is_absolute() or ".." in path.parts
                    or len(path.parts) <= len(relative.parts) or path in seen_paths):
                raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
            root = path.parents[len(relative.parts) - 1]
            if root / relative != path or receipt_root is not None and root != receipt_root:
                raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
            receipt_root = root
            seen_paths.add(path)
        if payload.get("skippedPages") and not payload.get("skippedFiles"):
            # Older receipts cannot prove skipped files; require explicit recovery.
            raise DownloadRequestError("下载完成回执损坏，未重复执行；请核对成品后重新发起任务", 503)
        for path, size, mtime in files:
            try:
                info = artifact_file_state(Path(path))
            except (FileNotFoundError, NotADirectoryError, StaleArtifactError):
                return None
            if info["size"] != size or info["mtime"] != mtime:
                return None
        return payload, 200

    def _confirm_receipt(self, request_id: str, fingerprints: tuple[str, ...]) -> Response | None:
        try:
            return self._read_success(request_id, fingerprints)
        except (OSError, sqlite3.Error) as exc:
            raise DownloadRequestError("暂时无法核对下载回执，请继续原任务重试", 503) from exc

    def _write_success(self, request_id: str, fingerprint: str, result: Response, root: Path) -> None:
        if self._path is None:
            return
        receipt = _receipt_row(request_id, fingerprint, result[0], root)
        connection = self._journal_connection()
        try:
            with connection:
                connection.execute("INSERT OR REPLACE INTO completed VALUES (?,?,?,?)", receipt)
            self._prune_receipts(connection)
        finally:
            connection.close()

    def _prune_receipts(self, connection: sqlite3.Connection) -> None:
        try:
            if connection.execute("SELECT 1 FROM completed LIMIT 1 OFFSET ?", (RECEIPT_LIMIT,)).fetchone() is None:
                return
            pending = self._workspace.pending_download_ids() if self._workspace is not None else nullcontext([])
            with pending as request_ids, connection:
                connection.execute("""
                    DELETE FROM completed WHERE rowid IN (
                        SELECT rowid FROM completed
                        WHERE id NOT IN (SELECT value FROM json_each(?))
                        ORDER BY rowid DESC LIMIT -1 OFFSET ?
                    )
                """, (json.dumps(request_ids), RECEIPT_LIMIT))
        except (OSError, sqlite3.Error, ValueError) as exc:
            # The new receipt is already committed. Retry maintenance on a
            # later success, after the queue can be read safely.
            logging.getLogger("moku.http").warning("Download receipt pruning deferred: %s", exc)

    def _finish_recovery(self, request_id: str, *, receipt_saved: bool) -> None:
        if self._history is None:
            return
        try:
            with self._history.recovery_journal() as connection:
                if connection is not None:
                    if receipt_saved:
                        with connection:
                            connection.execute("DELETE FROM completed WHERE id=?", (request_id,))
                    self._prune_receipts(connection)
        except (OSError, sqlite3.Error, ValueError) as exc:
            logging.getLogger("moku.http").warning("Download recovery cleanup deferred: %s", exc)

    def probe(
        self, request_id: object, route: str, body: dict, *, scope: str,
        durable_scope: str, output_root: Path,
    ) -> Response:
        """Inspect a queued legacy request without starting or publishing work.

        Inactive attempts can be inspected after reauthorization to the same
        account. The output_root keyword is retained for existing callers;
        a probe verifies the paths already stored in the receipt.
        """
        if request_id is None and "requestId" not in body:
            return {}, 204
        request_id, fingerprint, durable_fingerprint = _request_identity(request_id, route, body, scope, durable_scope)
        aliases = self._legacy_fingerprints(route, body, durable_scope)
        fingerprints = (durable_fingerprint, *aliases)
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is not None and (entry.active or self._clock() - entry.touched < self._ttl):
                expected = entry.fingerprint if entry.active else entry.durable_fingerprint
                observed = fingerprint if entry.active else durable_fingerprint
                if expected != observed and entry.durable_fingerprint not in aliases:
                    raise DownloadRequestError("下载任务内容或账户已变更，请重新发起下载", 409)
                if entry.active:
                    return {"pending": True, "retryAfterMs": 1000}, 202
                if self._path is None and entry.result is not None:
                    return entry.result
        if self._path is not None and download_execution_active(self._path, request_id, durable_scope):
            return {"pending": True, "retryAfterMs": 1000}, 202
        try:
            receipt = self._read_success(request_id, fingerprints)
        except (OSError, sqlite3.Error):
            receipt = self._confirm_receipt(request_id, fingerprints)
        return receipt if receipt is not None else ({}, 204)

    def migration_guard(self, request_id: str):
        return nullcontext(True) if self._path is None else download_migration_guard(self._path, request_id)

    def cancel(self, request_id: str, scope: str) -> bool:
        aliases = self._workspace.prior_web_scopes(scope) if self._workspace is not None else ()
        scopes = (scope, *aliases)
        if self._path is not None:
            return any(cancel_download_execution(self._path, request_id, owner) for owner in scopes)
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None or not entry.active or entry.scope not in scopes or entry.cancel is None:
                return False
            entry.cancel.set()
            return True

    def run(
        self, request_id: object, route: str, body: dict,
        operation: Callable[[], Response], *, scope: str = "", durable_scope: str | None = None,
        output_root: Path | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        # Old clients without an ID retain the existing synchronous behaviour.
        if request_id is None and "requestId" not in body:
            return operation()
        receipt_scope = scope if durable_scope is None else durable_scope
        request_id, fingerprint, durable_fingerprint = _request_identity(request_id, route, body, scope, receipt_scope)
        aliases = self._legacy_fingerprints(route, body, receipt_scope)
        fingerprints = (durable_fingerprint, *aliases)
        now = self._clock()
        # Active polls need only registry state. Disk verification belongs
        # outside this lock; a slow output directory must not stall other IDs.
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is not None and entry.active:
                if entry.fingerprint != fingerprint and entry.durable_fingerprint not in aliases:
                    raise DownloadRequestError("下载任务内容或账户已变更，请重新发起下载", 409)
                return {"pending": True, "retryAfterMs": 1000}, 202
        try:
            restored = self._read_success(request_id, fingerprints)
        except (OSError, sqlite3.Error):
            restored = None
        with self._lock:
            for key, entry in list(self._entries.items()):
                if not entry.active and now - entry.touched >= self._ttl:
                    del self._entries[key]
            entry = self._entries.get(request_id)
            if entry is not None:
                if entry.fingerprint != fingerprint:
                    # Reauthorization may change only the volatile epoch. Keep
                    # both account and request content bound to their identity.
                    if entry.active and entry.durable_fingerprint in aliases:
                        return {"pending": True, "retryAfterMs": 1000}, 202
                    if not entry.active and durable_scope is not None and entry.durable_fingerprint in fingerprints:
                        if entry.result is not None and self._path is None:
                            return entry.result
                        entry.fingerprint = fingerprint
                        entry.durable_fingerprint = durable_fingerprint
                        entry.result = None
                    else:
                        raise DownloadRequestError("下载任务内容或账户已变更，请重新发起下载", 409)
                self._entries.move_to_end(request_id)
                if entry.active:
                    return {"pending": True, "retryAfterMs": 1000}, 202
                if entry.result is not None and self._path is None:
                    return entry.result
            if restored is not None:
                return restored
            if entry is None:
                while len(self._entries) >= self._limit:
                    idle = next((key for key, row in self._entries.items() if not row.active), None)
                    if idle is None:
                        raise DownloadRequestError("下载任务繁忙，请稍后重试", 429)
                    del self._entries[idle]
                entry = _Request(fingerprint, True, now, durable_fingerprint=durable_fingerprint)
                self._entries[request_id] = entry
            else:
                entry.result = None
                entry.active = True
            entry.cancel = cancel_event
            entry.scope = receipt_scope

        result = None
        try:
            if self._path is None:
                result = operation()
            else:
                with download_execution(self._path, request_id, entry.scope, cancel_event) as owner:
                    if not owner:
                        return {"pending": True, "retryAfterMs": 1000}, 202
                    # A peer may have committed its receipt after our first
                    # lookup but before mutex acquisition. Recheck as owner.
                    result = self._confirm_receipt(request_id, fingerprints)
                    if result is None:
                        token = _PUBLICATION_ID.set((request_id, durable_fingerprint) if self._history is not None else None)
                        try:
                            result = operation()
                        finally:
                            _PUBLICATION_ID.reset(token)
                        if result[1] == 200:
                            try:
                                raw_root = str(body.get("saveRoot") or "").strip()
                                self._write_success(request_id, durable_fingerprint, result,
                                                    Path(raw_root).expanduser() if raw_root else Path(output_root or "."))
                            except (OSError, sqlite3.Error):
                                result[0]["recoveryWarning"] = "文件已保存，但重启后可能需要重新检查这批下载"
                                self._finish_recovery(request_id, receipt_saved=False)
                            else:
                                self._finish_recovery(request_id, receipt_saved=True)
            return result
        finally:
            # Record publication before the caller tries writing the response:
            # a disconnected client must not lose an already-completed result.
            with self._lock:
                if self._path is None and result is not None and result[1] == 200:
                    entry.result = result
                entry.active = False
                entry.cancel = None
                entry.touched = self._clock()
