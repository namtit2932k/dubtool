"""Thread-safe sequential API key pool (AGENT_UPDATE_5.md 2.4).

Selection follows list order (priority). Per-key state: `invalid` (401/403)
and `cooldown_until` (monotonic clock, injectable for tests). A rate-limited
key becomes eligible again after its cooldown and is then preferred again
(earliest eligible wins). The pool is shared by the app (keys from the
secret store) and the CLI (keys from .env), passed explicitly — never via
os.environ. Only key *positions* are ever logged, never key values.
"""

from __future__ import annotations

import logging
import threading
import time
import typing

from dubtool.llm.base import KeyExhaustedError

log = logging.getLogger("dubtool")

DEFAULT_COOLDOWN_S = 60.0
DEFAULT_MAX_WAIT_S = 120.0          # one wait is at most this
DEFAULT_MAX_TOTAL_WAIT_S = 600.0    # then give up with a clear error


class KeyPool:
    """Sequential by priority; compare-and-mark thread safety under a lock."""

    def __init__(
        self,
        keys: list[str],
        *,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        max_total_wait_s: float = DEFAULT_MAX_TOTAL_WAIT_S,
        clock: typing.Callable[[], float] = time.monotonic,
        sleep: typing.Callable[[float], None] = time.sleep,
        cancel: threading.Event | None = None,
        model: str = "",
        until_epoch: typing.Callable[[], float] = time.time,
    ):
        if not keys:
            raise KeyExhaustedError("no API keys provided")
        # (value, invalid, daily_exhausted, cooldown_until-monotonic)
        self._states: list[tuple[str, bool, bool, float]] = [
            (k, False, False, 0.0) for k in keys
        ]
        self._lock = threading.Lock()
        self._cooldown_s = cooldown_s
        self._max_wait_s = max_wait_s
        self._max_total_wait_s = max_total_wait_s
        self._clock = clock
        self._sleep = sleep
        self._cancel = cancel
        self.switches = 0
        # AGENT_UPDATE_6.md 5.3: daily quotas are per key AND per model, so
        # a pool always knows its model and consults the persisted state at
        # construction (fingerprint of the key value, never the value).
        self.model = model
        self._until_epoch = until_epoch
        self._apply_persisted_daily()

    def _apply_persisted_daily(self) -> None:
        if not self.model:
            return
        from dubtool.llm import quota_state

        for i, (value, invalid, daily, cooldown) in enumerate(self._states):
            if invalid or daily:
                continue
            until = quota_state.exhausted_until(value, self.model, self._until_epoch())
            if until is not None and until > self._until_epoch():
                self._states[i] = (value, invalid, True, cooldown)

    # --------------------------------------------------------------- utils
    def _position_of(self, value: str) -> int:
        for i, state in enumerate(self._states):
            if state[0] == value:
                return i
        return -1

    def position(self, value: str) -> int:
        """1-based position for log messages only ('key #2')."""
        with self._lock:
            pos = self._position_of(value)
        return pos + 1 if pos >= 0 else 0

    def keys_count(self) -> int:
        with self._lock:
            return len(self._states)

    def values(self) -> list[str]:
        """Key values in priority order (in-memory only; never logged)."""
        with self._lock:
            return [state[0] for state in self._states]

    def failed_count(self) -> int:
        """How many keys are permanently out (invalid or daily-limited)."""
        with self._lock:
            return sum(1 for (_k, invalid, daily, _cd) in self._states if invalid or daily)

    def all_daily_exhausted(self) -> bool:
        """Every key is out via the per-day quota (none merely cooling down)
        — the fail-fast condition (AGENT_UPDATE_6.md 5.3): waiting cannot
        help before midnight Pacific, so the caller must not wait."""
        with self._lock:
            return all(daily for (_k, _invalid, daily, _cd) in self._states) and bool(
                self._states
            )

    # ------------------------------------------------------------ selection
    def acquire(self) -> str:
        """First key (priority order) that is neither invalid, daily-exhausted
        nor cooling down. Raises KeyExhaustedError when every key is
        unavailable right now (the caller may wait; see wait_for_key)."""
        now = self._clock()
        with self._lock:
            for value, invalid, daily, cooldown_until in self._states:
                if invalid or daily:
                    continue
                if cooldown_until > now:
                    continue
                return value
        raise KeyExhaustedError("no API key is available right now")

    def has_eligible_key(self) -> bool:
        try:
            self.acquire()
            return True
        except KeyExhaustedError:
            return False

    # ------------------------------------------------------------- marking
    def _mark(self, value: str, field: int, amount: float = 0.0) -> None:
        """Compare-and-mark: the failure is recorded against the key value
        the failed call actually used (never against an index guessed by the
        caller), so concurrent failures of the same key cannot skip other
        keys. field: 0 invalid, 1 daily, 2 cooldown."""
        with self._lock:
            for i, state in enumerate(self._states):
                if state[0] != value:
                    continue
                key, invalid, daily, cooldown_until = state
                if field == 0:
                    invalid = True
                elif field == 1:
                    daily = True
                else:
                    cooldown_until = max(cooldown_until, self._clock() + max(0.0, amount))
                self._states[i] = (key, invalid, daily, cooldown_until)
                return

    def mark_invalid(self, value: str) -> None:
        self._mark(value, 0)
        log.warning("keypool: key #%d is invalid (401/403) — skipped", self.position(value))

    def mark_daily_limit(
        self, value: str, until_epoch: float | None = None
    ) -> None:
        """Daily exhaustion is per (key, model) and PERSISTED
        (AGENT_UPDATE_6.md 5.3): the next run pre-marks the same pairing."""
        self._mark(value, 1)
        log.warning(
            "keypool: key #%d hit a per-day quota for model %s — unavailable "
            "until its reset",
            self.position(value), self.model or "(model unknown)",
        )
        if self.model and until_epoch is not None:
            from dubtool.llm import quota_state

            quota_state.mark_daily(value, self.model, float(until_epoch),
                                   now=self._until_epoch())

    def mark_rate_limited(self, value: str, retry_after: float | None) -> None:
        """429: cooldown for max(retry-after hint, configured cooldown)."""
        wait = self._cooldown_s
        if retry_after is not None and retry_after > 0:
            wait = max(wait, float(retry_after))
        self._mark(value, 2, wait)
        log.warning(
            "keypool: key #%d is rate limited — cooling down %.1fs",
            self.position(value), wait,
        )

    def mark_rate_limited_once(self, value: str, retry_after: float | None,
                               seen: set[str]) -> None:
        """Idempotent helper for the retry loop: mark `value` only when it is
        not in `seen` yet, then add it. Cooldown extend; several threads
        hitting the same key produce exactly one log line and no key skip."""
        if value in seen:
            return
        seen.add(value)
        self.mark_rate_limited(value, retry_after)

    # ------------------------------------------------------------- waiting
    def earliest_cooldown(self) -> float:
        """Seconds until the earliest cooldown among exhaustible keys."""
        now = self._clock()
        with self._lock:
            pending = [
                cd for (_k, invalid, daily, cd) in self._states
                if not invalid and not daily and cd > now
            ]
        if not pending:
            return 0.0
        return min(pending) - now

    def wait_for_key(self) -> bool:
        """All keys unavailable: wait until the earliest cooldown (one wait is
        at most max_wait_s), repeat until a key is eligible again; give up
        with False after max_total_wait_s in total. Honors the cancel event;
        sleeps in small steps so cancel stays responsive. Returns True when a
        key became available."""
        waited = 0.0
        while True:
            if self.has_eligible_key():
                return True
            remaining_total = self._max_total_wait_s - waited
            if remaining_total <= 0:
                return False
            if self._cancel is not None and self._cancel.is_set():
                return False
            target = min(self._earliest_wait(), remaining_total)
            if target <= 0:
                # Fail fast when nothing is exhaustible: cooldowns never
                # resolve a per-day quota, and an all-daily pool would spin
                # here forever (AGENT_UPDATE_6.md 5.3).
                if self.all_daily_exhausted():
                    return False
                continue  # a race freed a key: recheck
            waited += target
            log.warning(
                "keypool: all keys limited — waiting %.0fs (total %.0fs of %.0fs)",
                target, waited, self._max_total_wait_s,
            )
            slept = 0.0
            while slept < target - 1e-9:
                if self._cancel is not None and self._cancel.is_set():
                    return False
                piece = min(1.0, target - slept)
                self._sleep(piece)
                slept += piece
            if self._cancel is not None and self._cancel.is_set():
                return False

    def _earliest_wait(self) -> float:
        """Wait target: the earliest cooldown, capped per wait; when no key is
        exhaustible (all invalid but one is eligible) it is 0."""
        cd = self.earliest_cooldown()
        if cd <= 0:
            return 0.0
        return min(self._max_wait_s, cd)
