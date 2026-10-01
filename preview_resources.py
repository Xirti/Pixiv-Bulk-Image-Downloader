from __future__ import annotations

import copy
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field


class PreviewCancelledError(RuntimeError):
    pass


class PreviewBusyError(RuntimeError):
    pass


@dataclass
class _Load:
    generation: int
    users: int = 0
    stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    value: object = None
    error: Exception | None = None


class PreviewResources:
    """Bounded, account-scoped reuse of animation manifests and small ZIPs.

    Concurrent consumers share a load. Cancelling one consumer cannot cancel
    another; the upstream operation stops only when no consumer remains.
    Network work never runs under the cache lock. Workers are bounded and
    daemonized, so cancelled preview work cannot hold application exit open.
    """

    def __init__(self, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        self._max_bytes = max_bytes
        self._bytes = 0
        self._generation = 0
        self._cache: OrderedDict[tuple, tuple[float, object, int]] = OrderedDict()
        self._loads: dict[tuple, _Load] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(2)

    @staticmethod
    def _check_cancel(event: threading.Event | None) -> None:
        if event is not None and event.is_set():
            raise PreviewCancelledError("动图预览已取消")

    @staticmethod
    def _copy(value):
        return value if isinstance(value, bytes) else copy.deepcopy(value)

    def get(self, key: tuple, load: Callable[[threading.Event], object], *,
            ttl: float, cancel_event: threading.Event | None = None):
        while True:
            self._check_cancel(cancel_event)
            with self._lock:
                now = time.monotonic()
                for old_key, (expires, _value, size) in list(self._cache.items()):
                    if expires <= now:
                        del self._cache[old_key]
                        self._bytes -= size
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    return self._copy(cached[1])
                job = self._loads.get(key)
                if job is None:
                    if len(self._loads) >= 4:
                        raise PreviewBusyError("动图预览繁忙，请稍后再试")
                    job = _Load(self._generation)
                    self._loads[key] = job
                    threading.Thread(target=self._run, args=(key, job, load, ttl),
                                     name="moku-preview", daemon=True).start()
                if not job.stop.is_set():
                    job.users += 1
                    break
            # A fully cancelled load cannot be reused or replaced while its
            # socket is still closing; wait without consuming another worker.
            job.done.wait(.05)
        try:
            while not job.done.wait(.05):
                self._check_cancel(cancel_event)
            self._check_cancel(cancel_event)
            if job.error is not None:
                raise job.error
            self._check_cancel(job.stop)
            return self._copy(job.value)
        finally:
            with self._lock:
                job.users -= 1
                if not job.users and not job.done.is_set():
                    job.stop.set()

    def _run(self, key, job, load, ttl):
        acquired = False
        try:
            while not self._slots.acquire(timeout=.05):
                self._check_cancel(job.stop)
            acquired = True
            self._check_cancel(job.stop)
            job.value = load(job.stop)
            self._check_cancel(job.stop)
            size = (len(job.value) if isinstance(job.value, bytes) else
                    len(json.dumps(job.value, ensure_ascii=False).encode("utf-8")) * 6)
            with self._lock:
                if job.generation == self._generation and not job.stop.is_set() and size <= self._max_bytes:
                    while self._cache and (len(self._cache) >= 64 or self._bytes + size > self._max_bytes):
                        _old_key, (_expires, _value, old_size) = self._cache.popitem(last=False)
                        self._bytes -= old_size
                    self._cache[key] = (time.monotonic() + ttl, job.value, size)
                    self._bytes += size
        except Exception as exc:
            job.error = exc
        finally:
            if acquired:
                self._slots.release()
            with self._lock:
                if self._loads.get(key) is job:
                    del self._loads[key]
                job.done.set()

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._cache.clear()
            self._bytes = 0
            for job in self._loads.values():
                job.stop.set()
