from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(frozen=True)
class TimingEvent:
    label: str
    elapsed: float
    total: float


class PerformanceTimer:
    """Small request-local timer for verbose analysis diagnostics."""

    def __init__(self):
        now = time.perf_counter()
        self._start = now
        self._last = now
        self._events: list[TimingEvent] = []

    def mark(self, label: str) -> None:
        now = time.perf_counter()
        self._events.append(
            TimingEvent(
                label=label,
                elapsed=now - self._last,
                total=now - self._start,
            )
        )
        self._last = now

    @property
    def events(self) -> list[TimingEvent]:
        return list(self._events)


def timing_messages(events: list[TimingEvent]) -> list[str]:
    return [
        f"Timing: {event.label} {event.elapsed:.3f}s (total {event.total:.3f}s)"
        for event in events
    ]
