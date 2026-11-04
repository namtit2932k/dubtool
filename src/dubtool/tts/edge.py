"""Edge-tts provider (default, unofficial — kept behind TTSProvider so it can
be swapped later).

AGENT_UPDATE_11 3.1: every edge-tts request runs under a hard timeout
(`tts.request_timeout_s`, seconds, counted over connect + send + receive)
and surfaces errors immediately. This module performs exactly ONE network
attempt per synthesize() call — the repair ladder in the TTS stage owns
retries, sentence splitting and longer waits. The former 5-attempt
exponential-backoff ladder lived here and could keep a refused segment
busy for ~80 seconds on its own; that ladder is gone.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

log = logging.getLogger("dubtool")

from dubtool.tts.base import TTSFailure  # noqa: E402


class EdgeTTSProvider:
    def __init__(
        self,
        request_timeout_s: float = 45.0,
        rate_limit_s: float = 0.3,
        rate: str = "+0%",
        max_cooldown_s: float = 30.0,
    ):
        # AGU11 3.1: hard cap for one network request (connect+send+receive).
        self.request_timeout_s = float(request_timeout_s)
        self.rate_limit_s = rate_limit_s
        self.rate = rate  # edge-tts speaking-rate parameter, e.g. "+0%"
        # AGU8 2.1: the cooldown cap is configurable (config tts.max_cooldown_s,
        # default raised from 30 s to 120 s).
        self.max_cooldown_s = max_cooldown_s
        # Pacing only: after failures the provider cools down before the NEXT
        # request (the stage decides what that next request is).
        self._cooldown_until = 0.0
        self._consecutive_failures = 0

    def _wait_cooldown(self) -> None:
        now = time.monotonic()
        if now < self._cooldown_until:
            log.info("edge-tts: waiting %.0fs (service cooldown)", self._cooldown_until - now)
            time.sleep(self._cooldown_until - now)

    def _cool_down(self) -> None:
        self._consecutive_failures += 1
        cooldown = min(8.0 * self._consecutive_failures, self.max_cooldown_s)
        self._cooldown_until = time.monotonic() + cooldown

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        """One network attempt under a hard cap (AGU11 3.1)."""
        if not text or not text.strip():
            raise TTSFailure("refusing to synthesize empty text")
        self._wait_cooldown()
        try:
            self._synthesize_once(text.strip(), voice, out_path, self.rate)
            self._validate(out_path)
            self._consecutive_failures = 0
            time.sleep(self.rate_limit_s)  # simple rate limiting
        except Exception as exc:
            # AGU11 3.1: surface immediately; the repair ladder in s4_tts
            # decides the next step (wait / split / alternate voice / ...).
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
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        # AGU11 3.1: the hard cap is OUR asyncio.wait_for over the whole
        # request (connect+send+receive). edge-tts's own timeout kwarg is a
        # per-receive timeout and would not bound a hung handshake.
        self._run_bounded(communicate.save(str(out_path)))

    def _run_bounded(self, coro) -> None:
        """Run an edge-tts coroutine under asyncio.wait_for with a hard cap
        measured on the running loop clock (AGU11 3.1)."""
        async def _runner():
            return await asyncio.wait_for(coro, timeout=self.request_timeout_s)

        asyncio.run(_runner())

    def _validate(self, out_path: Path) -> None:
        if not out_path.exists() or out_path.stat().st_size < 200:
            raise TTSFailure(f"TTS produced no usable audio at {out_path}")


import edge_tts  # noqa: E402  (kept at bottom so importing this module stays cheap in tests)
