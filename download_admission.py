"""Admit download execution and own its update, priority and lookahead resources."""
from __future__ import annotations

import threading

from request_coordinator import REQUEST_PRIORITY


class DownloadAdmission:
    def __init__(self, *, updates, ahead, network_ready, capacity):
        self._updates = updates
        self._ahead = ahead
        self._network_ready = network_ready
        self._slots = threading.BoundedSemaphore(capacity)

    def run(self, operation, cancel, *, validate):
        with self._updates.gate:
            if self._updates.restarting:
                return {"error": "正在重启更新，请稍后再下载"}, 409
            admitted = self._slots.acquire(blocking=False)
            if admitted:
                self._updates.active_downloads += 1
        if not admitted:
            return {"error": "下载任务繁忙，请等待当前任务完成后重试", "retryable": True}, 429
        try:
            validate()
            self._network_ready()
            priority = REQUEST_PRIORITY.set(1)
            completed = False
            try:
                self._ahead.begin()
                result = operation()
                completed = result[1] == 200
                return result
            finally:
                try:
                    self._ahead.finish(keep=completed and not cancel.is_set())
                finally:
                    REQUEST_PRIORITY.reset(priority)
        finally:
            with self._updates.gate:
                self._updates.active_downloads -= 1
                self._slots.release()
