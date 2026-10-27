"""LLM provider package: `get_provider` factory + shared client wrapper.

Stages import only from here (never provider SDKs directly — AGENT_UPDATE.md 2).
"""

from __future__ import annotations

from dubtool.llm.base import (
    InvalidKeyError,
    KeyExhaustedError,
    LLMError,
    LLMProvider,
    LLMResult,
    MissingKeyError,
    RateLimitError,
    RateLimitExhausted,
    classify_rate_limit,
    looks_like_placeholder,
)
from dubtool.llm.client import LLMClient
from dubtool.llm.parsing import parse_json_array, parse_shortened, parse_batch_shortened, strip_code_fences

PROVIDERS = ("gemini", "ollama", "anthropic")

# Default chunk sizes / models per provider (AGENT_UPDATE.md 3.3 / 4).
PROVIDER_DEFAULTS = {
    "gemini": {"model": "gemini-3.5-flash-lite", "chunk_size": 25},
    "ollama": {"model": "gemma3:12b", "chunk_size": 20, "host": "http://localhost:11434", "timeout_sec": 300},
    "anthropic": {"model": "claude-sonnet-5-5", "chunk_size": 40},
}


def get_provider(config, provider_id: str | None = None,
                 api_keys: list[str] | None = None, cancel=None) -> LLMProvider:
    """Build the provider for `provider_id`. `api_keys` gives the Gemini
    provider an explicit pool (AGENT_UPDATE_5.md 2.4): the app passes the
    secret-store list, the CLI the env-derived list; None keeps the read-only
    env fallback. `cancel` hooks the pool's waits into the pipeline's event.
    `llm.key_cooldown_s` / `llm.max_all_keys_wait_s` (config.yaml) tune the
    pool."""
    pid = (provider_id or getattr(config, "llm_provider", None) or "gemini").lower()
    if pid not in PROVIDERS:
        raise LLMError(f"unknown llm provider: {pid!r} (choose from {', '.join(PROVIDERS)})")
    cfg = getattr(config, "llm", {}).get(pid, {}) or {}
    model = cfg.get("model") or PROVIDER_DEFAULTS[pid]["model"]
    if pid == "gemini":
        from dubtool.llm.gemini import GeminiProvider

        llm_cfg = getattr(config, "llm", {}) or {}
        return GeminiProvider(
            model=model,
            api_keys=api_keys,
            cooldown_s=float(llm_cfg.get("key_cooldown_s", 60.0) or 60.0),
            max_total_wait_s=float(llm_cfg.get("max_all_keys_wait_s", 600.0) or 600.0),
            timeout_s=float(llm_cfg.get("request_timeout_s", 180.0) or 0) or None,
            cancel=cancel,
        )
    if pid == "ollama":
        from dubtool.llm.ollama import OllamaProvider

        return OllamaProvider(
            model=model,
            host=cfg.get("host") or PROVIDER_DEFAULTS[pid]["host"],
            timeout_sec=int(cfg.get("timeout_sec") or PROVIDER_DEFAULTS[pid]["timeout_sec"]),
        )
    from dubtool.llm.anthropic import AnthropicProvider

    return AnthropicProvider(model=model)


def require_provider_ready(config, provider_id: str | None = None) -> str:
    """Fail early and clearly if the *selected* provider is not configured."""
    pid = (provider_id or getattr(config, "llm_provider", None) or "gemini").lower()
    if pid == "gemini":
        key = _env("GEMINI_API_KEY")
        if not key or looks_like_placeholder(key):
            raise LLMError(
                "GEMINI_API_KEY in .env is empty or still a placeholder. Get a key "
                "at Google AI Studio and put it in .env."
            )
        return "GEMINI_API_KEY is set"
    if pid == "anthropic":
        key = _env("ANTHROPIC_API_KEY")
        if not key or looks_like_placeholder(key):
            raise LLMError(
                "ANTHROPIC_API_KEY is not set — add it to .env (see .env.example) "
                "or your shell environment"
            )
        return "ANTHROPIC_API_KEY is set"
    if pid == "ollama":
        return "Ollama requires no key"
    return pid


def _env(name: str) -> str:
    import os

    value = os.environ.get(name, "") or ""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    return value


__all__ = [
    "get_provider",
    "require_provider_ready",
    "LLMClient",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "MissingKeyError",
    "RateLimitError",
    "RateLimitExhausted",
    "InvalidKeyError",
    "KeyExhaustedError",
    "classify_rate_limit",
    "PROVIDERS",
    "PROVIDER_DEFAULTS",
    "parse_json_array",
    "parse_shortened",
    "parse_batch_shortened",
    "strip_code_fences",
    "looks_like_placeholder",
]
