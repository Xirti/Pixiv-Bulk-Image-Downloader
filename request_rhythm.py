"""Small, correlated timing model for queued Pixiv operations."""
from __future__ import annotations

from dataclasses import dataclass
import random


@dataclass
class _Pace:
    level: float = .5
    updated: float = 0.0
    run: int = 0
    rest_after: int = 12


class RequestRhythm:
    def __init__(self, rng=None):
        self._random = rng if rng is not None else random.Random()
        self._pace = {"api": _Pace(), "image": _Pace()}
        self.phase = "idle"

    def interval(self, mode, lane, priority, now, minimum, variation, activity=""):
        pace = self._pace[lane]
        if pace.updated and now - pace.updated > 15:
            pace.level = self._random.uniform(.25, .75)
            pace.run = 0
        # Adjacent requests share a slowly changing pace instead of choosing
        # unrelated intervals. Timing remains bounded by the profile range.
        pace.level = .88 * pace.level + .12 * self._random.random()
        pace.updated = now
        phase = "download" if priority == 1 else "prefetch" if priority >= 2 else activity or "browse"
        if phase == "detail" and self.phase != phase:
            pace.level = max(.55, pace.level)
        self.phase = phase
        delay = minimum + variation * pace.level
        if priority >= 2:
            delay *= 1.15
        if mode == "safe" and priority == 1:
            pace.run += 1
            if pace.run >= pace.rest_after:
                delay += self._random.uniform(2, 4)
                pace.run = 0
                pace.rest_after = self._random.randint(8, 16)
        return delay
