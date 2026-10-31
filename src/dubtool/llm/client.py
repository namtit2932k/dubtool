"""Shared wrapper around any LLMProvider used by the pipeline stages.

Responsibilities (AGENT_UPDATE.md 3.1):
- retries (max 3) for transient errors with exponential backoff
- rate-limit handling: waits, honors retry hints, then fails with a clear
  provider-naming error
- parsing/validation with the error appended to the prompt on retry
- logs provider name, model, and token counts
"""

from __future__ import annotations

import logging
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


class LLMClient:
    def __init__(self, provider: LLMProvider, on_rate_limit: Callable[[float], None] | None = None,
                 cancel=None, options: dict | None = None,
                 on_overload: Callable[[], str] | None = None):
        """on_rate_limit(seconds) is called ONLY when every key is limited and
        the wrapper has to wait (AGENT_UPDATE_5.md 2.4): a caller running
        chunks in parallel can back off and reduce concurrency. A successful
        key switch never triggers it. `cancel` reaches the pool's waits via
        provider.wait_for_key().

        `options` carries the AGU8 task-2 retry knobs read from config llm:
        server_error_backoff_s, overload_after, split_on_timeout,
        split_min_units. `on_overload` is the AGU8 3.4 overload-policy
        callback: returns "wait" (keep backing off), "fallback_fast" (README
        raises OverloadFallbackFast itself), or raises (e.g. StageCancelled).
        """
        self.provider = provider
        self.on_rate_limit = on_rate_limit
        self.cancel = cancel
        self.options = options or {}
        self.on_overload = on_overload
        # Key positions that failed — keypool logging only, never values.
        self.key_switches = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
        self.consecutive_server_errors = 0
        self.overload_asked = False  # 'ask' consults on_overload only once
        # Labels from the stage ("translate chunk 3/4, 20 units, 2431 chars")
        # are attached per call and echoed on every request log line.
        self.request_label = ""
        self.last_request_elapsed_s: float | None = None
        # The per-run request budget is captured at construction (the stage's
        # main thread has the bound context; worker threads do not).
        self._budget = budget_mod.current()

    # ------------------------------------------------------------ internals
    def _server_backoff_s(self, attempt: int) -> float:
        """AGU8 3.2: exponential-with-jitter delay (s) after the `attempt`-th
        consecutive server error (1-based), from llm.server_error_backoff_s."""
        import random

        delays = [float(v) for v in self.options.get("server_error_backoff_s") or DEFAULT_SERVER_BACKOFF_S]
        base = delays[min(max(attempt - 1, 0), len(delays) - 1)]
        return max(0.1, base * (0.8 + 0.4 * random.random()))

    def _handle_overload(self) -> str:
        """AGU8 3.4: the overload policy after `overload_after` consecutive
        503/504. Returns "wait" (keep retrying) or raises OverloadFallbackFast
        (the stage then rebuilds the client around the Fast model). The
        callback may raise (e.g. StageCancelled) — it propagates untouched.
        With no callback, 'ask' behaves like 'wait'."""
        overload_after = int(self.options.get("overload_after") or 2)
        if self.consecutive_server_errors < max(1, overload_after):
            return "wait"
        if self.overload_asked:
            return "wait"  # the user already decided; keep the decision
        self.overload_asked = True
        default_policy = str(self.options.get("overload_policy") or "ask").lower()
        if self.on_overload is None:
            policy = default_policy
        else:
            action = self.on_overload()  # may raise (dialog Cancel)
            policy = str(action or default_policy).lower()
        if policy in ("fallback_fast", "fallback-fast"):
            from dubtool.llm.base import OverloadFallbackFast

            raise OverloadFallbackFast(self.provider.model)
        return "wait"

    def _request_started_log(self, kind: str) -> None:
        """AGU8 3.5: name the model, the request kind and its size before the
        call; the stage sets request_label like 'translate chunk 3/4 (20
        units, 2431 chars)'."""
        label = self.request_label or kind
        # The per-mode timeout lives on the provider (llm/__init__.py get_provider).
        timeout = float(getattr(self.provider, "timeout_s", 0) or 0)
        tail = f" — đang chờ phản hồi (timeout {timeout:.0f} s)" if timeout else ""
        log.info("llm %s/%s — %s%s", self.provider.name, self.provider.model, label, tail)

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

        Pool-capable providers (gemini + KeyPool, AGENT_UPDATE_5.md 2.4):
        a 429 or an invalid key switches to the next available key
        immediately — no sleep, no retry attempt consumed; only when ALL
        keys are limited does the wrapper wait (honoring the cancel event
        and total-wait cap; on_rate_limit reduced-concurrency callback fires
        only in that case). Give up with a clear error naming how many keys
        were tried after the total wait ran out.
        """
        if self._pool_capable():
            return self._call_with_pool(system, user, json_mode=json_mode, max_tokens=max_tokens)
        # (the non-pool loop below re-raises InternalLLMError directly in the
        # same way — see the except clauses)
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self._consume_budget()
                self._request_started_log("complete")
                with heartbeat.waiting(_waiting_text):
                    result = self._call_logged(
                        self.provider.complete,
                        system, user, json_mode=json_mode, max_tokens=max_tokens,
                    )
                self.total_calls += 1
                self.consecutive_server_errors = 0
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
                # AGU8 3.2: model-wide server errors — rotating keys cannot
                # help, so NO key rotation; exponential-with-jitter backoff
                # instead, capped at MAX_RETRIES transport attempts.
                last_error = exc
                self.consecutive_server_errors += 1
                log.warning(
                    "llm %s/%s server error %s (attempt %d/%d, %s): %s — backoff %.0f s (model-wide: no key rotation)",
                    self.provider.name, self.provider.model,
                    getattr(exc, "status", "?"), attempt, MAX_RETRIES,
                    self.request_label or "", exc, self._server_backoff_s(attempt),
                )
                self._handle_overload()  # raises OverloadFallbackFast per policy
                if attempt < MAX_RETRIES:
                    heartbeat.sleeping(
                        self._server_backoff_s(attempt), _backoff_text, self.cancel
                    )
            except RateLimitError as exc:
                last_error = exc
                self.consecutive_server_errors = 0
                wait = RATE_LIMIT_BASE_WAIT_S * attempt
                if getattr(exc, "retry_after", None):
                    wait = max(wait, float(exc.retry_after))
                log.warning(
                    "llm %s rate limit (attempt %d/%d): %s — waiting %.0fs",
                    self.provider.name, attempt, MAX_RETRIES, exc, wait,
                )
                if self.on_rate_limit is not None:
                    try:
                        self.on_rate_limit(wait)
                    except Exception:
                        log.exception("on_rate_limit callback failed")
                if attempt < MAX_RETRIES:
                    heartbeat.sleeping(wait, _backoff_text, self.cancel)
            except InternalLLMError:
                # AGENT_UPDATE_7.md 4.3: deterministic — fails immediately,
                # never retried, never reported as a connection problem.
                raise
            except (LLMError, ConnectionError, OSError) as exc:
                last_error = exc
                self.consecutive_server_errors = 0
                log.warning(
                    "llm %s attempt %d/%d failed: %s",
                    self.provider.name, attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    heartbeat.sleeping(BACKOFF_BASE_S * attempt, _backoff_text, self.cancel)
        if isinstance(last_error, RateLimitError):
            raise LLMError(
                f"giving up after {MAX_RETRIES} attempts: {last_error.provider} rate "
                f"limit was hit. Wait a bit and re-run the same command — stages are "
                f"resumable." ) from last_error
        raise LLMError(
            f"giving up after {MAX_RETRIES} attempts ({self.provider.name}): {last_error}"
        ) from last_error

    def _call_with_pool(self, system: str, user: str, *, json_mode: bool, max_tokens: int):
        """The pool-aware retry loop: immediate switching, then waiting."""
        attempts = 0
        last_error: Exception | None = None
        while attempts < MAX_RETRIES:
            try:
                self._consume_budget()
                self._request_started_log("complete")
                with heartbeat.waiting(_waiting_text):
                    result = self._call_logged(
                        self.provider.complete,
                        system, user, json_mode=json_mode, max_tokens=max_tokens,
                    )
                self.total_calls += 1
                self.consecutive_server_errors = 0
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
                # AGU8 3.2/3.4: model-wide server errors — NO key rotation in
                # the pool loop either; jittered backoff, then the overload
                # policy after `overload_after` consecutive errors.
                attempts += 1
                last_error = exc
                self.consecutive_server_errors += 1
                log.warning(
                    "llm %s/%s server error %s (attempt %d/%d, %s): %s — backoff %.0f s (model-wide: no key rotation)",
                    self.provider.name, self.provider.model,
                    getattr(exc, "status", "?"), attempts, MAX_RETRIES,
                    self.request_label or "", exc, self._server_backoff_s(attempts),
                )
                self._handle_overload()  # raises OverloadFallbackFast per policy
                if attempts < MAX_RETRIES:
                    heartbeat.sleeping(
                        self._server_backoff_s(attempts), _backoff_text, self.cancel
                    )
            except RateLimitError as exc:
                attempts += 1
                last_error = exc
                self.consecutive_server_errors = 0
                used = getattr(exc, "key_used", None)
                outcome = self.provider.handle_rate_limit(exc, used or "")
                self.key_switches = getattr(self.provider, "switches", 0)
                log.warning(
                    "llm %s rate limit (attempt %d/%d): %s",
                    self.provider.name, attempts, MAX_RETRIES, exc,
                )
                if outcome == "switched":
                    continue  # fresh key: retry immediately, no sleep
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
                attempts += 1
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
                attempts += 1
                last_error = exc
                self.consecutive_server_errors = 0
                log.warning(
                    "llm %s attempt %d/%d failed: %s",
                    self.provider.name, attempts, MAX_RETRIES, exc,
                )
                if attempts < MAX_RETRIES:
                    if self.cancel is not None and self.cancel.is_set():
                        break
                    heartbeat.sleeping(BACKOFF_BASE_S * attempts, _backoff_text, self.cancel)
            except KeyExhaustedError:
                # acquire() found every key unavailable right now: this is
                # not a failure of a request — wait until the earliest
                # cooldown WITHOUT consuming a retry attempt (AGU5 2.4).
                if self.provider.wait_for_key():
                    continue
                self.provider.raise_exhausted()  # fail-fast message if all daily
        if isinstance(last_error, RateLimitError):
            raise LLMError(
                f"giving up after {attempts} attempts: {last_error.provider} rate "
                f"limit was hit on every key. Wait a bit and re-run the same "
                f"command — stages are resumable."
            ) from last_error
        if isinstance(last_error, InvalidKeyError):
            raise LLMError(
                "invalid Gemini API key: the stored key was rejected and no "
                "valid key remains (check Settings → API keys)"
            ) from last_error
        raise LLMError(
            f"giving up after {attempts} attempts ({self.provider.name}): {last_error}"
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
