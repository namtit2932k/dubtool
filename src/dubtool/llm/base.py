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
    def __init__(self, provider: str, detail: str = "", retry_after: float | None = None):
        message = f"{provider} rate limit was hit"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after


class MissingKeyError(LLMError):
    """The selected provider is not configured (e.g. no API key)."""


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
