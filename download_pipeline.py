"""Bounded, ordered downloads with one consumer and cancellation-safe cleanup."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import ExitStack, contextmanager
from contextvars import copy_context
from dataclasses import dataclass
import threading
import time


WAIT_FOR_INPUT = object()


@dataclass(frozen=True)
class DownloadSource:
    order: tuple[int, int]
    payload: tuple
    animation: bool


class _Cancellation(threading.Event):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    def is_set(self):
        return super().is_set() or (self.parent is not None and self.parent.is_set())

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            super().wait(.05 if remaining is None else min(.05, remaining))
        return self.is_set()


def consume_downloads(jobs, prepare, consume, *, cancel_event, check, width, maximum=2):
    """Prepare a bounded window; consume in order and close all resources.

    prepare returns a context manager. Its files remain owned until consume
    finishes or the whole pipeline stops. width is re-read at admission so
    mode changes and network backoff also shrink speculative work.
    """
    return _consume_downloads(jobs, prepare, consume, cancel_event=cancel_event,
                              check=check, width=width, maximum=maximum,
                              stop=_Cancellation(cancel_event), streaming=False)


def stream_downloads(groups, resolve, expand, prepare, consume, *, cancel_event, check, width, detail_width):
    """Two detail workers feed bounded transfers as each artwork becomes ready."""
    stop = _Cancellation(cancel_event)
    pending = {}
    inputs = iter(enumerate(groups))
    exhausted = False
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="download-detail") as pool:
        def jobs():
            nonlocal exhausted
            while pending or not exhausted:
                check(stop)
                while not exhausted and len(pending) < min(2, detail_width()):
                    try:
                        index, group = next(inputs)
                    except StopIteration:
                        exhausted = True
                        break
                    pending[pool.submit(copy_context().run, resolve, group, stop)] = index
                ready = next((future for future in pending if future.done()), None)
                if ready is None:
                    yield WAIT_FOR_INPUT
                    continue
                index = pending.pop(ready)
                yield from expand(index, ready.result(), stop)
        try:
            return _consume_downloads(jobs(), prepare, consume, cancel_event=cancel_event,
                                      check=check, width=width, maximum=4,
                                      stop=stop, streaming=True)
        finally:
            stop.set()
            for future in pending:
                future.cancel()


def _consume_downloads(jobs, prepare, consume, *, cancel_event, check, width, maximum, stop, streaming):
    pending = deque()
    animations = set()
    deferred = None
    failures = []
    lock = threading.Lock()
    iterator = iter(jobs)
    exhausted = False
    started = time.monotonic()
    completed = 0
    peak = 0

    with ExitStack() as resources:
        def prepare_owned(job):
            stack = ExitStack()
            with lock:
                resources.callback(stack.close)
            try:
                check(stop)
                return stack.enter_context(prepare(job, stop)), stack
            except Exception as exc:
                with lock:
                    if not failures and not stop.is_set():
                        failures.append(exc)
                stop.set()
                raise

        try:
            with ThreadPoolExecutor(max_workers=maximum, thread_name_prefix="download-source") as pool:
                try:
                    while True:
                        check(stop)
                        limit = max(1, min(maximum, width()))
                        while not exhausted and len(pending) < limit:
                            try:
                                job = deferred if deferred is not None else next(iterator)
                            except StopIteration:
                                exhausted = True
                                break
                            if job is WAIT_FOR_INPUT:
                                break
                            if streaming and job.animation and len(animations) >= 2:
                                deferred = job
                                break
                            deferred = None
                            future = pool.submit(copy_context().run, prepare_owned, job)
                            pending.append(future)
                            if streaming and job.animation:
                                animations.add(future)
                            peak = max(peak, len(pending))
                        if not pending:
                            if exhausted:
                                break
                            stop.wait(.02)
                            continue
                        if streaming:
                            future = next((future for future in pending if future.done()), None)
                            if future is None:
                                stop.wait(.02)
                                continue
                            pending.remove(future)
                        else:
                            future = pending.popleft()
                        while True:
                            check(stop)
                            try:
                                value, stack = future.result(timeout=.05)
                                break
                            except TimeoutError:
                                # A transfer can itself raise TimeoutError.
                                if future.done():
                                    value, stack = future.result()
                                    break
                        try:
                            check(stop)
                            consume(value, stop)
                            completed += 1
                        finally:
                            stack.close()
                            animations.discard(future)
                finally:
                    stop.set()
                    for future in pending:
                        future.cancel()
        except Exception:
            # Surface the source failure that stopped a concurrent conversion,
            # rather than presenting its internal cancellation as a user pause.
            if failures and not (cancel_event is not None and cancel_event.is_set()):
                raise failures[0]
            raise
    return {"sources": completed, "peakSources": peak,
            "seconds": round(time.monotonic() - started, 3)}


class DownloadAhead:
    """Bounded expiring sources across batches; claimed sources own their lifetime.

    Prefetch failure is optional work: the foreground batch still commits,
    and the next batch falls back to its normal request/cooldown path.
    """
    def __init__(self, *, ttl=30, capacity=1):
        self._condition = threading.Condition()
        self._entries = {}
        self._workers = set()
        self._ttl = ttl
        self._capacity = capacity

    def start(self, key, prepare, cancel_event, *, hold=False):
        with self._condition:
            if key in self._entries or len(self._entries) >= self._capacity:
                return False
            entry = {"key": key, "stop": _Cancellation(cancel_event),
                     "ready": False, "claimed": False, "released": False,
                     "value": None, "idle": not hold, "expires": None}
            self._entries[key] = entry
            context = copy_context()
            worker = threading.Thread(target=context.run, args=(self._prepare, entry, prepare),
                                      name="download-ahead", daemon=True)
            self._workers.add(worker)
            try:
                worker.start()
            except Exception:
                del self._entries[key]
                self._workers.remove(worker)
                raise
            return True

    def _prepare(self, entry, prepare):
        try:
            with prepare(entry["stop"]) as value:
                if value is None:
                    return
                with self._condition:
                    entry["value"] = value
                    entry["ready"] = True
                    if entry["idle"] and entry["expires"] is None:
                        entry["expires"] = time.monotonic() + self._ttl
                    self._condition.notify_all()
                    while not entry["released"]:
                        if not entry["claimed"] and (entry["stop"].is_set()
                                or (entry["expires"] is not None and time.monotonic() >= entry["expires"])):
                            entry["released"] = True
                            break
                        self._condition.wait(None if entry["claimed"] else .05)
        except Exception as exc:
            # Optional prefetch cannot invalidate the current committed batch.
            # Foreground retries retain the coordinator's shared cooldown.
            import logging
            logging.getLogger("moku.http").info("download lookahead unavailable category=%s; next batch will request its source", type(exc).__name__)
        finally:
            with self._condition:
                entry["value"] = None
                entry["released"] = True
                if self._entries.get(entry["key"]) is entry:
                    del self._entries[entry["key"]]
                self._workers.discard(threading.current_thread())
                self._condition.notify_all()

    @contextmanager
    def take(self, key, cancel_event):
        entry = None
        value = None
        with self._condition:
            candidate = self._entries.pop(key, None)
            if candidate is not None:
                entry = candidate
                entry["claimed"] = True
                while not candidate["ready"] and not candidate["released"]:
                    if cancel_event is not None and cancel_event.is_set():
                        candidate["stop"].set()
                        break
                    self._condition.wait(.05)
                if candidate["ready"] and not candidate["released"] and not candidate["stop"].is_set():
                    value = candidate["value"]
        try:
            yield value
        finally:
            if entry is not None:
                with self._condition:
                    entry["released"] = True
                    self._condition.notify_all()

    def clear(self, *, wait=False):
        with self._condition:
            for entry in self._entries.values():
                entry["stop"].set()
            self._condition.notify_all()
            if wait:
                self._condition.wait_for(lambda: not self._entries, timeout=2)

    def retain(self, keys):
        with self._condition:
            for key, entry in self._entries.items():
                if key not in keys:
                    entry["stop"].set()
            self._condition.notify_all()

    def close(self):
        self.clear()
        with self._condition:
            self._condition.wait_for(lambda: not self._workers, timeout=2)

    def begin(self):
        with self._condition:
            for entry in self._entries.values():
                entry["idle"] = False
                entry["expires"] = None
            self._condition.notify_all()

    def finish(self, *, keep):
        with self._condition:
            for entry in self._entries.values():
                if keep:
                    entry["idle"] = True
                    entry["expires"] = time.monotonic() + self._ttl
                else:
                    entry["stop"].set()
            self._condition.notify_all()
