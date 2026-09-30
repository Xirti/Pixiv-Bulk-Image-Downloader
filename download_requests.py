from __future__ import annotations

import hashlib
import json
import re
import threading
import time
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
    ) -> None:
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("invalid download recovery limits")
        self._limit = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[str, _Request] = OrderedDict()
        self._lock = threading.Lock()

    def run(
        self, request_id: object, route: str, body: dict,
        operation: Callable[[], Response], *, scope: str = "",
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
        now = self._clock()
        with self._lock:
            for key, entry in list(self._entries.items()):
                if not entry.active and now - entry.touched >= self._ttl:
                    del self._entries[key]
            entry = self._entries.get(request_id)
            if entry is not None:
                if entry.fingerprint != fingerprint:
                    raise DownloadRequestError("下载任务内容或账户已变更，请重新发起下载", 409)
                self._entries.move_to_end(request_id)
                if entry.active:
                    return {"pending": True, "retryAfterMs": 1000}, 202
                if entry.result is not None:
                    return entry.result
                entry.active = True
            else:
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
            return result
        finally:
            # Record publication before the caller tries writing the response:
            # a disconnected client must not lose an already-completed result.
            with self._lock:
                entry.result = result if result is not None and result[1] == 200 else None
                entry.active = False
                entry.touched = self._clock()
