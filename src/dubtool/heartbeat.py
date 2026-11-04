"""Stage heartbeats (Update 9 task 3.3; AGU11 2.4).

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

AGENT_UPDATE_11 2.4: the binding is now a plain module-level value instead
of a ContextVar. The old storage kept the emitter only in the thread that
called bind() — every ThreadPoolExecutor worker (translation chunks run
with concurrency 3 in Accurate mode) saw the default None and the panel
received nothing during model calls and their backoffs. That is the
missing nine minutes of the user's 2026-11-01 run (13:26:10–13:35:18 in
log.txt). The callback is still one per run: `bind` overwrites and
`reset` clears it; api.run_dub resets in its finally block.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("dubtool")

_lock = threading.Lock()
_cb = {"cb": None}  # plain shared storage: visible from every thread

_KEY = "heartbeat_s"


def bind(cb) -> object:
    """Bind the per-run emission callback (overwrites any previous one);
    returns a reset token for `reset()`."""
    with _lock:
        _cb["cb"] = cb
    return object()


def reset(token) -> None:
    """Clear the binding (matches the docstring in heartbeat 1.0)."""
    if token is not None:
        with _lock:
            _cb["cb"] = None


def active() -> bool:
    return _cb["cb"] is not None


def interval() -> float:
    """"Heartbeat period from settings (`heartbeat_s`, default 10 s)."""
    try:
        from dubtool.app import settings as settings_mod

        return max(1.0, float(settings_mod.load().get(_KEY, 10.0) or 10.0))
    except Exception:
        return 10.0


def emit(message: str) -> None:
    """Send one progress line to the panel (never raises)."""
    with _lock:
        cb = _cb["cb"]
    if cb is None:
        return
    try:
        cb("progress", message)
    except Exception:
        log.debug("heartbeat emission failed", exc_info=True)


def sleeping(seconds: float, text=None, cancel=None) -> float:
    """Sleep `seconds`, emitting a progress line roughly every interval.

    `text(remaining_s)` produces the message for the current remaining
    time (e.g. "Model đang quá tải, thử lại sau N giây"). The cancel event
    short-circuits: the wait ends as soon as cancel is set.

    AGENT_UPDATE_11 7.1: returns the actual slept seconds so the llm/tts
    layers can feed the stage budget with the real waiting time.
    """
    seconds = max(0.0, float(seconds))
    started = time.monotonic()
    if not active():
        # No panel to update (tests, CLI runs): one plain sleep so patched
        # time.sleep in tests sees the full duration exactly once.
        if cancel is None or not cancel.is_set():
            time.sleep(seconds)
        return time.monotonic() - started
    step = max(1.0, float(interval()))
    deadline = time.monotonic() + max(0.0, float(seconds))
    emit_step = min(step, max(0.2, max(0.0, float(seconds))))
    last_emit = -1e9
    while True:
        remaining = deadline - time.monotonic()
        if not remaining > 0 or (cancel is not None and cancel.is_set()):
            return time.monotonic() - started
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
    the outermost one emits. No-op when no callback is bound. AGU11 2.4:
    the emission thread is global across the worker threads."""

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
            with _lock:
                self._cb = _cb["cb"]
        else:
            with _lock:
                self._cb = _cb["cb"]
        with waiting._guard:
            waiting._depth += 1
            self._outermost = waiting._depth == 1
        if self._outermost and self._cb is not None:
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
