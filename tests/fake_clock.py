"""AGENT_UPDATE_11 replay helpers: a virtual (fake) clock.

`fast_clock()` patches the global time.monotonic / time.sleep / time.time so
every scheduled wait (model backoffs, TTS cooldowns, ladder delays) advances
a virtual timeline instead of blocking real seconds, while keeping the same
relative durations. Tests capture `clock.timeline`-style marks through the
returned FakeClock (`clock.now`, or `clock` itself is a float-convertible).

Only the plain module-level lookups are redirected — code that captured a
reference to a time function at import time (for example asyncio event
loops) keeps real time; the replay tests avoid depending on that.
"""

from __future__ import annotations

import time as _real_time
from contextlib import contextmanager
from unittest import mock


class FakeClock:
    """Virtual monotonic clock driven by time.sleep."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def set(self, value: float) -> None:
        self.now = float(value)


@contextmanager
def fast_clock(start: float = 1000.0, monkeypatch=None):
    """Advance simulated time instead of sleeping for real.

    Used directly (`with fast_clock() as clock:`) or with a pytest
    monkeypatch to keep the patch scoped to the test.
    """
    clock = FakeClock(start)
    patchers = [
        mock.patch("time.monotonic", clock.monotonic),
        mock.patch("time.sleep", clock.sleep),
        mock.patch("time.time", clock.time),
    ]
    for patcher in patchers:
        patcher.start()
    if monkeypatch is not None:
        # keep other monkeypatch undo order intact: nothing extra needed,
        # recorded so callers can introspect.
        monkeypatch._mock_clock = clock  # type: ignore[attr-defined]
    try:
        yield clock
    finally:
        for patcher in reversed(patchers):
            patcher.stop()
