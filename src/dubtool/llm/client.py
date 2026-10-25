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
    LLMError,
    LLMProvider,
    RateLimitError,
)
from dubtool.llm import parsing
from dubtool.llm.prompts import (
    render_shorten_batch_user,
    render_shorten_user,
    render_translate_user,
)

log = logging.getLogger("dubtool")

MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0
RATE_LIMIT_BASE_WAIT_S = 30.0


class LLMClient:
    def __init__(self, provider: LLMProvider, on_rate_limit: Callable[[float], None] | None = None):
        """on_rate_limit(seconds) is called (once per observed 429, in addition
        to the internal retry wait) so a caller running chunks in parallel can
        back off and reduce concurrency (AGU4 4.1)."""
        self.provider = provider
        self.on_rate_limit = on_rate_limit
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0

    # ------------------------------------------------------------ internals
    def _call(self, system: str, user: str, *, json_mode: bool = True, max_tokens: int = 8000):
        """Raw call with transient-error and rate-limit retries."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self.provider.complete(
                    system, user, json_mode=json_mode, max_tokens=max_tokens
                )
                self.total_calls += 1
                if result.input_tokens is not None:
                    self.total_input_tokens += result.input_tokens
                if result.output_tokens is not None:
                    self.total_output_tokens += result.output_tokens
                log.info(
                    "llm %s/%s call %d: in=%s out=%s tokens (total in=%s out=%s)",
                    self.provider.name,
                    self.provider.model,
                    self.total_calls,
                    result.input_tokens,
                    result.output_tokens,
                    self.total_input_tokens,
                    self.total_output_tokens,
                )
                return result.text
            except RateLimitError as exc:
                last_error = exc
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
                # Multiple keys set: rotate to the next one instead of wait-
                # ing out one key's daily quota.
                rotated = False
                if isinstance(exc, RateLimitError) and hasattr(self.provider, "rotate_key"):
                    try:
                        rotated = self.provider.rotate_key()
                        if rotated:
                            log.warning(
                                "llm %s: switched to the next API key (rate limit on the previous one)",
                                self.provider.name,
                            )
                    except Exception:
                        log.exception("rotate_key failed")
                # With another key the wait shrinks; without one, honor the hint.
                if rotated:
                    wait = min(wait, RATE_LIMIT_BASE_WAIT_S)
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
            except (LLMError, ConnectionError, OSError) as exc:
                last_error = exc
                log.warning(
                    "llm %s attempt %d/%d failed: %s",
                    self.provider.name, attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_BASE_S * attempt)
        if isinstance(last_error, RateLimitError):
            raise LLMError(
                f"giving up after {MAX_RETRIES} attempts: {last_error.provider} rate "
                f"limit was hit. Wait a bit and re-run the same command — stages are "
                f"resumable." ) from last_error
        raise LLMError(
            f"giving up after {MAX_RETRIES} attempts ({self.provider.name}): {last_error}"
        ) from last_error

    def _call_and_validate(
        self, system: str, user_base: str, validate: Callable[[str], object], error_note_builder
    ):
        error_note = ""
        last_error: Exception | None = None
        for _ in range(MAX_RETRIES):
            try:
                text = self._call(system, user_base + ("\n\n" + error_note if error_note else ""))
                return validate(text)
            except LLMError as exc:
                last_error = exc
                error_note = error_note_builder(exc)
                log.warning("llm validation retry: %s", exc)
        raise LLMError(
            f"giving up after {MAX_RETRIES} attempts ({self.provider.name}): {last_error}"
        ) from last_error

    # ------------------------------------------------------------------ API
    def translate_batch(
        self, segments: list[dict], context: list[dict], glossary: dict | None = None,
        brief: str | None = None, pronouns: dict | None = None,
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
            render_translate_user(segments, context, glossary, brief=brief, pronouns=pronouns),
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

    def shorten_batch(self, items: list[dict]) -> list[dict]:
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
            render_shorten_batch_user(items),
            validate,
            note,
        )


def _system_of(kind: str) -> str:
    from dubtool.llm import prompts

    return {
        "translate": prompts.TRANSLATE_SYSTEM,
        "shorten": prompts.SHORTEN_SYSTEM,
        "shorten_batch": prompts.SHORTEN_BATCH_SYSTEM,
    }[kind]
