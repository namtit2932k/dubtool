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


class InternalLLMError(LLMError):
    """Deterministic failure inside the app (bad SDK options, programming
    error) — AGENT_UPDATE_7.md 4.3: NEVER retried, never reported as a
    network problem. Wraps the original as its cause."""


class NetworkLLMError(LLMError):
    """Connection could not be established (DNS, TCP, SSL) — retryable."""


class TimeoutLLMError(LLMError):
    """The server accepted the connection but the answer took too long —
    retryable (AGENT_UPDATE_7.md 4.3: distinct from no-connection)."""


# --- error classification by TYPE + cause chain (AGENT_UPDATE_7.md 4.3) ----
# Update 6 searched the message text, which misfiled SDK validation errors
# mentioning "timeout" as network failures. _walk() follows __cause__ and
# __context__ so a wrapped transport error still classifies correctly.

_TIMEOUT_TYPES = frozenset()
_NETWORK_TYPES = frozenset()


def _walk(exc: BaseException):
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for link in (current.__cause__, current.__context__):
            if isinstance(link, BaseException):
                stack.append(link)


def _exc_id(exc: BaseException) -> str:
    return f"{type(exc).__module__}.{type(exc).__name__}"


def _load_markers() -> tuple[frozenset, frozenset, frozenset]:
    """Exception identities by module+name, without importing httpx at
    module import time (it is a google-genai dependency, but tests may fake
    their own exceptions with the real names)."""
    timeout = {
        "httpx.ConnectTimeout", "httpx.ReadTimeout", "httpx.WriteTimeout",
        "httpx.PoolTimeout", "httpx.ConnectingError", "httpx.TimeoutException",
        "socket.timeout", "builtins.TimeoutError", "builtins.ConnectionError",
    }
    network = {
        "httpx.ConnectError", "httpx.NetworkError", "httpx.ReadError",
        "httpx.WriteError", "httpx.RemoteProtocolError", "httpx.ProxyError", 
        "socket.gaierror", "socket.herror", "socket.ConnectionError",
        "builtins.ConnectionError", "builtins.ConnectionResetError",
        "builtins.ConnectionRefusedError", "builtins.BrokenPipeError",
        "ssl.SSLError", "ssl.SSLCertVerificationError", "ssl.SSLEOFError",
        "urllib.error.URLError", "dns.resolver.LifetimeTimeout",
        "dns.resolver.NoNameservers", "dns.resolver.NXDOMAIN",
        "http.client.RemoteDisconnected", "aiohttp.ClientConnectorError",
    }
    internal = {
        "pydantic_core.ValidationError", "pydantic_core._pydantic_core.ValidationError",
        "pydantic.ValidationError",
        "builtins.TypeError", "builtins.AttributeError",
        "builtins.KeyError", "builtins.NameError", "builtins.UnboundLocalError",
        "builtins.RecursionError", "builtins.ZeroDivisionError",
    }
    return frozenset(timeout), frozenset(network), frozenset(internal)


def _transport_kind(exc: BaseException) -> str | None:
    """'timeout' | 'network' | None, decided from exception types along the
    cause chain — never from text (AGENT_UPDATE_7.md 4.3)."""
    timeout, network, _internal = _load_markers()
    kinds: set[str] = set()
    for e in _walk(exc):
        name = _exc_id(e)
        # SSL failures look like any OSError; treat ssl modules explicitly.
        if name.startswith("ssl.") or "ssl" in name.lower():
            kinds.add("network")
        elif name in timeout:
            kinds.add("timeout")
        elif name in network:
            kinds.add("network")
    if "network" in kinds:
        return "network"  # a connect failure is the more informative verdict
    if "timeout" in kinds:
        return "timeout"
    return None


def _is_internal(exc: BaseException) -> bool:
    _timeout, _network, internal = _load_markers()
    for e in _walk(exc):
        if _exc_id(e) in internal:
            return True
        # pydantic's ValidationError lives in pydantic_core._pydantic_core
        # (module ids vary across pydantic versions); match by class name.
        if type(e).__name__ == "ValidationError" and "pydantic" in type(e).__module__:
            return True
    return False


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
