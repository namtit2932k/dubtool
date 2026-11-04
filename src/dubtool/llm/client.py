"""Shared wrapper around any LLMProvider used by the pipeline stages.

Responsibilities (AGENT_UPDATE.md 3.1; Update 12 task 2):
- the Update 12 error taxonomy decides the action: a 503/500/502 (or any
  model-wide server error) is capacity — the SAME request is retried with
  the bounded `llm.backoff_s` schedule [5, 15, 45, 90] (never split);
  a 504 DEADLINE or a client timeout (or a 400-size limit) is a request-
  size/latency signal — it splits once into two halves (only when the
  chunk is larger than `split_min_chars`/`split_min_units`, and halves
  are never split again); a 429 per-minute pauses every request to the
  model until the retry delay; a 429 per-day with no key left goes
  straight to the fallback;
- the model circuit breaker: `breaker_failures` consecutive failed
  requests open it for `breaker_cooldown_s`; when it has opened
  `fallback_after_opens` times, or the per-day quota is gone for every
  key, or the stage waited past `llm.stage_budget_s` on failing
  requests, the remaining chunks go to the Fast model through the
  existing overload callback;
- parsing/validation with the error appended to the prompt on retry;
- logs provider name, model, and token counts.

One LLMClient object is shared by the parallel chunk workers of a stage;
the breaker, the rate pause and the stage-wait accumulator are all
thread-safe instance/shared state on it.
"""

from __future__ import annotations

import logging
import inspect
import time
from typing import Callable

from dubtool.llm.base import (
    InternalLLMError,
    InvalidKeyError,
    KeyExhaustedError,
    LLMError,
    LLMProvider,
    NetworkLLMError,
    RateLimitError,
    RateLimitExhausted,
    ServerErrorLLMError,
    OverloadFallbackFast,
    SplitRequiredError,
    TimeoutLLMError,
)
from dubtool import heartbeat
from dubtool.llm import budget as budget_mod
from dubtool.llm import parsing
from dubtool.llm import prompts
from dubtool.llm.prompts import (
    render_shorten_batch_user,
    render_shorten_user,
    render_translate_user,
)

log = logging.getLogger("dubtool")

MAX_RETRIES = 3
MAX_VALIDATION_ATTEMPTS = 2   # AGU8 3.1: reply-invalid (parse) attempts only
BACKOFF_BASE_S = 2.0
RATE_LIMIT_BASE_WAIT_S = 30.0
DEFAULT_SERVER_BACKOFF_S = [10.0, 30.0, 90.0]
DEFAULT_STAGE_BUDGET_S = 600.0


def _waiting_text(elapsed: int) -> str:
    """Update 9 task 3.3: the waiting line while a model call is
    outstanding — resolved against the active UI language at emission."""
    try:
        from dubtool.app import i18n

        return i18n.tr("hb.model_wait", n=int(elapsed))
    except Exception:
        return f"waiting for the model ({elapsed} s)"


def _backoff_text(seconds: int) -> str:
    try:
        from dubtool.app import i18n

        return i18n.tr("hb.model_backoff", n=int(seconds))
    except Exception:
        return f"model busy — retrying in {seconds} s"


def _panel(key: str, **kwargs) -> None:
    """One immediate progress line resolved in the active UI language
    (AGENT_UPDATE_11 2.4 — every 503/504/backoff is visible from the
    moment it happens, never only in pipeline.log)."""
    try:
        from dubtool.app import i18n

        heartbeat.emit(i18n.tr(key, **kwargs))
    except Exception:
        pass


