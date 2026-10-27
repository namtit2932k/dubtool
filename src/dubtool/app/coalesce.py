"""Progress-event coalescing (AGENT_UPDATE_6.md 4.3).

The pipeline emits an event per segment/stage step; the UI must handle at
most ~10 per second or the log/repaint work dominates the event loop. The
gate keeps the LATEST event per (stage, state) slot and never drops the
terminal states ("done", "error", "skipped", "cancel").

Pure logic here (`ProgressGate`); `main_window.py` wires a 100 ms QTimer
that drains the gate, which caps UI updates at 10/s.
"""

from __future__ import annotations

TERMINAL_STATES = ("done", "error", "skipped", "cancel", "finished")


class ProgressGate:
    def __init__(self, limit_per_second: int = 10):
        self._min_interval = 1.0 / max(1, int(limit_per_second))
        self._pending: dict[tuple[str, str], tuple] = {}
        self._last_flush = 0.0  # monotonic seconds (caller's clock)

    def feed(
        self,
        stage: str,
        state: str,
        done: int,
        total: int,
        message: str,
        eta: float,
        now: float,
    ) -> list[tuple]:
        """Add one event; return the events to render right now.

        Terminal events pass through immediately (flushing anything queued
        for the same stage first, so ordering stays logical). Ordinary "
        progress" events accumulate and are returned once per second-slot —
        i.e. when `now - last_flush >= 1/limit`.
        """
        event = (stage, state, done, total, message, eta)
        if state in TERMINAL_STATES:
            # Terminal: render everything still queued for this stage (any
            # state, latest per slot) and then this event — ordering stays
            # logical and the terminal state can never be dropped.
            out = [e for slot, e in sorted(self._pending.items(),
                                           key=lambda kv: kv[0])
                   if slot[0] == stage]
            self._pending = {slot: e for slot, e in self._pending.items()
                             if slot[0] != stage}
            out.append(event)
            self._last_flush = now
            return out
        self._pending[(stage, state)] = event
        if now - self._last_flush >= self._min_interval:
            return self.drain(now)
        return []

    def drain(self, now: float) -> list[tuple]:
        """All queued events (latest per slot), in stable insertion order."""
        if not self._pending:
            return []
        events = list(self._pending.values())
        self._pending.clear()
        self._last_flush = now
        return events
