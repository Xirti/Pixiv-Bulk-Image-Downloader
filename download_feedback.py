"""Download-only feedback from complete, overlapping transfer windows."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time


@dataclass
class TransferSample:
    owner: DownloadFeedback
    generation: int
    phase: int
    started: float
    bytes: int = 0
    first_byte: float | None = None
    finished: bool = False

    def received(self, size):
        with self.owner._lock:
            if self.first_byte is None and size:
                self.first_byte = self.owner._clock()
            self.bytes += size

    def finish(self, success, *, allow_growth=True):
        self.owner._finish(self, success, allow_growth)


class DownloadFeedback:
    def __init__(self, *, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._generation = 0
        self._route = None
        self._maximum = 4
        self._initial = 2
        self._width = 2
        self._baseline = None
        self._trial = None
        self._cooldown = self._strikes = 0
        self._stable_windows = 0
        self._active = 0
        self._phase = 0
        self._clear_window()
        self._last_window = {}

    def _clear_window(self):
        self._records = []
        self._peak = 0
        self._phase += 1
        self._evaluation_at = self._clock() + 1.5

    def reset(self, *, initial=2, maximum=4):
        with self._lock:
            self._route = None
            self._generation += 1
            self._initial, self._maximum = initial, maximum
            self._width = initial
            self._baseline = self._trial = None
            self._cooldown = self._strikes = self._active = 0
            self._stable_windows = 0
            self._clear_window()
            self._last_window = {}

    @property
    def width(self):
        with self._lock:
            return self._width

    def penalize(self):
        with self._lock:
            self._width = max(1, self._width // 2)
            self._generation += 1
            self._baseline = self._trial = None
            self._active = self._strikes = 0
            self._stable_windows = 0
            self._cooldown = 1
            self._clear_window()

    def start(self, route, *, maximum=4):
        with self._lock:
            now = self._clock()
            if route != self._route:
                self._generation += 1
                self._route, self._maximum = route, maximum
                self._width = min(self._initial, maximum)
                self._baseline = self._trial = None
                self._active = self._cooldown = self._strikes = 0
                self._stable_windows = 0
                self._clear_window()
                self._last_window = {}
            self._active += 1
            self._peak = max(self._peak, self._active)
            return TransferSample(self, self._generation, self._phase, now)

    def _finish(self, sample, success, allow_growth):
        with self._lock:
            if sample.finished:
                return
            sample.finished = True
            if sample.generation != self._generation:
                return
            now = self._clock()
            self._active -= 1
            if not success:
                self._generation += 1
                self._active = 0
                self._clear_window()
                return
            if sample.phase != self._phase:
                return
            self._records.append((sample.started, now, sample.bytes,
                                  None if sample.first_byte is None else sample.first_byte - sample.started))
            self._records = self._records[-256:]
            if now < self._evaluation_at or len(self._records) < max(4, self._width * 4):
                return
            transferred = sum(row[2] for row in self._records)
            if transferred < 128 * 1024:
                return
            # Union of completed transfer intervals: no CPU/pacing idle time,
            # no overlapping double count, and no unvalidated partial bodies.
            intervals = sorted((row[0], row[1]) for row in self._records)
            start, end = intervals[0]
            busy = 0.0
            for left, right in intervals[1:]:
                if left > end:
                    busy += end - start
                    start, end = left, right
                else:
                    end = max(end, right)
            busy += end - start
            if busy < 1.5:
                return
            latencies = [row[3] for row in self._records if row[3] is not None]
            rate = transferred / busy
            latency = sum(latencies) / max(1, len(latencies))
            self._last_window = {"bytesPerSecond": round(rate), "firstByteSeconds": round(latency, 3),
                                 "transfers": len(self._records), "peak": self._peak}
            if self._peak >= self._width:
                self._evaluate(rate, latency, allow_growth)
            self._clear_window()

    def _evaluate(self, rate, latency, allow_growth):
        observed = (rate, latency)
        if self._trial is not None:
            previous, baseline = self._trial
            if self._width < previous:
                if rate >= baseline[0] * .95 and latency <= max(baseline[1] * 1.25, baseline[1] + .10):
                    self._baseline = observed
                else:
                    self._width, self._baseline = previous, baseline
                self._trial = None
                self._stable_windows = 0
                self._cooldown = 3
                return
            if rate >= baseline[0] * 1.12 and latency <= max(baseline[1] * 1.5, baseline[1] + .20):
                self._baseline = observed
                self._cooldown = 1
            else:
                self._width, self._baseline = previous, baseline
                self._cooldown = 2
            self._trial = None
            self._stable_windows = 0
            return
        baseline = self._baseline
        if baseline is not None and rate < baseline[0] * .80 and latency > max(baseline[1] * 1.5, baseline[1] + .15):
            self._strikes += 1
            if self._strikes >= 2:
                self._width = max(1, self._width // 2)
                self._baseline = None
                self._strikes = 0
                self._cooldown = 2
                self._stable_windows = 0
            return
        self._strikes = 0
        self._baseline = observed if baseline is None else (
            .7 * baseline[0] + .3 * rate, .7 * baseline[1] + .3 * latency)
        if self._cooldown:
            self._cooldown -= 1
        elif allow_growth and self._width < self._maximum:
            self._trial = self._width, self._baseline
            self._width = min(self._maximum, self._width * 2)
            self._stable_windows = 0
        elif allow_growth and self._width > 1:
            self._stable_windows += 1
            if self._stable_windows >= 8:
                self._trial = self._width, self._baseline
                self._width //= 2
                self._stable_windows = 0

    def snapshot(self):
        with self._lock:
            return {"width": self._width, "probing": self._trial is not None,
                    "activeTransfers": self._active, "lastWindow": dict(self._last_window)}