class LLMClient:
    def __init__(self, provider: LLMProvider, on_rate_limit: Callable[[float], None] | None = None,
                 cancel=None, options: dict | None = None,
                 on_overload: Callable[..., str] | None = None):
        """on_rate_limit(seconds) is called ONLY when every key is limited and
        the wrapper has to wait (AGENT_UPDATE_5.md 2.4): a caller running
        chunks in parallel can back off and reduce concurrency. A successful
        key switch never triggers it. `cancel` reaches the pool's waits via
        provider.wait_for_key().

        `options` carries the AGU8/AGU11 retry knobs read from config llm:
        server_error_backoff_s, overload_after, overload_policy,
        split_on_timeout, split_min_units, stage_budget_s and the shared
        "stage_wait_state" accumulator. `on_overload` is the
        AGU8 3.4 / AGU11 2.3 overload-policy callback: it receives an info
        dict (model, consecutive failures, waited seconds) and returns
        "wait" (keep backing off), "fallback_fast" (the caller raises
        OverloadFallbackFast itself), or raises (e.g. StageCancelled).
        """
        self.provider = provider
        self.on_rate_limit = on_rate_limit
        self.cancel = cancel
        self.options = options or {}
        self.on_overload = on_overload
        # AGU11 2.3: the callback may take the info dict or nothing (tests,
        # simple policies) — decide once from its signature.
        self._on_overload_takes_info = False
        if callable(on_overload):
            try:
                params = inspect.signature(on_overload).parameters
                self._on_overload_takes_info = any(
                    p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY, p.KEYWORD_ONLY)
                    or p.kind is p.VAR_POSITIONAL
                    for p in params.values()
                )
            except (TypeError, ValueError):
                self._on_overload_takes_info = False
        # Key positions that failed — keypool logging only, never values.
        self.key_switches = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
        self.consecutive_server_errors = 0
        # AGU11 2.3: consecutive failures since the last policy consult —
        # 'ask' is asked again every time this reaches overload_after, not
        # once per run as before.
        self.consecutive_since_consult = 0
        # AGU11 2.2: the payload count the stage sets per chunk (split
        # eligibility); None = unknown/never split.
        self.request_units: int | None = None
        # Update 12: source characters of the current chunk (split
        # eligibility for the character cap).
        self.request_chars: int | None = None
        # Update 12 task 1: the bounded backoff schedule for capacity
        # errors (503/500/502): llm.backoff_s, at most 4 attempts.
        self.backoff_s = self._read_backoff()
        # The model circuit breaker (task 2.2), one per model.
        from dubtool.llm.breaker import ModelBreaker
        self.breaker = ModelBreaker(
            failures=int(self.options.get("breaker_failures", 3) or 3),
            cooldown_s=float(self.options.get("breaker_cooldown_s", 60.0) or 60.0),
        )
        self.fallback_after_opens = max(
            1, int(self.options.get("fallback_after_opens", 2) or 2)
        )
        # Per-minute 429: EVERY request to this model pauses until then
        # (a global pause, not a per-request sleep).
        self.rate_pause_until = 0.0
        self.min_interval_s = self._read_min_interval()
        # AGU11 2.3: one shared wait accumulator per stage (passed via
        # options so the Fast-model rebuild shares the same stage budget).
        self.stage_wait = self.options.get("stage_wait_state") or {"waited_s": 0.0}
        self.stage_budget_s = float(
            self.options.get("stage_budget_s", DEFAULT_STAGE_BUDGET_S)
        )
        # Labels from the stage ("translate chunk 3/4, 20 units, 2431 chars")
        # are attached per call and echoed on every request log line.
        self.request_label = ""
        self.last_request_elapsed_s: float | None = None
        # The per-run request budget is captured at construction (the stage's
        # main thread has the bound context; worker threads do not).
        self._budget = budget_mod.current()

    # ------------------------------------------------------------ internals
    def _read_backoff(self) -> list[float]:
        """The bounded 503/504 backoff schedule: llm.backoff_s first, the
        legacy llm.server_error_backoff_s second.
        """
        raw = (
            self.options.get("backoff_s")
            or self.options.get("server_error_backoff_s")
            or DEFAULT_SERVER_BACKOFF_S
        )
        if raw is DEFAULT_SERVER_BACKOFF_S:
            return list(DEFAULT_SERVER_BACKOFF_S)
        try:
            return [float(v) for v in raw] or list(DEFAULT_SERVER_BACKOFF_S)
        except (TypeError, ValueError):
            return list(DEFAULT_SERVER_BACKOFF_S)

    def _read_min_interval(self) -> float:
        """Token-bucket interval from llm.max_rpm (default: unset).
        """
        rpm = self.options.get("max_rpm") or 0
        try:
            rpm = float(rpm)
        except (TypeError, ValueError):
            rpm = 0.0
        return 60.0 / rpm if rpm > 0 else 0.0

    def _rate_gate(self) -> None:
        """Wait out the model rate pause (per-minute 429) and the token
        bucket (llm.max_rpm) BEFORE a request is sent; both waits are
        visible and count against the stage budget."""
        import time as _time

        wait_s = max(0.0, self.rate_pause_until - _time.monotonic())
        if wait_s > 0:
            def pause_text(remaining: float) -> str:
                return _panel_text_rate_pause(int(remaining + 0.999))

            slept = heartbeat.sleeping(wait_s, pause_text, self.cancel)
            self.stage_wait["waited_s"] = float(
                self.stage_wait.get("waited_s", 0.0)) + max(0.0, float(slept))
            self.rate_pause_until = _time.monotonic()
        elif self.min_interval_s > 0:
            heartbeat.sleeping(
                self.min_interval_s, lambda _r: "", self.cancel,
            )

    def _split_eligible(self) -> bool:
        """Update 12 2.1: a chunk may split only when it is larger than
        `llm.split_min_chars` (600) or `llm.split_min_units` (4 units).
        """
        units = self.request_units
        if units is None:
            return False
        min_units = max(1, int(self.options.get("split_min_units", 4) or 4))
        min_chars = max(100, int(self.options.get("split_min_chars", 600) or 600))
        chars = self.request_chars or 0
        return units > min_units or chars > min_chars

    def _server_backoff_s(self, attempt: int) -> float:
        """AGU8 3.2: exponential-with-jitter delay (s) after the `attempt`-th
        consecutive server error (1-based), from llm.server_error_backoff_s."""
        import random

        delays = [float(v) for v in self.options.get("server_error_backoff_s") or DEFAULT_SERVER_BACKOFF_S]
        base = delays[min(max(attempt - 1, 0), len(delays) - 1)]
        return max(0.1, base * (0.8 + 0.4 * random.random()))

    def _over_budget(self) -> bool:
        return self.stage_wait.get("waited_s", 0.0) >= self.stage_budget_s

    def _announce_budget(self) -> None:
        _panel("llm.stage_budget_exceeded", n=int(self.stage_wait.get("waited_s", 0.0)))

    def _handle_overload(self, *, quota_gone: bool = False) -> str:
        """Update 12 task 2: the overload policy consult.

        The fallback triggers (2.4) are: the breaker has opened
        `fallback_after_opens` times, the per-day quota is gone for every
        key, or the stage has waited past `llm.stage_budget_s` on failing
        requests. With the dialog policy ('ask') the dialog is consulted
        again after every `overload_after` consecutive failures (and at the
        budget), receiving the info dict. 'wait' keeps backing off (only
        the budget then switches it). The callback may raise — it
        propagates untouched. With no callback, 'ask' behaves like 'wait'.
        """
        overload_after = max(1, int(self.options.get("overload_after") or 2))
        opens_ok = self.breaker.opens >= self.fallback_after_opens
        over_budget = self._over_budget()
        must_consult = (
            (self.consecutive_since_consult >= overload_after)
            or over_budget or quota_gone or opens_ok
        )
        if not must_consult:
            return "wait"
        if over_budget and self.stage_wait.get("announced_at", -1.0) != self.stage_wait.get("waited_s", 0.0):
            self._announce_budget()
            self.stage_wait["announced_at"] = self.stage_wait.get("waited_s", 0.0)
        self.consecutive_since_consult = 0
        self.stage_wait["consults"] = int(self.stage_wait.get("consults", 0)) + 1
        self.stage_wait["budget_consulted_at"] = self.stage_wait.get("waited_s", 0.0)
        default_policy = str(self.options.get("overload_policy") or "ask").lower()
        info = {
            "model": self.provider.model,
            "consecutive": self.consecutive_server_errors,
            "waited_s": round(float(self.stage_wait.get("waited_s", 0.0)), 1),
            "over_budget": over_budget,
            "breaker_opens": int(self.breaker.opens),
            "quota_gone": bool(quota_gone),
        }
        if self.on_overload is None:
            action = None
        elif self._on_overload_takes_info:
            action = self.on_overload(info)  # may raise (dialog Cancel)
        else:
            action = self.on_overload()  # may raise (dialog Cancel)
        policy = str(action or default_policy).lower()
        if policy in ("fallback_fast", "fallback-fast"):
            raise OverloadFallbackFast(self.provider.model)
        if policy == "wait" and (quota_gone or over_budget):
            # 'wait' cannot wait out a daily quota or an empty budget:
            # fail the stage with the clear reason instead of waiting hours.
            raise LLMError(
                f"model {self.provider.model} is unavailable "
                + ("(daily quota used up for every key)" if quota_gone
                   else f"(stage wait budget {self.stage_budget_s:.0f} s spent)")
                + "; stages are resumable — re-run the same command"
            )
        return "wait"

    def _maybe_split(self, exc: Exception) -> None:
        """Update 12 2.1: ONLY a latency or request-size signal splits —
        504 DEADLINE, a client timeout, or a 400 for size (a reply cut by
        MAX_TOKENS). A 503 (capacity) never splits. The split happens once,
        into two halves, and only when the chunk is larger than
        `split_min_chars`/`split_min_units` (small chunks and halves are
        never split)."""
        if not bool(self.options.get("split_on_timeout", False)):
            return
        status = getattr(exc, "status", None)
        is_timeout = isinstance(exc, TimeoutLLMError) or status == 504
        is_size = status == 400
        if not (is_timeout or is_size):
            return
        if not self._split_eligible():
            return
        units = self.request_units or 0
        _panel(
            "llm.chunk_split",
            n=units,
            err=str(status or exc),
        )
        raise SplitRequiredError(
            f"chunk of {units} units failed ({status or 'client timeout'}) — "
            "splitting in two once instead of resending it",
            status=status,
        ) from exc

    def _process_server_error(self, exc: Exception, attempt: int,
                              max_attempts: int) -> float | None:
        """AGU8 3.2 + Update 12 task 2: one server failure (503/504/timeout).

        Logs the plain explanation into pipeline.log AND the details panel,
        counts the failed request against the model circuit breaker, then
        consults the overload policy (the fallback triggers: enough breaker
        opens, the stage budget spent, the quota gone), then raises
        SplitRequiredError — only for a latency (504/timeout) or request-
        size (400) failure of a chunk large enough to split. Returns the
        backoff seconds for the caller to sleep (None when the caller
        should give up because attempts are exhausted).
        """
        import time as _time

        self.consecutive_server_errors += 1
        self.consecutive_since_consult += 1
        status = getattr(exc, "status", None)
        if status == 503:
            _panel("llm.server_503", model=self.provider.model)
        elif status == 504:
            _panel("llm.server_504", model=self.provider.model)
        elif isinstance(exc, TimeoutLLMError):
            _panel("llm.server_timeout", model=self.provider.model)
        else:
            _panel("llm.server_error", model=self.provider.model, status=status or "?")
        log.warning(
            "llm %s/%s server error %s (attempt %d/%d, %s): %s — model-wide: no key rotation",
            self.provider.name, self.provider.model,
            status if status is not None else getattr(exc, "name", "?"), attempt, max_attempts,
            self.request_label or "", exc,
        )
        opened = self.breaker.record_failure(_time.monotonic())
        self._handle_overload()  # raises OverloadFallbackFast per the triggers
        if opened:
            _panel(
                "llm.breaker_open",
                n=int(self.breaker.cooldown_s),
                opens=int(self.breaker.opens),
            )
        self._maybe_split(exc)   # raises SplitRequiredError for eligible latency/size chunks
        return self._server_backoff_s(attempt)

    def _sleep_backoff(self, seconds: float) -> None:
        """Wait out one server-error backoff, feeding the shared stage-wait
        accumulator (AGU11 2.3) and emitting the countdown from sleeping()."""
        slept = heartbeat.sleeping(seconds, _backoff_text, self.cancel)
        self.stage_wait["waited_s"] = float(self.stage_wait.get("waited_s", 0.0)) + max(0.0, float(slept))

    def _request_started_log(self, kind: str, attempt: int = 1) -> None:
        """AGU8 3.5 + AGU11 2.4: name the model, the request kind, its size
        and the attempt, in the log AND in the details panel from the
        moment the call starts."""
        label = self.request_label or kind
        # The per-mode timeout lives on the provider (llm/__init__.py get_provider).
        timeout = float(getattr(self.provider, "timeout_s", 0) or 0)
        tail = f" — đang chờ phản hồi (timeout {timeout:.0f} s)" if timeout else ""
        message = f"llm {self.provider.name}/{self.provider.model} — {label} (lần {attempt}){tail}"
        log.info(message)
        _panel("llm.request_start", model=self.provider.model, label=label, n=attempt)

    def _call_logged(self, provider_call, *args, **kwargs):
        """Wrap one provider.complete() with the elapsed-time log line."""
        started = time.monotonic()
        text_result = provider_call(*args, **kwargs)
        self.last_request_elapsed_s = round(time.monotonic() - started, 2)
        return text_result

    def _consume_budget(self) -> None:
        """One slot per actual request attempt (retries count too). Raises
        BudgetExhausted BEFORE any network call when the run is out."""
        budget = self._budget
        if budget is not None and not budget.try_consume():
            log.warning(
                "llm: request budget used up (%d requests) — stopping LLM calls "
                "for this run", budget.limit
            )
            raise budget_mod.BudgetExhausted(
                f"LLM request budget used up ({budget.limit} requests this run)"
            )
    def _pool_capable(self) -> bool:
        """True for providers that own a KeyPool (gemini with api_keys) and
        take priority over the generic-ish wait/rotate fallback below."""
        return hasattr(self.provider, "handle_rate_limit") and hasattr(self.provider, "pool")
    def _call(self, system: str, user: str, *, json_mode: bool = True, max_tokens: int = 8000):
        """Raw call with transient-error and rate-limit handling.

        Pool-capable providers (gemini + KeyPool): a 429 or an invalid key
        switches keys per the Update 12 taxonomy — a per-minute rate limit
        pauses the whole model instead of rotating, and a daily quota with
        no key left goes straight to the fallback. Only when keys remain
        limited does the wrapper wait (honoring the cancel event).
        """
        if self._pool_capable():
            return self._call_with_pool(system, user, json_mode=json_mode, max_tokens=max_tokens)
        # (the non-pool loop below re-raises InternalLLMError directly in the
        # same way — see the except clauses)
        import time as _time

        last_error: Exception | None = None
        attempt_no = 0
        cap_attempts = 0          # 503/500/502/504/timeouts: at most len(backoff_s)
        max_cap_attempts = max(1, min(4, len(self.backoff_s)))
        gen_attempts = 0          # connection/misc failures
        pauses_left = 3           # per-minute pause occurrences per request
        first_attempt = True
        while True:
            self._rate_gate()     # per-minute model pause + token bucket
            if first_attempt:
                first_attempt = False
            try:
                self._consume_budget()
                attempt_no += 1
                self._request_started_log("complete", attempt_no)

                with heartbeat.waiting(_waiting_text):
                    result = self._call_logged(
                        self.provider.complete,
                        system, user, json_mode=json_mode, max_tokens=max_tokens,
                    )
                self.total_calls += 1
                self.consecutive_server_errors = 0
                self.consecutive_since_consult = 0
                self.stage_wait["consulted_at"] = self.stage_wait.get("waited_s", 0.0)
                self.breaker.record_success()
                if result.input_tokens is not None:
                    self.total_input_tokens += result.input_tokens
                if result.output_tokens is not None:
                    self.total_output_tokens += result.output_tokens
                self.key_switches = getattr(self.provider, "switches", 0)
                log.info(
                    "llm %s/%s call %d (%s, replied in %.1f s): in=%s out=%s tokens (total in=%s out=%s)",
                    self.provider.name, self.provider.model, self.total_calls,
                    self.request_label or "complete", self.last_request_elapsed_s or 0.0,
                    result.input_tokens, result.output_tokens,
                    self.total_input_tokens, self.total_output_tokens,
                )
                return result.text
            except ServerErrorLLMError as exc:
                # Update 12 2.1: model-wide capacity errors (503/500/502) —
                # no key rotation; retry the SAME request with the bounded
                # backoff schedule (at most len(backoff_s) attempts) and the
                # breaker counting the request.
                last_error = exc
                cap_attempts += 1
                backoff = self._process_server_error(exc, cap_attempts, max_cap_attempts)
                if cap_attempts >= max_cap_attempts:
                    raise LLMError(
                        f"giving up after {cap_attempts} attempts ({self.provider.name}): {exc}"
                    ) from exc
                self._sleep_backoff(backoff)
            except TimeoutLLMError as exc:
                # Update 12 2.1: a client timeout is a latency signal — the
                # same schedule, and it may split once (split-eligible chunk).
                last_error = exc
                cap_attempts += 1
                backoff = self._process_server_error(exc, cap_attempts, max_cap_attempts)
                if cap_attempts >= max_cap_attempts:
                    raise LLMError(
                        f"giving up after {cap_attempts} attempts ({self.provider.name}): {exc}"
                    ) from exc
                self._sleep_backoff(backoff)
            except RateLimitError as exc:
                last_error = exc
                kind = getattr(exc, "kind", None) or "minute"
                retry_after = float(getattr(exc, "retry_after", None) or 60.0)
                if kind == "day":
                    # Per-day quota: when no key remains for this model, go
                    # straight to the fallback (2.4) — no 3-attempt loop and
                    # no hour-long wait. (A one-key provider is 'no key left'.)
                    self._handle_overload(quota_gone=True)  # raises per policy
                    raise LLMError(
                        f"daily quota used up for {self.provider.model} on every "
                        "key. Wait for the quota reset and re-run the same "
                        "command — stages are resumable."
                    ) from exc
                # Per-minute 429: pause EVERY request to this model until the
                # delay passes (a global pause, not a per-request sleep) — no
                # key rotation, no attempt consumed.
                if pauses_left <= 0:
                    raise LLMError(
                        f"giving up after repeated per-minute rate limits "
                        f"({self.provider.name}): {last_error}"
                    ) from exc
                pauses_left -= 1
                self.rate_pause_until = _time.monotonic() + min(retry_after, 600.0)
                log.warning(
                    "llm %s per-minute rate limit: %s — pausing every request "
                    "to this model for %.0f s",
                    self.provider.name, exc, min(retry_after, 600.0),
                )
                _panel("llm.rate_pause", n=int(min(retry_after, 600.0)))
            except InternalLLMError:
                # AGENT_UPDATE_7.md 4.3: deterministic — fails immediately,
                # never retried, never reported as a connection problem.
                raise
            except (LLMError, ConnectionError, OSError) as exc:
                last_error = exc
                self.consecutive_server_errors = 0
                log.warning(
                    "llm %s attempt %d failed: %s",
                    self.provider.name, gen_attempts + 1, exc,
                )
                gen_attempts += 1
                if gen_attempts >= MAX_RETRIES:
                    raise LLMError(
                        f"giving up after {gen_attempts} attempts ({self.provider.name}): {exc}"
                    ) from exc
                slept = heartbeat.sleeping(BACKOFF_BASE_S * gen_attempts, _backoff_text, self.cancel)
                self.stage_wait["waited_s"] = float(self.stage_wait.get("waited_s", 0.0)) + slept

    def _call_with_pool(self, system: str, user: str, *, json_mode: bool, max_tokens: int):
        """The pool-aware retry loop (Update 12 taxonomy): immediate key
        switching on a daily 429 while another key exists, a global model
        pause (no rotation) on a per-minute 429, the bounded 503/504
        backoff with the breaker, and the straight fallback when no key
        remains for the day."""
        import time as _time

        attempt_no = 0
        cap_attempts = 0
        max_cap_attempts = max(1, min(4, len(self.backoff_s)))
        gen_attempts = 0
        pauses_left = 3
        last_error: Exception | None = None
        while True:
            self._rate_gate()
            try:
                self._consume_budget()
                attempt_no += 1
                self._request_started_log("complete", attempt_no)
                with heartbeat.waiting(_waiting_text):
                    result = self._call_logged(
                        self.provider.complete,
                        system, user, json_mode=json_mode, max_tokens=max_tokens,
                    )
                self.total_calls += 1
                self.consecutive_server_errors = 0
                self.consecutive_since_consult = 0
                self.stage_wait["consulted_at"] = self.stage_wait.get("waited_s", 0.0)
                self.breaker.record_success()
                if result.input_tokens is not None:
                    self.total_input_tokens += result.input_tokens
                if result.output_tokens is not None:
                    self.total_output_tokens += result.output_tokens
                self.key_switches = getattr(self.provider, "switches", 0)
                log.info(
                    "llm %s/%s call %d (%s, replied in %.1f s): in=%s out=%s tokens (total in=%s out=%s)",
                    self.provider.name, self.provider.model, self.total_calls,
                    self.request_label or "complete", self.last_request_elapsed_s or 0.0,
                    result.input_tokens, result.output_tokens,
                    self.total_input_tokens, self.total_output_tokens,
                )
                return result.text
            except ServerErrorLLMError as exc:
                # Update 12: model-wide capacity errors — NO key rotation;
                # the bounded backoff with the breaker, the policy triggers,
                # and the one-time split for eligible latency/size chunks.
                last_error = exc
                cap_attempts += 1
                backoff = self._process_server_error(exc, cap_attempts, max_cap_attempts)
                if cap_attempts >= max_cap_attempts:
                    raise LLMError(
                        f"giving up after {cap_attempts} attempts ({self.provider.name}): {exc}"
                    ) from exc
                self._sleep_backoff(backoff)
            except TimeoutLLMError as exc:
                # Update 12: same handling as a server deadline (latency).
                last_error = exc
                cap_attempts += 1
                backoff = self._process_server_error(exc, cap_attempts, max_cap_attempts)
                if cap_attempts >= max_cap_attempts:
                    raise LLMError(
                        f"giving up after {cap_attempts} attempts ({self.provider.name}): {exc}"
                    ) from exc
                self._sleep_backoff(backoff)
            except RateLimitError as exc:
                last_error = exc
                self.consecutive_server_errors = 0
                used = getattr(exc, "key_used", None)
                kind = getattr(exc, "kind", None) or "minute"
                retry_after = float(getattr(exc, "retry_after", None) or 60.0)
                outcome = self.provider.handle_rate_limit(exc, used or "")
                self.key_switches = getattr(self.provider, "switches", 0)
                log.warning(
                    "llm %s rate limit (%s): %s",
                    self.provider.name, kind, exc,
                )
                if kind == "minute":
                    # A per-minute 429 pauses EVERY request to this model
                    # until the delay passes (a global pause) — with no key
                    # rotation for a minute limit.
                    if pauses_left <= 0:
                        raise LLMError(
                            f"giving up after repeated per-minute rate limits "
                            f"({self.provider.name}): {exc}"
                        ) from exc
                    pauses_left -= 1
                    self.rate_pause_until = _time.monotonic() + min(retry_after, 600.0)
                    _panel("llm.rate_pause", n=int(min(retry_after, 600.0)))
                    continue
                if outcome == "switched":
                    # A per-day quota on one key with another key available:
                    # retry immediately on the fresh key.
                    continue
                if outcome == "fallback":
                    # No key remains for the selected model — straight to the
                    # fallback (2.4), no attempt loop and no hour-long wait.
                    self._handle_overload(quota_gone=True)  # raises per policy
                    self.provider.raise_exhausted()
                # All keys limited: wait until the earliest cooldown; the
                # reduced-concurrency callback fires ONLY in this case.
                if self.on_rate_limit is not None:
                    try:
                        self.on_rate_limit(min(self.provider.pool._max_wait_s, 120.0))
                    except Exception:
                        log.exception("on_rate_limit callback failed")
                if self.provider.wait_for_key():
                    continue
                self.provider.raise_exhausted()
            except InternalLLMError:
                # AGENT_UPDATE_7.md 4.3: deterministic — fails immediately,
                # never retried, never reported as a connection problem.
                raise
            except InvalidKeyError as exc:
                attempt_no += 1
                last_error = exc
                used = getattr(exc, "key_used", None)
                outcome = self.provider.handle_invalid_key(used or "")
                self.key_switches = getattr(self.provider, "switches", 0)
                log.warning(
                    "llm %s: key #%s invalid — skipped",
                    self.provider.name,
                    self.provider.pool.position(used or "") if used else "?",
                )
                if outcome == "switched":
                    continue
                raise LLMError(
                    "invalid Gemini API key: the stored key was rejected and no "
                    "valid key remains (check Settings → API keys)"
                ) from exc
            except (LLMError, ConnectionError, OSError) as exc:
                last_error = exc
                self.consecutive_server_errors = 0
                log.warning(
                    "llm %s attempt %d failed: %s",
                    self.provider.name, gen_attempts + 1, exc,
                )
                gen_attempts += 1
                if gen_attempts >= MAX_RETRIES:
                    raise LLMError(
                        f"giving up after {gen_attempts} attempts ({self.provider.name}): {exc}"
                    ) from exc
                if self.cancel is not None and self.cancel.is_set():
                    break
                slept = heartbeat.sleeping(BACKOFF_BASE_S * gen_attempts, _backoff_text, self.cancel)
                self.stage_wait["waited_s"] = float(self.stage_wait.get("waited_s", 0.0)) + slept
            except KeyExhaustedError:
                # acquire() found every key unavailable right now: this is
                # not a failure of a request — wait until the earliest
                # cooldown WITHOUT consuming a retry attempt (AGU5 2.4).
                if self.provider.wait_for_key():
                    continue
                self.provider.raise_exhausted()  # fail-fast message if all daily
        if isinstance(last_error, InvalidKeyError):
            raise LLMError(
                "invalid Gemini API key: the stored key was rejected and no "
                "valid key remains (check Settings → API keys)"
            ) from last_error
        raise LLMError(
            f"giving up after {attempt_no} attempts ({self.provider.name}): {last_error}"
        ) from last_error

    def _call_and_validate(
        self, system: str, user_base: str, validate: Callable[[str], object], error_note_builder
    ):
        """AGU8 3.1: the OUTER loop catches ONLY reply-invalid (parse and
        validation) errors, at most MAX_VALIDATION_ATTEMPTS times. Transport,
        rate-limit and server errors are retried ONLY by the inner loop
        (self._call); the old nesting multiplied a persistent 503 into up to
        9 requests."""
        error_note = ""
        last_error: Exception | None = None
        for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
            text = None
            try:
                text = self._call(system, user_base + ("\n\n" + error_note if error_note else ""))
                # _call returns text; only validate() raising below counts
                # as a reply-invalid failure (a reply arrived but is bad).
                try:
                    return validate(text)
                except LLMError as exc:
                    last_error = exc
                    _log_raw_reply_diagnosis(text)
                    error_note = error_note_builder(exc)
                    log.warning("llm validation retry: %s", exc)
            except OverloadFallbackFast:
                raise
            except (SplitRequiredError,):
                raise  # AGU11 2.2: split happens at translate_resilient
            except LLMError as exc:
                # A transport/side-error while text is still None: the inner
                # loop already gave up or the outcome is final — propagate.
                if text is None:
                    raise
                last_error = exc
        if isinstance(last_error, RateLimitError):
            raise last_error
        raise LLMError(
            f"giving up after {MAX_VALIDATION_ATTEMPTS} invalid replies "
            f"({self.provider.name}): {last_error}"
        ) from last_error

    # ------------------------------------------------------------------ API
    def translate_batch(
        self, segments: list[dict], context: list[dict], glossary: dict | None = None,
        brief: str | None = None, pronouns: dict | None = None,
        policy: str | None = None,
    ) -> list[dict]:
        ids = [s["id"] for s in segments]

        def validate(text: str) -> list[dict]:
            return parsing.parse_json_array(text, ids)

        def note(exc: Exception) -> str:
            return (
                f"Your previous reply was invalid: {exc}. Follow the format exactly "
                "and reply again with only the JSON array."
            )

        return self._call_and_validate(
            _system_of("translate"),
            render_translate_user(
                segments, context, glossary, brief=brief, pronouns=pronouns,
                policy=policy,
            ),
            validate,
            note,
        )

    def shorten(self, english: str, current_vi: str, max_chars: int) -> str:
        def note(exc: Exception) -> str:
            return (
                f"Your previous reply was invalid: {exc}. Reply again with only "
                '{"vi": "..."} as JSON.'
            )

        return self._call_and_validate(
            _system_of("shorten"),
            render_shorten_user(english, current_vi, max_chars),
            parsing.parse_shortened,
            note,
        )

    def shorten_batch(self, items: list[dict], policy: str | None = None) -> list[dict]:
        """One-time enforcement batch: shorten over-budget segments."""
        ids = [s["id"] for s in items]

        def validate(text: str) -> list[dict]:
            return parsing.parse_batch_shortened(text, ids)

        def note(exc: Exception) -> str:
            return (
                f"Your previous reply was invalid: {exc}. Follow the format exactly "
                "and reply again with only the JSON array."
            )

        return self._call_and_validate(
            _system_of("shorten_batch"),
            render_shorten_batch_user(items, policy=policy),
            validate,
            note,
        )


def _log_raw_reply_diagnosis(raw_reply: str) -> None:
    """Keep the failing raw reply (truncated to 500 characters) in the log
    with a guessed cause, so a parse failure is diagnosable after the fact
    (AGENT_UPDATE_5.md task 5.3). The reply comes from the model and never
    contains our stored key values."""
    causes: list[str] = []
    if "```" in raw_reply:
        causes.append("markdown code fence around the JSON")
    if not raw_reply.strip().endswith(("}", "]")):
        causes.append("reply does not end with } or ] — looks cut off (token limit?)")
    if raw_reply and raw_reply[0] not in "{ [`\n\t\r":
        causes.append("reply starts with prose, not JSON")
    cause_text = "; ".join(causes) if causes else "no obvious pattern — see reply below"
    log.warning(
        "llm reply failed validation (%d chars, cause: %s), first 500:",
        len(raw_reply), cause_text,
    )
    log.warning("llm raw reply: %.500s", raw_reply)


def _system_of(kind: str) -> str:
    from dubtool.llm import prompts

    return {
        "translate": prompts.TRANSLATE_SYSTEM,
        "shorten": prompts.SHORTEN_SYSTEM,
        "shorten_batch": prompts.SHORTEN_BATCH_SYSTEM,
    }[kind]
