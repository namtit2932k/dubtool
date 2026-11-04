"""AGENT_UPDATE_11 2.1: parallel unit translation with right-sized,
latency-adaptive chunks.

Chunking by unit count (`config.chunk_size`) made the whole `test2` video
one 16-unit / 3,827-character request — it timed out six times over
18 minutes (AGENT_UPDATE_11 §0.2). Requests are now sized by volume:
`llm.chunk_max_chars` (default 1200 source characters) or
`llm.chunk_max_units` (default 8), whichever is hit first, and the
configured translate concurrency runs the smaller requests in parallel.

The per-chunk cap adapts to measured latency (AGU11 2.1):
- a request that took more than 60% of the per-request timeout halves the
  character cap (never below 400) for the REMAINING chunks — chunks are
  cut fresh from the pending queue each time a worker is free, so a fixed
  chunk list cannot trap the adaptiation;
- three consecutive requests under 20% of the timeout raise the cap by
  25% (never above the configured default).

Rate limiting keeps the AGU4 semantics: a 429 reduces concurrency by one
for the rest of the run (never below 1) and re-queues the chunk; other
failures finish the in-flight chunks and re-raise (resume-safe).
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from dubtool.job import StageCancelled
from dubtool.llm.base import RateLimitError

log = logging.getLogger("dubtool")

MIN_CAP_CHARS = 400
QUICK_STREAK = 3


def adaptive_state(config) -> dict:
    """Fresh adaptive-state dict from config llm (AGU11 2.1/2.2 knobs)."""
    llm_cfg = getattr(config, "llm", {}) or {}
    mode = getattr(config, "llm_mode", None)
    if mode == "fast":
        timeout = float(llm_cfg.get("request_timeout_s_fast", 90.0) or 90.0)
    else:
        timeout = float(llm_cfg.get("request_timeout_s", 180.0) or 180.0)
    return {
        "max_chars": int(llm_cfg.get("chunk_max_chars", 1200) or 1200),
        "cur_chars": int(llm_cfg.get("chunk_max_chars", 1200) or 1200),
        "max_units": int(llm_cfg.get("chunk_max_units", 8) or 8),
        "timeout_s": timeout,
        "quick_streak": 0,
        "latencies": [],
    }


def adapt_after_latency(adaptive: dict, elapsed: float) -> None:
    """AGU11 2.1: feed one completed request latency into the cap policy."""
    adaptive.setdefault("latencies", []).append(round(float(elapsed), 2))
    timeout = float(adaptive.get("timeout_s") or 0)
    if not timeout > 0:
        return
    elapsed = float(elapsed)
    if elapsed > 0.6 * timeout:
        old = int(adaptive["cur_chars"])
        adaptive["cur_chars"] = max(MIN_CAP_CHARS, old // 2)
        adaptive["quick_streak"] = 0
        if adaptive["cur_chars"] != old:
            log.warning(
                "s3: a request took %.0f s (> 60%% of the %.0f s timeout) — "
                "chunk cap halved to %d characters for the remaining chunks",
                elapsed, timeout, adaptive["cur_chars"],
            )
    elif elapsed < 0.2 * timeout:
        adaptive["quick_streak"] = int(adaptive.get("quick_streak", 0)) + 1
        if int(adaptive["quick_streak"]) >= QUICK_STREAK:
            old = int(adaptive["cur_chars"])
            adaptive["cur_chars"] = min(
                int(adaptive["max_chars"]), int(old * 1.25) if old else 1
            )
            adaptive["quick_streak"] = 0
            if adaptive["cur_chars"] != old:
                log.info(
                    "s3: three consecutive fast requests — chunk cap raised to %d characters",
                    adaptive["cur_chars"],
                )


def run_units_parallel(
    units: list[dict],
    translate_chunk: Callable[[list, str], list[dict]],
    apply: Callable[[list[dict]], None] | None = None,
    concurrency: int = 1,
    cancel: threading.Event | None = None,
    adaptive: dict | None = None,
    label_prefix: str = "translate chunk",
) -> None:
    """Translate sentence units in parallel chunks cut from a shared queue.

    Chunks are cut fresh from the remaining units every time a worker is
    free (`llm.chunk_max_chars` / `llm.chunk_max_units`, whichever first),
    so the adaptive cap from earlier requests governs the remaining ones.
    Results are applied as they complete; merging goes per unit id, so
    completion order does not matter. `cancel` is checked between chunks
    and while waiting on a rate-limit hint.
    """
    import time as _time

    if adaptive is None:
        adaptive = {
            "max_chars": 1200, "cur_chars": 1200, "max_units": 8,
            "timeout_s": 0.0, "quick_streak": 0, "latencies": [],
        }

    def _check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise StageCancelled("s3: cancelled by user")

    pending_units: list = list(units)
    lock = threading.Lock()
    label_no = [0]
    workers = max(1, int(concurrency))
    first_error: Exception | None = None

    def cut_chunk() -> list | None:
        # Caller holds the lock.
        cur: list = []
        cur_chars = 0
        while pending_units:
            unit = pending_units[0]
            size = len(str(unit.get("text") or ""))
            if cur and (
                cur_chars + size > int(adaptive["cur_chars"])
                or len(cur) >= int(adaptive["max_units"])
            ):
                break
            pending_units.pop(0)
            cur.append(unit)
            cur_chars += size
        return cur or None

    pool = ThreadPoolExecutor(max_workers=workers)
    active: dict = {}  # future -> (chunk, label, started)
    try:
        while True:
            _check_cancel()
            with lock:
                while len(active) < workers:
                    chunk = cut_chunk()
                    if chunk is None:
                        break
                    label_no[0] += 1
                    total_chars = sum(len(str(u.get("text") or "")) for u in chunk)
                    label = (
                        f"{label_prefix} {label_no[0]} "
                        f"({len(chunk)} units, {total_chars} chars)"
                    )
                    active[pool.submit(translate_chunk, chunk, label)] = (
                        chunk, label, _time.monotonic(),
                    )
            if not active:
                break
            done, _ = wait(active, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done:
                chunk, label, started = active.pop(future)
                try:
                    updates = future.result()
                except RateLimitError as exc:
                    with lock:
                        pending_units = list(chunk) + pending_units
                    workers = max(1, workers - 1)
                    pool._max_workers = workers  # free workers stop early
                    hint = float(getattr(exc, "retry_after", 0) or 0)
                    remaining = max(0.0, min(hint, 300.0))
                    log.warning(
                        "s3: HTTP 429 — reducing translation concurrency to %d "
                        "for the rest of the run; the chunk was re-queued",
                        workers,
                    )
                    while remaining > 0:
                        _check_cancel()
                        step = min(1.0, remaining)
                        _time.sleep(step)
                        remaining -= step
                    continue
                except Exception as exc:  # noqa: BLE001  (re-raised below)
                    if first_error is None:
                        first_error = exc
                    log.warning("s3: translation chunk failed: %s", exc)
                    continue
                if apply is not None:
                    apply(updates)
                elapsed = _time.monotonic() - started
                log.info("s3: %s done in %.1f s", label, elapsed)
                adapt_after_latency(adaptive, elapsed)
    finally:
        pool.shutdown(wait=True)  # in-flight chunks finish (resume-safe)
    if pending_units:
        raise RuntimeError(
            f"s3: {len(pending_units)} unit(s) did not complete after repeated rate limits"
        )
    if first_error is not None:
        raise first_error
