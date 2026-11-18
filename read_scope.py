"""One read owns registration, contextual checks and final publication permission."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
import threading

from account_pages import ACCOUNT_CACHE_ONLY
from pixiv_account import ACCOUNT_REQUEST_CHECK, AccountError
from request_coordinator import REQUEST_PRIORITY, TrackedReadEvent


@dataclass(frozen=True)
class ReadLease:
    authorized: bool
    authorization_epoch: int | None
    generation: int
    cancel_event: threading.Event | None
    _check_current: Callable[[threading.Event | None, int, int | None], None] = field(repr=False)

    def check(self) -> None:
        """Also use as the response hook, after encoding and before headers."""
        self._check_current(self.cancel_event, self.generation, self.authorization_epoch)


class ReadScopes:
    def __init__(self, *, track, capture_identity, check_current):
        self._track = track
        self._capture_identity = capture_identity
        self._check_current = check_current

    @contextmanager
    def request(self, request_id: str, *, capture_identity=None, require_account=False,
                priority: int | None = None, cache_only: bool | None = None):
        with self._track(request_id) as cancel_event:
            capture = self._capture_identity if capture_identity is None else capture_identity
            authorized, epoch, generation = capture()
            if require_account and not authorized:
                raise AccountError("reauth", "请先在「我的账号」完成所选方式的登录")
            lease = ReadLease(authorized, epoch, generation, cancel_event, self._check_current)
            with ExitStack() as bindings:
                token = ACCOUNT_REQUEST_CHECK.set(lease.check)
                bindings.callback(ACCOUNT_REQUEST_CHECK.reset, token)
                if priority is not None:
                    if priority == 1 and isinstance(cancel_event, TrackedReadEvent):
                        priority = cancel_event.priority
                    token = REQUEST_PRIORITY.set(priority)
                    bindings.callback(REQUEST_PRIORITY.reset, token)
                if cache_only is not None:
                    token = ACCOUNT_CACHE_ONLY.set(cache_only)
                    bindings.callback(ACCOUNT_CACHE_ONLY.reset, token)
                lease.check()
                yield lease
