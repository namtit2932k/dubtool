"""Edge-tts provider (default, unofficial — kept behind TTSProvider so it can
be swapped later).

AGENT_UPDATE_11 3.1: every edge-tts request runs under a hard timeout
(`tts.request_timeout_s`, seconds, counted over connect + send + receive)
and surfaces errors immediately. This module performs exactly ONE network
attempt per synthesize() call — the repair ladder in the TTS stage owns
retries, sentence splitting and longer waits.

Speed optimizations for CPU-light & multi-worker execution:
- A shared persistent background asyncio loop thread avoids rebuilding a new
  Windows ProactorEventLoop/selector per sentence (~150-250 ms saved/clip).
- A reusable aiohttp connector (`ttl_dns_cache=300`) eliminates repeated DNS
  lookups and allows multiple worker threads to synthesize concurrently on one
  event loop while closing cleanly on provider teardown.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import inspect
import logging
import threading
import time
import weakref
from pathlib import Path

log = logging.getLogger("dubtool")

from dubtool.tts.base import TTSFailure  # noqa: E402


def _make_reusable_connector():
    """Build an aiohttp TCPConnector that stays open across Communicate calls."""
    import aiohttp

    class _ReusableTCPConnector(aiohttp.TCPConnector):
        async def close(self, *args, **kwargs) -> None:
            # Communicate's internal ClientSession calls connector.close() on exit;
            # keep DNS cache & pool alive until EdgeTTSProvider.close() runs.
            return None

        async def force_close(self) -> None:
            await super().close()

    return _ReusableTCPConnector(ttl_dns_cache=300, limit=16)


class EdgeTTSProvider:
    supports_concurrent = True

    def __init__(
        self,
        request_timeout_s: float = 45.0,
        rate_limit_s: float = 0.0,
        rate: str = "+0%",
        max_cooldown_s: float = 30.0,
        cancel: threading.Event | None = None,
    ):
        # AGU11 3.1: hard cap for one network request (connect+send+receive).
        self.request_timeout_s = float(request_timeout_s)
        self.rate_limit_s = float(rate_limit_s)
        self.rate = rate  # edge-tts speaking-rate parameter, e.g. "+0%"
        # AGU8 2.1: the cooldown cap is configurable (config tts.max_cooldown_s,
        # default raised from 30 s to 120 s).
        self.max_cooldown_s = float(max_cooldown_s)
        self.cancel = cancel
        # Pacing only: after failures the provider cools down before the NEXT
        # request (the stage decides what that next request is).
        self._state_lock = threading.Lock()
        self._cooldown_until = 0.0
        self._consecutive_failures = 0
        self._loop_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._connector = None
        weak_self = weakref.ref(self)
        self._atexit_cb = lambda ref=weak_self: (obj := ref()) and obj.close()
        atexit.register(self._atexit_cb)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)

                def _init_connector() -> None:
                    try:
                        # Create inside the running loop so get_running_loop() works.
                        self._connector = _make_reusable_connector()
                    except Exception:
                        self._connector = None
                    finally:
                        ready.set()

                loop.call_soon(_init_connector)
                try:
                    loop.run_forever()
                finally:
                    ready.set()
                    try:
                        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                        for task in pending:
                            task.cancel()
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        if self._connector is not None:
                            loop.run_until_complete(self._connector.force_close())
                    except Exception:
                        pass
                    self._connector = None
                    loop.close()

            thread = threading.Thread(target=_run_loop, name="edge-tts-loop", daemon=True)
            thread.start()
            ready.wait(timeout=5.0)
            self._loop = loop
            self._loop_thread = thread
            return loop

    def close(self) -> None:
        cb = getattr(self, "_atexit_cb", None)
        if cb is not None:
            self._atexit_cb = None
            try:
                atexit.unregister(cb)
            except Exception:
                pass
        with self._loop_lock:
            loop = self._loop
            thread = self._loop_thread
            self._loop = None
            self._loop_thread = None
        if loop is not None:
            try:
                if loop.is_running():
                    loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

    def _wait_cooldown(self) -> None:
        with self._state_lock:
            until = self._cooldown_until
        now = time.monotonic()
        if now < until:
            log.info("edge-tts: waiting %.0fs (service cooldown)", until - now)
            time.sleep(until - now)

    def _cool_down(self) -> None:
        with self._state_lock:
            self._consecutive_failures += 1
            if self.max_cooldown_s <= 0:
                self._cooldown_until = 0.0
                return
            cooldown = min(8.0 * self._consecutive_failures, self.max_cooldown_s)
            self._cooldown_until = time.monotonic() + cooldown

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        """One network attempt under a hard cap (AGU11 3.1)."""
        if not text or not text.strip():
            raise TTSFailure("refusing to synthesize empty text")
        if self.cancel is not None and self.cancel.is_set():
            raise TTSFailure("edge-tts cancelled")
        self._wait_cooldown()
        try:
            self._synthesize_once(text.strip(), voice, out_path, self.rate)
            self._validate(out_path)
            with self._state_lock:
                self._consecutive_failures = 0
            if self.rate_limit_s > 0:
                time.sleep(self.rate_limit_s)
        except Exception as exc:
            # AGU11 3.1: surface immediately; the repair ladder in s4_tts
            # decides the next step (wait / split / ...).
            self._cool_down()
            raise TTSFailure(self._message(exc)) from exc

    @staticmethod
    def _message(exc: Exception) -> str:
        if isinstance(exc, TTSFailure):
            return str(exc)
        if isinstance(exc, asyncio.TimeoutError):
            # AGU11 3.1: a hanging request is cut and reported as a timeout.
            return "edge-tts timeout (hard cap reached)"
        return f"edge-tts request failed: {exc}"

    def _synthesize_once(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        loop = self._ensure_loop()
        kwargs: dict = {"rate": rate}
        try:
            params = inspect.signature(edge_tts.Communicate.__init__).parameters
            if "connector" in params and self._connector is not None:
                kwargs["connector"] = self._connector
        except (TypeError, ValueError):
            pass
        communicate = edge_tts.Communicate(text, voice, **kwargs)
        # AGU11 3.1: the hard cap is OUR asyncio.wait_for over the whole
        # request (connect+send+receive). edge-tts's own timeout kwarg is a
        # per-receive timeout and would not bound a hung handshake.
        self._run_bounded(communicate.save(str(out_path)), loop=loop)

    def _run_bounded(self, coro, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Run an edge-tts coroutine under asyncio.wait_for on the persistent
        background event loop thread."""
        target_loop = loop or self._ensure_loop()

        async def _runner():
            return await asyncio.wait_for(coro, timeout=self.request_timeout_s)

        future = asyncio.run_coroutine_threadsafe(_runner(), target_loop)
        while True:
            if self.cancel is not None and self.cancel.is_set():
                future.cancel()
                raise TTSFailure("edge-tts cancelled")
            # Use concurrent.futures.wait rather than catching TimeoutError from
            # future.result(): in Python 3.11+, asyncio.TimeoutError and
            # concurrent.futures.TimeoutError both alias builtins.TimeoutError.
            done, _ = concurrent.futures.wait(
                (future,), timeout=0.25 if self.cancel is not None else None
            )
            if done:
                future.result()
                return

    def _validate(self, out_path: Path) -> None:
        if not out_path.exists() or out_path.stat().st_size < 200:
            raise TTSFailure(f"TTS produced no usable audio at {out_path}")


import edge_tts  # noqa: E402  (kept at bottom so importing this module stays cheap in tests)
