from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Optional

from vocode.logger import logger


@dataclass
class StartupTimingSnapshot:
    label: str
    elapsed_s: float
    delta_s: float


class StartupTimer:
    def __init__(self, *, enabled: bool = False, event: str = "startup timing") -> None:
        self._enabled = enabled
        self._event = event
        self._start = perf_counter()
        self._last = self._start

    @property
    def enabled(self) -> bool:
        return self._enabled

    def mark(self, label: str) -> StartupTimingSnapshot:
        now = perf_counter()
        snapshot = StartupTimingSnapshot(
            label=label,
            elapsed_s=now - self._start,
            delta_s=now - self._last,
        )
        self._last = now
        if self._enabled:
            logger.info(
                self._event,
                label=label,
                elapsed_s=round(snapshot.elapsed_s, 6),
                delta_s=round(snapshot.delta_s, 6),
            )
        return snapshot

    def child(self, event: Optional[str] = None) -> "StartupTimer":
        child = StartupTimer(enabled=self._enabled, event=event or self._event)
        child._start = self._start
        child._last = self._last
        return child
