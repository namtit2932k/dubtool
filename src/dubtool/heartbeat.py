"""Stage heartbeats (Update 9 task 3.3).

The progress-details panel must never go quiet: while a stage waits on a
retry cooldown, a rate-limited key pool or an outstanding model request it
emits a progress line every `heartbeat_s` (settings, default 10 s) with
what is happening and how long it has been going. Text is resolved at the
emission point (worker thread) into the active i18n language.

Binding: `api.run_dub` binds one callback per stage on its worker thread
(`heartbeat.bind(emit)`); stages and the llm/tts layers call

  with heartbeat.waiting(lambda s: i18n.tr("hb.model_wait", n=s)):
      provider.complete(...)

so a nested/inside call needs no explicit stage wiring. Every callback is
fire-and-safe: any exception inside a heartbeat is swallowed (progress
reporting can never break a run).
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("dubtool")

try:
    import contextvars

    _cb = contextvars.ContextVar("dubtool-heartbeat", default=None)
except ImportError:  # pragma: no cover
    _cb = None

_KEY = "heartbeat_s"


def bind(cb) -> object:
    """Bind the per-stage emission callback; returns the reset token."""
    if _cb is None:
        return None
    return _cb.set(cb)


def reset(token) -> None:
    if _cb is not None and token is not None:
        try:
            _cb.reset(token)
        except ValueError:
            pass


def active() -> bool:
    return _cb is not None and _cb.get() is not None


def interval() -> float:
    """""Heartbeat period from settings (`heartbeat_s`, default 10 s)."""
    try:
        from dubtool.app import settings as settings_mod

        return max(1.0, float(settings_mod.load().get(_KEY, 10.0) or 10.0))
    except Exception:
        return 10.0


def emit(message: str) -> None:
    """Send one progress line to the panel (never raises)."""
    if _cb is None:
        return
    cb = _cb.get()
    if cb is None:
        return
    try:
        cb("progress", message)
    except Exception:
        log.debug("heartbeat emission failed", exc_info=True)


def sleeping(seconds: float, text=None, cancel=None) -> None:
    """Sleep `seconds`, emitting a progress line roughly every interval.

    `text(remaining_s)` produces the message for the current remaining
    time (e.g. "Model đang quá tải, thử lại sau N giây"). The cancel event
    short-circuits: the wait ends as soon as cancel is set."""
    seconds = max(0.0, float(seconds))
    if not active():
        # No panel to update (tests, CLI runs): one plain sleep so patched
        # time.sleep in tests sees the full duration exactly once.
        if cancel is None or not cancel.is_set():
            time.sleep(seconds)
        return
    step = max(1.0, float(interval()))
    deadline = time.monotonic() + max(0.0, float(seconds))
    emit_step = min(step, max(0.2, max(0.0, float(seconds))))
    last_emit = -1e9
    while True:
        remaining = deadline - time.monotonic()
        if not remaining > 0 or (cancel is not None and cancel.is_set()):
            return
        now = time.monotonic()
        if text is not None and now - last_emit >= emit_step - 1e-6:
            try:
                emit(text(round(max(0.0, remaining))))
            except Exception:
                pass
            last_emit = now
        chunk = min(0.2, max(0.0, remaining))
        time.sleep(chunk)


class waiting:
    """Context manager: emits `text(elapsed_s)` lines every interval while
    the body runs (Update 9 task 3.3 — model calls, encodes without a
    percent). Nested `waiting` blocks in the same thread collapse; only
    the outermost one emits. No-op when no callback is bound."""

    _depth = 0
    _guard = threading.Lock()

    def __init__(self, text=None):
        self.text = text or (lambda elapsed: f"... {int(elapsed)} s")
        self._cb = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = time.monotonic()
        self._outermost = False

    def __enter__(self):
        if not active():
            return self
        self._cb = _cb.get()  # captured: the timer thread has no contextvars
        with waiting._guard:
            waiting._depth += 1
            self._outermost = waiting._depth == 1
        if self._outermost:
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="dubtool-heartbeat"
            )
            self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        with waiting._guard:
            waiting._depth = max(0, waiting._depth - 1)
            outermost = self._outermost
        if outermost and self._thread is not None:
            self._stop.set()
            # Do NOT join: a .join() on the event-loop-adjacent worker
            # thread is fine here (this runs on the worker itself), but a
            # short join keeps slow emitters from stalling the stage.
            self._thread.join(timeout=1.5)
            self._thread = None
        return False

    def _loop(self) -> None:
        step = max(1.0, interval())
        self._started = time.monotonic()
        while not self._stop.wait(timeout=step):
            elapsed = max(0, int(time.monotonic() - self._started))
            if self._cb is None:
                return
            try:
                self._cb("progress", self.text(elapsed))
            except Exception:
                pass
