"""Shared LLM types: provider interface, results, and errors.

Providers only do a single completion; retries, rate-limit handling, and
parsing/validation live in the shared wrapper (client.py), not in providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Thinking/reasoning tokens the API reports, when available (AGU4 4.2).
    thinking_tokens: int | None = None
    # Seconds to honor a provider-provided retry hint, if any (rate limits).
    retry_after_s: float | None = None


class LLMError(RuntimeError):
    """A failure attributable to the LLM provider or its output."""


class RateLimitError(LLMError):
    def __init__(self, provider: str, detail: str = "", retry_after: float | None = None, key_used: str | None = None):
        message = f"{provider} rate limit was hit"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after
        self.key_used = key_used


class MissingKeyError(LLMError):
    """The selected provider is not configured (e.g. no API key)."""


class InvalidKeyError(LLMError):
    """401/403: the API rejected this key — the pool skips it.
    `key_used` records which key value failed (in memory only)."""

    def __init__(self, *args, key_used: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.key_used = key_used


class RateLimitExhausted(LLMError):
    """All keys have been tried and are still limited (after the total wait).
    The message names how many keys were tried — never any key value."""


class KeyExhaustedError(LLMError):
    """Every key in the pool is unavailable right now (cooldowns / invalid /
    daily limit), or the list was empty."""


# --- 429 payload classification (AGENT_UPDATE_5.md 2.4) ---------------------

# The official google-genai SDK raises APIError subclasses carrying the full
# response JSON in `.details` (errors.py: APIError(code, response_json)). A
# real 429 payload looks like:
#   {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
#     "message": "You exceeded your current quota...",
#     "details": [
#       {"@type": "...google.rpc.QuotaFailure",
#        "violations": [{"description": "... per day ..."}]},
#       {"@type": "...google.rpc.RetryInfo", "retryDelay": "28s"}]}}
# Per-day vs per-minute wording: daily free-tier messages say "daily" or
# "per day" (message or violation description), per-minute limits say
# "per minute"; violation quota ids contain "PerDay" (vs "PerMinute").
_DAY_MARKERS = ("per day", "daily", "per_day", "perday", "day quota",
                "daily_free", "GenerateRequestsPerDay", "DAILY")
_MINUTE_MARKERS = ("per minute", "per_minute", "perminute", "minute quota",
                   "GENERATE_REQUESTS_PER_MINUTE")


def _detail_text(exc: Exception) -> str:
    parts: list[str] = []
    detail = getattr(exc, "details", None)
    if detail is not None:
        parts.append(_to_text(detail))
    message = getattr(exc, "message", None)
    if message:
        parts.append(str(message))
    parts.append(str(exc))
    return " ".join(p for p in parts if p)


def _to_text(obj) -> str:
    import json

    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


def classify_rate_limit(exc: Exception) -> tuple[str, float | None]:
    """('daily', None) | ('per_minute', retry_after_s | None) from a 429.

    Unidentifiable payloads fall back to ('per_minute', None), which the pool
    turns into the plain configured cooldown.
    """
    text = _detail_text(exc)
    lowered = text.lower()
    # Daily check first: the daily payload usually also mentions a retry
    # delay, but the remaining per-day quota is useless within minutes.
    if any(marker.lower() in lowered for marker in _DAY_MARKERS):
        return "daily", _extract_retry_after(text) or None
    if any(marker.lower() in lowered for marker in _MINUTE_MARKERS):
        return "per_minute", _extract_retry_after(text) or None
    return "per_minute", _extract_retry_after(text) or None


def _extract_retry_after(text: str) -> float | None:
    """Parse the RetryInfo retryDelay (e.g. "28s", "1.5s") from the payload.
    Falls back to any 'retry-after' hint reported on the exception."""
    import re

    m = re.search(r'"?retryDelay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)\s*s', text, re.I)
    if not m:
        m = re.search(r'retry[- ]?after\s*[:=]\s*"?(\d+(?:\.\d+)?)', text, re.I)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = True,
        max_tokens: int = 8000,
    ) -> LLMResult: ...


def read_env_key(name: str, root: Path | None = None) -> str:
    """Fetch an env var, defensively stripped of whitespace/quotes."""
    import os

    value = os.environ.get(name, "")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    return value


def looks_like_placeholder(value: str) -> bool:
    """True when a key value is empty or still a placeholder.

    Match AGENT_UPDATE.md: empty, contains spaces (e.g. `key cua toi`),
    or obviously non-key text.
    """
    if not value:
        return True
    if any(ch.isspace() for ch in value):
        return True
    lowered = value.lower()
    for phrase in ("placeholder", "cua toi", "your key", "your_key", "changeme", "xxx", "todo"):
        if phrase in lowered:
            return True
    return False
