"""Edge-tts provider (default, unofficial — kept behind TTSProvider so it can
be swapped later). Retries transient errors up to 5 times with exponential
backoff and rate-limits between requests."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path

log = logging.getLogger("dubtool")

from dubtool.tts.base import TTSFailure  # noqa: E402


class EdgeTTSProvider:
    def __init__(
        self, retries: int = 5, backoff_base_s: float = 3.0, rate_limit_s: float = 0.3,
        rate: str = "+0%", max_cooldown_s: float = 30.0,
    ):
        self.retries = retries
        self.backoff_base_s = backoff_base_s
        self.rate_limit_s = rate_limit_s
        self.rate = rate  # edge-tts speaking-rate parameter, e.g. "+0%"
        # AGU8 2.1: the cooldown cap is configurable (config tts.max_cooldown_s,
        # default raised from 30 s to 120 s).
        self.max_cooldown_s = max_cooldown_s
        # Adaptive cooldown: the service occasionally throttles an IP for over
        # a minute, so after failures we slow down across calls, not just
        # inside one synthesize() retry loop.
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
        if not text or not text.strip():
            raise TTSFailure("refusing to synthesize empty text")
        last_error: Exception | None = None
        self._wait_cooldown()
        for attempt in range(1, self.retries + 1):
            try:
                self._synthesize_once(text.strip(), voice, out_path, self.rate)
                self._validate(out_path)
                self._consecutive_failures = 0
                time.sleep(self.rate_limit_s)  # simple rate limiting
                return
            except Exception as exc:  # transient network/service errors
                last_error = exc
                log.warning(
                    "edge-tts attempt %d/%d failed: %s", attempt, self.retries, exc
                )
                self._cool_down()
                if attempt < self.retries:
                    # Exponential backoff with jitter; the service occasionally
                    # throttles for over a minute, so keep the window wide.
                    delay = self.backoff_base_s * (2 ** (attempt - 1))
                    backoff = delay * (0.8 + 0.4 * random.random())
                    remaining = self._cooldown_until - time.monotonic()
                    time.sleep(max(backoff, remaining))
        raise TTSFailure(
            f"edge-tts failed after {self.retries} attempts: {last_error}"
        ) from last_error

    def _synthesize_once(self, text: str, voice: str, out_path: Path, rate: str = "+0%") -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        save_sync = getattr(communicate, "save_sync", None)
        if callable(save_sync):
            save_sync(str(out_path))
        else:
            asyncio.run(communicate.save(str(out_path)))

    def _validate(self, out_path: Path) -> None:
        if not out_path.exists() or out_path.stat().st_size < 200:
            raise TTSFailure(f"TTS produced no usable audio at {out_path}")


import edge_tts  # noqa: E402  (kept at bottom so importing this module stays cheap in tests)
