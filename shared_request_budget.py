"""Per-user Pixiv admission, pacing and cooldown shared by MOKU editions."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from email.utils import parsedate_to_datetime
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from pixiv_adapter import PixivPolicyError


class RequestDeferredError(PixivPolicyError):
    def __init__(self, kind: str, seconds: float):
        self.kind = kind
        self.retry_after = max(1, math.ceil(seconds))
        message = (f"Pixiv 请求已冷却，请在 {self.retry_after} 秒后继续"
                   if kind == "upstream_rate_limit" else "网络请求正在排队，请稍后继续")
        super().__init__(message)


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float | None:
    if not value:
        return None
    current = time.time() if now is None else now
    try:
        seconds = float(value) if value.isascii() and value.isdecimal() else parsedate_to_datetime(value).timestamp() - current
    except (ValueError, TypeError, OverflowError):
        return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


_KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
_KERNEL.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_KERNEL.OpenProcess.restype = wintypes.HANDLE
_KERNEL.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_KERNEL.GetProcessTimes.restype = wintypes.BOOL
_KERNEL.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_KERNEL.WaitForSingleObject.restype = wintypes.DWORD
_KERNEL.CloseHandle.argtypes = [wintypes.HANDLE]
_KERNEL.CloseHandle.restype = wintypes.BOOL


def process_identity(pid: int) -> int | None:
    """Creation time identifies PID reuse. Access denial retains the lease."""
    handle = _KERNEL.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return None
        if error == 5:
            return -1
        raise ctypes.WinError(error)
    try:
        if _KERNEL.WaitForSingleObject(handle, 0) == 0:
            return None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _KERNEL.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        _KERNEL.CloseHandle(handle)


class SharedRequestBudget:
    def __init__(self, path: Path | None = None):
        directory = os.environ.get("MOKU_SHARED_NETWORK_DIR")
        self.path = path or ((Path(directory) if directory else Path(os.environ["LOCALAPPDATA"]) / "MOKU-Shared") / "network.sqlite3")
        self.owner = uuid.uuid4().hex
        self.pid = os.getpid()
        self.created = process_identity(self.pid)
        self._mode = "safe"
        self._ready = False
        self._connection = None
        self._lock = threading.RLock()
        self._initialize_lock = threading.Lock()
        self._gc_at = 0.0
        self._released = set()

    def _initialize(self):
        with self._initialize_lock:
            if self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = None
            try:
                connection = sqlite3.connect(self.path, timeout=5, isolation_level=None, check_same_thread=False)
                # Concurrent first opens can fail a journal-mode lock upgrade
                # immediately, even with SQLite's normal busy timeout.
                connection.execute("PRAGMA busy_timeout=50")
                deadline = time.monotonic() + 5
                while True:
                    try:
                        connection.execute("PRAGMA journal_mode=WAL")
                        break
                    except sqlite3.OperationalError as exc:
                        if exc.sqlite_errorcode not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or time.monotonic() >= deadline:
                            raise
                        time.sleep(.025)
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA synchronous=NORMAL")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > 1:
                    raise PixivPolicyError("共享网络调度库来自较新版本，请更新 MOKU")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS owners (
                        id TEXT PRIMARY KEY, pid INTEGER NOT NULL, created INTEGER NOT NULL,
                        mode TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS tickets (
                        id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                        lane TEXT NOT NULL, priority INTEGER NOT NULL, born REAL NOT NULL,
                        running INTEGER NOT NULL DEFAULT 0);
                    CREATE INDEX IF NOT EXISTS ticket_queue ON tickets(kind, lane, running);
                    CREATE TABLE IF NOT EXISTS lanes (
                        name TEXT PRIMARY KEY, credits REAL NOT NULL, updated REAL NOT NULL,
                        next_send REAL NOT NULL, burst_after REAL NOT NULL, refill REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS policy (
                        id INTEGER PRIMARY KEY CHECK(id=1), cooldown REAL NOT NULL, limits INTEGER NOT NULL);
                    INSERT OR IGNORE INTO policy VALUES(1,0,0);
                    PRAGMA user_version=1;
                """)
                connection.commit()
                connection.execute("PRAGMA busy_timeout=50")
                self._connection = connection
                self._ready = True
            except sqlite3.Error as exc:
                raise PixivPolicyError("共享网络调度库初始化失败，请检查资料目录") from exc
            finally:
                if not self._ready and connection is not None:
                    connection.close()

    @contextmanager
    def _transaction(self, check=lambda: None):
        self._initialize()
        connection = self._connection
        deadline = time.monotonic() + 5
        while True:
            check()
            if time.monotonic() >= deadline:
                raise RequestDeferredError("request_queue", 1)
            if not self._lock.acquire(timeout=.05):
                continue
            try:
                dead = []
                now = time.monotonic()
                if now >= self._gc_at:
                    owners = connection.execute("SELECT id,pid,created FROM owners").fetchall()
                    # Native liveness queries run outside the write transaction.
                    for owner, pid, created in owners:
                        identity = process_identity(pid)
                        if identity is None or (identity != -1 and identity != created):
                            dead.append(owner)
                    self._gc_at = now + .5
                connection.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                self._lock.release()
                if exc.sqlite_errorcode not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise PixivPolicyError("共享网络调度库读写失败，请检查资料目录") from exc
                time.sleep(.01)
            except BaseException:
                self._lock.release()
                raise
        try:
            for owner in dead:
                connection.execute("DELETE FROM tickets WHERE owner=?", (owner,))
                connection.execute("DELETE FROM owners WHERE id=?", (owner,))
            if connection.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 0:
                connection.execute("DELETE FROM lanes")
            connection.execute("INSERT OR IGNORE INTO owners VALUES(?,?,?,?)", (self.owner, self.pid, self.created, self._mode))
            released = tuple(self._released)
            for token in released:
                connection.execute("DELETE FROM tickets WHERE id=?", (token,))
            yield connection
            connection.commit()
            self._released.difference_update(released)
        except sqlite3.Error as exc:
            connection.rollback()
            raise PixivPolicyError("共享网络调度库读写失败，请检查资料目录") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._lock.release()

    def set_mode(self, mode: str):
        with self._lock:
            with self._transaction() as connection:
                connection.execute("UPDATE owners SET mode=? WHERE id=?", (mode, self.owner))
            self._mode = mode

    def snapshot(self) -> dict:
        with self._transaction() as connection:
            cooldown, limits = connection.execute("SELECT cooldown,limits FROM policy WHERE id=1").fetchone()
            active, queued = connection.execute("SELECT COALESCE(SUM(running),0),COUNT(*)-COALESCE(SUM(running),0) FROM tickets WHERE kind='slot'").fetchone()
        return {"cooldownSeconds": max(0, math.ceil(cooldown - time.time())), "active": active, "queued": queued, "rateLimits": limits}

    def restore_cooldown(self, seconds: float):
        if seconds > 0:
            with self._transaction() as connection:
                connection.execute("UPDATE policy SET cooldown=MAX(cooldown,?) WHERE id=1", (time.time() + seconds,))

    def limit(self, seconds: float | None) -> RequestDeferredError:
        with self._transaction() as connection:
            count = connection.execute("SELECT limits FROM policy WHERE id=1").fetchone()[0] + 1
            delay = max(1, seconds if seconds is not None else min(600, 60 * 2 ** min(count - 1, 4)))
            connection.execute("UPDATE policy SET cooldown=MAX(cooldown,?),limits=? WHERE id=1", (time.time() + delay, count))
            remaining = connection.execute("SELECT cooldown FROM policy WHERE id=1").fetchone()[0] - time.time()
            connection.execute("UPDATE lanes SET credits=0,updated=?,next_send=MAX(next_send,?),burst_after=MAX(burst_after,?)", (time.monotonic()+remaining,time.monotonic()+remaining,time.monotonic()+remaining))
        return RequestDeferredError("upstream_rate_limit", remaining)

    @contextmanager
    def _ticket(self, kind, lane, priority, check):
        token = uuid.uuid4().hex
        with self._transaction(check) as connection:
            count = connection.execute("SELECT COUNT(*) FROM tickets WHERE kind=? AND lane=? AND running=0", (kind, lane)).fetchone()[0]
            if count >= 64:
                raise RequestDeferredError("request_queue", 2)
            connection.execute("INSERT INTO tickets(id,owner,kind,lane,priority,born) VALUES(?,?,?,?,?,?)", (token,self.owner,kind,lane,priority,time.monotonic()))
        try:
            yield token
        finally:
            # Retain cleanup intent if a temporary database failure prevents
            # release. The next successful transaction completes the deletion.
            with self._lock:
                self._released.add(token)
            with self._transaction():
                pass

    def _turn(self, connection, token, kind, lane):
        cooldown = connection.execute("SELECT cooldown FROM policy WHERE id=1").fetchone()[0] - time.time()
        if cooldown > 0:
            raise RequestDeferredError("upstream_rate_limit", cooldown)
        safe = connection.execute("SELECT 1 FROM owners JOIN tickets ON owners.id=tickets.owner WHERE owners.mode='safe' LIMIT 1").fetchone() is not None
        first = connection.execute("SELECT id FROM tickets WHERE kind=? AND lane=? AND running=0 ORDER BY MAX(0,priority-(?-born)/5),born,id LIMIT 1", (kind,lane,time.monotonic())).fetchone()
        return first is not None and first[0] == token, safe

    @contextmanager
    def slot(self, lane: str, check, *, priority: int = 0, maximum: int = 2):
        deadline = time.monotonic() + 15
        with self._ticket("slot", lane, priority, check) as token:
            while True:
                check()
                if time.monotonic() >= deadline:
                    raise RequestDeferredError("request_queue", 2)
                with self._transaction(check) as connection:
                    first, safe = self._turn(connection, token, "slot", lane)
                    active = connection.execute("SELECT COUNT(*) FROM tickets WHERE kind='slot' AND lane=? AND running=1", (lane,)).fetchone()[0]
                    shared_maximum = 1 if safe else 2
                    if lane == "download" and not safe:
                        ordinary = connection.execute("SELECT 1 FROM tickets JOIN owners ON tickets.owner=owners.id WHERE tickets.kind='slot' AND tickets.lane=? AND owners.mode!='fast' LIMIT 1", (lane,)).fetchone() is not None
                        if not ordinary:
                            shared_maximum = 4
                    admitted = first and active < min(maximum, shared_maximum)
                    if admitted:
                        connection.execute("UPDATE tickets SET running=1 WHERE id=?", (token,))
                if admitted:
                    break
                time.sleep(.05)
            yield

    def before_send(self, image_only: bool, check, *, priority=0, delay=.275, backoff=0):
        lane = "image" if image_only else "api"
        with self._ticket("send", lane, priority, check) as token:
            while True:
                check()
                with self._transaction(check) as connection:
                    first, safe = self._turn(connection, token, "send", lane)
                    if safe and priority >= 2:
                        raise RequestDeferredError("background_paused", 1)
                    now = time.monotonic()
                    interval = max(delay, (1.5 if lane == "api" else .35) if safe else 0) * (2 ** backoff)
                    capacity = 1 if safe or backoff else 4 if image_only else 2
                    connection.execute("INSERT OR IGNORE INTO lanes VALUES(?,1,?,0,0,?)", (lane,now,interval))
                    credits, updated, next_send, burst_after, refill = connection.execute("SELECT credits,updated,next_send,burst_after,refill FROM lanes WHERE name=?", (lane,)).fetchone()
                    credits = min(capacity, credits + max(0,now-updated)/max(interval,refill))
                    burst = not safe and not backoff and priority == 0
                    admitted = first and credits >= 1 and now >= (burst_after if burst else next_send)
                    connection.execute("UPDATE lanes SET credits=?,updated=?,refill=? WHERE name=?", (credits,now,interval,lane))
                    if admitted:
                        connection.execute("UPDATE lanes SET credits=credits-1,next_send=?,burst_after=? WHERE name=?", (now+interval, now+(.025 if image_only else .04),lane))
                if admitted:
                    return
                time.sleep(.05)
