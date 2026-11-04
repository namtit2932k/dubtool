"""Update 12 task 2.2: the model circuit breaker.

Per model: after `llm.breaker_failures` (default 3) consecutive failed
REQUESTS (not attempts) with 503/504/timeout, the breaker opens for
`llm.breaker_cooldown_s` (default 60): no new request goes to that model
during the cooldown. A half-open probe (one request) decides whether to
close it — success closes it, failure re-opens it (and the open count
rises). Thread-safe: one stage runs several chunk workers on one client.
"""

from __future__ import annotations

import threading


class ModelBreaker:
    def __init__(self, failures: int = 3, cooldown_s: float = 60.0):
        self.failures = max(1, int(failures))
        self.cooldown_s = max(1.0, float(cooldown_s))
        self._lock = threading.Lock()
        self.consecutive = 0
        self.open_until = 0.0
        self.opens = 0

    def remaining_open_s(self, now: float) -> float:
        with self._lock:
            return max(0.0, self.open_until - now)

    def record_success(self) -> None:
        """A successful (half-open probe or normal) request closes it."""
        with self._lock:
            self.consecutive = 0
            self.open_until = 0.0

    def record_failure(self, now: float) -> bool:
        """One failed request. Returns True when this opened the breaker."""
        with self._lock:
            self.consecutive += 1
            if self.consecutive < self.failures:
                return False
            self.consecutive = 0
            self.open_until = now + self.cooldown_s
            self.opens += 1
            return True

    def is_open(self, now: float) -> bool:
        with self._lock:
            return now < self.open_until
