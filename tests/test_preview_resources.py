from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from preview_resources import PreviewCancelledError, PreviewResources


class _ObservedCancellation(threading.Event):
    def __init__(self):
        super().__init__()
        self.checks = 0
        self.waiting = threading.Event()

    def is_set(self):
        self.checks += 1
        if self.checks >= 2:
            self.waiting.set()
        return super().is_set()


class PreviewResourceTests(unittest.TestCase):
    def test_concurrent_consumers_share_a_zip_and_one_can_leave_without_stopping_the_other(self):
        resources = PreviewResources()
        started, finish = threading.Event(), threading.Event()
        cancel_first, cancel_second = _ObservedCancellation(), _ObservedCancellation()
        calls = []

        def fetch(stop):
            calls.append("zip")
            started.set()
            self.assertTrue(finish.wait(3))
            self.assertFalse(stop.is_set(), "one departing viewer cancelled another viewer's download")
            return b"ZIP"

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(resources.get, ("zip", "1", None), fetch, ttl=15, cancel_event=cancel_first)
            self.assertTrue(started.wait(2))
            second = workers.submit(resources.get, ("zip", "1", None), fetch, ttl=15, cancel_event=cancel_second)
            try:
                self.assertTrue(cancel_first.waiting.wait(2))
                self.assertTrue(cancel_second.waiting.wait(2))
                cancel_first.set()
                with self.assertRaises(PreviewCancelledError):
                    first.result(timeout=2)
                finish.set()
                self.assertEqual(second.result(timeout=2), b"ZIP")
                self.assertEqual(calls, ["zip"])
                self.assertEqual(resources.get(("zip", "1", None), fetch, ttl=15), b"ZIP")
                self.assertEqual(calls, ["zip"])
            finally:
                finish.set()
                resources.clear()

    def test_the_last_departing_consumer_stops_the_upstream_operation(self):
        resources = PreviewResources()
        cancel = _ObservedCancellation()
        started, stopped = threading.Event(), threading.Event()

        def fetch(stop):
            started.set()
            if stop.wait(2):
                stopped.set()
            return b"cancelled"

        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(resources.get, ("zip", "1", None), fetch, ttl=15, cancel_event=cancel)
            self.assertTrue(started.wait(2))
            cancel.set()
            with self.assertRaises(PreviewCancelledError):
                pending.result(timeout=2)
            self.assertTrue(stopped.wait(2))
        resources.clear()

    def test_clear_prevents_a_late_result_from_repopulating_the_cache(self):
        resources = PreviewResources()
        started, finish = threading.Event(), threading.Event()

        def fetch(_stop):
            started.set()
            self.assertTrue(finish.wait(2))
            return {"secret": "old-account"}

        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(resources.get, ("meta", "1", 7), fetch, ttl=60)
            self.assertTrue(started.wait(2))
            resources.clear()
            finish.set()
            with self.assertRaises(PreviewCancelledError):
                pending.result(timeout=2)
        self.assertEqual(resources.get(("meta", "1", 7), lambda _stop: {"secret": "new-account"}, ttl=60),
                         {"secret": "new-account"})

    def test_cached_metadata_is_not_mutated_by_a_caller_and_large_zip_is_not_retained(self):
        resources = PreviewResources(max_bytes=256)
        value = resources.get(("meta", "1", None), lambda _stop: {"frames": [1]}, ttl=60)
        value["frames"].append(2)
        self.assertEqual(resources.get(("meta", "1", None), lambda _stop: self.fail("metadata was not cached"), ttl=60),
                         {"frames": [1]})
        calls = []

        def fetch(_stop):
            calls.append(1)
            return b"a" * 257

        resources.get(("zip", "1", None), fetch, ttl=15)
        resources.get(("zip", "1", None), fetch, ttl=15)
        self.assertEqual(calls, [1, 1])


if __name__ == "__main__":
    unittest.main()
