"""Process-wide pacing and upstream cooldown for Pixiv requests."""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import time

from pixiv_adapter import PixivPolicyError
from download_feedback import DownloadFeedback
from request_rhythm import RequestRhythm
from shared_request_budget import RequestDeferredError, SharedRequestBudget, retry_after_seconds


REQUEST_PRIORITY = ContextVar("pixiv_request_priority", default=0)


@dataclass(eq=False)
class _Ticket:
    priority: int
    created: float


class RequestBlockedError(PixivPolicyError):
    def __init__(self, kind: str):
        self.kind = kind
        super().__init__({
            "verification_required": "Pixiv 要求完成页面验证，请打开官方登录页面处理后继续",
            "authentication_required": "Pixiv 登录状态已失效，请重新登录后继续",
            "access_denied": "Pixiv 拒绝了当前访问，请检查作品权限或在官方页面确认访问状态",
            "not_found": "作品已删除或当前账户无权查看",
        }[kind])


class RequestCoordinator:
    # Each tuple holds concurrency and the base/span of sustained timing.
    # Normal foreground requests can also use the bounded burst allowance.
    PROFILES = {
        "safe": {"api": (1, 1.5, 1.0), "image": (1, .35, .35), "download": (1, .35, .35)},
        "normal": {"api": (2, .20, .15), "image": (2, .08, .08), "download": (2, .08, .08)},
        "fast": {"api": (2, .20, .15), "image": (2, .08, .08), "download": (4, .04, .04)},
    }

    def __init__(self, path: Path, *, shared: SharedRequestBudget | None = None):
        self._shared = shared
        self._path = path
        self._condition = threading.Condition()
        self._mode = "safe"
        self._cooldown = 0.0
        self._blocked = ""
        self._active = {lane: 0 for lane in self.PROFILES[self._mode]}
        self._next_send = {"api": 0.0, "image": 0.0}
        self._burst_after = {"api": 0.0, "image": 0.0}
        self._credits = {"api": 1.0, "image": 1.0}
        self._credit_at = {"api": time.monotonic(), "image": time.monotonic()}
        self._refill_interval = {"api": .275, "image": .12}
        self._rhythm = RequestRhythm()
        self._feedback = DownloadFeedback()
        self._backoff = 0
        self._last_failure = 0.0
        self._successes = 0
        self._queues = {lane: deque() for lane in self._active}
        self._send_queues = {"api": [], "image": []}
        self._requests = 0
        self._limits = 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("mode"), str) or data["mode"] not in self.PROFILES:
                raise ValueError("Invalid request settings")
            deadline = data.get("cooldownUntil", 0)
            if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline < 0:
                raise ValueError("Invalid cooldown")
            blocked = data.get("blockedKind", "")
            if blocked not in ("", "verification_required", "authentication_required"):
                raise ValueError("Invalid blocked state")
            self._blocked = blocked
            self._mode = data["mode"]
            self._cooldown = time.monotonic() + max(0.0, deadline - time.time())
        except FileNotFoundError:
            pass
        except (OSError, ValueError, OverflowError):
            logging.getLogger("moku.http").warning("Request settings could not be restored; using safe mode")
        if self._cooldown > time.monotonic():
            self._backoff = 1
            self._last_failure = time.monotonic()
        self._feedback.reset(initial=2 if self._mode == "fast" else 1,
                             maximum=self.PROFILES[self._mode]["download"][0])
        for lane in self._refill_interval:
            _, interval, jitter = self.PROFILES[self._mode][lane]
            self._refill_interval[lane] = interval + jitter / 2
        self._credits = {lane: self._capacity(lane) for lane in self._credits}
        if shared is not None:
            shared.set_mode(self._mode)
            shared.restore_cooldown(max(0, self._cooldown - time.monotonic()))

    def _save_locked(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="request-settings-", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"mode": self._mode, "blockedKind": self._blocked, "cooldownUntil": time.time() + max(0.0, self._cooldown - time.monotonic())}, stream)
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def set_mode(self, mode: str):
        if mode not in self.PROFILES:
            raise ValueError("请求模式无效")
        with self._condition:
            previous = self._mode
            if self._shared is not None:
                self._shared.set_mode(mode)
            self._mode = mode
            try:
                self._save_locked()
            except OSError:
                self._mode = previous
                if self._shared is not None:
                    self._shared.set_mode(previous)
                raise
            # Mode switches never mint additional burst credit.
            for lane in self._credits:
                self._credits[lane] = min(self._credits[lane], self._capacity(lane))
                _, interval, jitter = self.PROFILES[self._mode][lane]
                self._refill_interval[lane] = interval + jitter / 2
            self._condition.notify_all()
            if previous != mode:
                self._feedback.reset(initial=2 if mode == "fast" else 1,
                                     maximum=self.PROFILES[mode]["download"][0])
        return self.snapshot()

    def snapshot(self):
        shared = self._shared.snapshot() if self._shared is not None else {}
        with self._condition:
            return {"mode": self._mode, "blockedKind": self._blocked, "cooldownSeconds": max(shared.get("cooldownSeconds", 0), 0, math.ceil(self._cooldown - time.monotonic())),
                    "queued": sum(map(len, self._queues.values())), "active": sum(self._active.values()),
                    "requests": self._requests, "rateLimits": self._limits,
                    "backoffLevel": self._backoff, "activity": self._rhythm.phase, "shared": shared,
                    "downloadFeedback": self._feedback.snapshot(), "downloadWidth": self.download_width()}

    def download_width(self):
        with self._condition:
            maximum = self.PROFILES[self._mode]["download"][0]
            return min(self._feedback.width, max(1, maximum // (2 ** self._backoff)))

    def detail_width(self):
        with self._condition:
            return max(1, self.PROFILES[self._mode]["api"][0] // (2 ** self._backoff))

    def download_lookahead(self):
        with self._condition:
            if self._mode != "fast" or self._backoff >= 2:
                return 0
            return 1 if self._backoff else min(2, self._feedback.width)

    def start_download_sample(self, route, *, archive=False):
        with self._condition:
            maximum = self.PROFILES[self._mode]["download"][0]
            return self._feedback.start((route, archive), maximum=min(2, maximum) if archive else maximum)

    def finish_download_sample(self, sample, success):
        if sample is not None:
            with self._condition:
                grow = self._mode != "safe" and self._backoff == 0
            sample.finish(success, allow_growth=grow)

    def limit(self, retry_after: str | None):
        seconds = retry_after_seconds(retry_after)
        if self._shared is not None:
            seconds = self._shared.limit(seconds).retry_after
        with self._condition:
            self._limits += 1
            self._failure_locked()
            seconds = max(1.0, seconds if seconds is not None else min(600, 60 * 2 ** min(self._limits - 1, 4)))
            self._cooldown = max(self._cooldown, time.monotonic() + seconds)
            try:
                self._save_locked()
            except OSError:
                logging.getLogger("moku.http").warning("Upstream cooldown is active; persistence failed")
            self._condition.notify_all()
            return RequestDeferredError("upstream_rate_limit", self._cooldown - time.monotonic())

    def _capacity(self, lane):
        if self._mode == "safe" or self._backoff:
            return 1.0
        return 2.0 if lane == "api" else 4.0

    def _failure_locked(self):
        self._feedback.penalize()
        self._backoff = min(2, self._backoff + 1)
        self._last_failure = time.monotonic()
        self._successes = 0
        for lane in self._credits:
            self._credits[lane] = 0.0
            self._credit_at[lane] = self._last_failure
            self._next_send[lane] = max(self._next_send[lane], self._last_failure + self._refill_interval[lane] * (2 ** self._backoff))

    def failure(self):
        with self._condition:
            self._failure_locked()
            self._condition.notify_all()

    def success(self):
        with self._condition:
            self._successes += 1
            if self._backoff and self._successes >= 8 and time.monotonic() - self._last_failure >= 30:
                self._backoff -= 1
                self._successes = 0
                self._condition.notify_all()

    @staticmethod
    def _first(queue, now):
        # Age background work toward foreground priority to bound starvation.
        return min(queue, key=lambda ticket: (max(0, ticket.priority - (now - ticket.created) / 5), ticket.created))

    def _check_state(self, priority):
        if self._blocked:
            raise RequestBlockedError(self._blocked)
        if priority >= 2 and self._mode == "safe":
            raise RequestDeferredError("background_paused", 1)
        remaining = self._cooldown - time.monotonic()
        if remaining > 0:
            raise RequestDeferredError("upstream_rate_limit", remaining)

    def block(self, kind):
        with self._condition:
            self._blocked = kind
            self._failure_locked()
            try:
                self._save_locked()
            except OSError:
                logging.getLogger("moku.http").warning("Pixiv verification pause persistence failed")
            self._condition.notify_all()
        return RequestBlockedError(kind)

    def _clear_block(self, blocked_kind: str | None = None):
        with self._condition:
            if blocked_kind is None or self._blocked == blocked_kind:
                previous = self._blocked
                self._blocked = ""
                try:
                    self._save_locked()
                except OSError:
                    self._blocked = previous
                    raise
                self._condition.notify_all()

    def session_connected(self):
        self._clear_block("authentication_required")

    def resume(self):
        self._clear_block()
        return self.snapshot()

    def _check_shared_state(self, check, priority):
        check()
        with self._condition:
            self._check_state(priority)

    def before_send(self, image_only: bool, check, *, priority: int = 0, activity: str = ""):
        lane = "image" if image_only else "api"
        profile_lane = "download" if image_only and priority == 1 else lane
        if self._shared is not None:
            with self._condition:
                self._check_state(priority)
                _, interval, jitter = self.PROFILES[self._mode][profile_lane]
                delay = self._rhythm.interval(self._mode, lane, priority, time.monotonic(), interval, jitter, activity)
                backoff = self._backoff
            self._shared.before_send(image_only, lambda: self._check_shared_state(check, priority), priority=priority, delay=delay, backoff=backoff)
            with self._condition:
                self._requests += 1
            return
        ticket = _Ticket(priority, time.monotonic())
        with self._condition:
            queue = self._send_queues[lane]
            queue.append(ticket)
        try:
            while True:
                check()
                with self._condition:
                    self._check_state(priority)
                    now = time.monotonic()
                    elapsed = max(0.0, now - self._credit_at[lane])
                    capacity = self._capacity(lane)
                    refill = self._refill_interval[lane] * (2 ** self._backoff)
                    self._credits[lane] = min(capacity, self._credits[lane] + elapsed / refill)
                    self._credit_at[lane] = now
                    foreground_burst = self._mode != "safe" and priority == 0 and not self._backoff
                    timing_ready = now >= (self._burst_after[lane] if foreground_burst else self._next_send[lane])
                    if self._first(queue, now) is ticket and self._credits[lane] >= 1 and timing_ready:
                        _, interval, jitter = self.PROFILES[self._mode][profile_lane]
                        delay = self._rhythm.interval(self._mode, lane, priority, now, interval, jitter, activity)
                        self._refill_interval[lane] = delay
                        self._next_send[lane] = now + delay * (2 ** self._backoff)
                        self._burst_after[lane] = now + (.04 if lane == "api" else .025)
                        self._credits[lane] -= 1
                        self._requests += 1
                        return
                    self._condition.wait(.05)
        finally:
            with self._condition:
                queue.remove(ticket)
                self._condition.notify_all()

    @contextmanager
    def slot(self, image_only: bool, check, *, download: bool = False, priority: int = 0):
        lane = "download" if image_only and download else "image" if image_only else "api"
        ticket = _Ticket(priority, time.monotonic())
        acquired = False
        deadline = time.monotonic() + 15
        with self._condition:
            queue = self._queues[lane]
            if len(queue) >= 64:
                raise RequestDeferredError("request_queue", 2)
            queue.append(ticket)
        try:
            while True:
                check()
                with self._condition:
                    self._check_state(priority)
                    now = time.monotonic()
                    if now >= deadline:
                        raise RequestDeferredError("request_queue", 2)
                    maximum, _, _ = self.PROFILES[self._mode][lane]
                    if self._backoff:
                        maximum = max(1, maximum // (2 ** self._backoff))
                    if lane == "download":
                        maximum = min(maximum, self._feedback.width)
                    if self._first(queue, now) is ticket and self._active[lane] < maximum:
                        queue.remove(ticket)
                        self._active[lane] += 1
                        acquired = True
                        self._condition.notify_all()
                        break
                    self._condition.wait(.05)
        finally:
            if not acquired:
                with self._condition:
                    queue.remove(ticket)
                    self._condition.notify_all()
        try:
            if self._shared is None:
                yield
            else:
                with self._shared.slot(lane, lambda: self._check_shared_state(check, priority), priority=priority, maximum=maximum):
                    yield
        finally:
            with self._condition:
                self._active[lane] -= 1
                self._condition.notify_all()
