"""LLM provider package: `get_provider` factory + shared client wrapper.

Stages import only from here (never provider SDKs directly — AGENT_UPDATE.md 2).
"""

from __future__ import annotations

import logging

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
log = logging.getLogger("dubtool")
_FAULT_STAGE = "llm"

from dubtool.llm.client import LLMClient
from dubtool.llm.parsing import parse_json_array, parse_shortened, parse_batch_shortened, strip_code_fences

PROVIDERS = ("gemini", "ollama", "anthropic")

# Default chunk sizes / models per provider (AGENT_UPDATE.md 3.3 / 4).
PROVIDER_DEFAULTS = {
    "gemini": {"model": "gemini-3.5-flash-lite", "chunk_size": 25},
    "ollama": {"model": "gemma3:12b", "chunk_size": 20, "host": "http://localhost:11434", "timeout_sec": 300},
    "anthropic": {"model": "claude-sonnet-5-5", "chunk_size": 40},
}


class _FaultProvider:
    """AGU8 2.5/10: debug-only fault injection for the LLM path.

    `--fault llm:503-first=N` makes the first N provider.complete() calls of
    the PROCESS raise ServerErrorLLMError(503) so the packaged exe can prove
    the retry/overload paths without a real outage; `fail-first` raises a
    plain connection error, `timeout-first` a TimeoutLLMError. The counter is
    process-global and counts actual call attempts (retries included): a
    rebuilt client after OverloadFallbackFast continues where the old one
    left off instead of re-faulting forever. Faults are only declared by the
    CLI/--headless-run (ignored by the GUI) and never concern key values.
    """

    _counter: dict[str, int] = {}

    def __init__(self, inner, spec: dict[str, int]):
        self._inner = inner
        self.spec = spec

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def complete(self, system: str, user: str, *, json_mode: bool = True,
                 max_tokens: int = 8000):
        from dubtool.llm.base import (
            ServerErrorLLMError,
            TimeoutLLMError,
        )

        for kind, exc_factory in (
            ("503-first", lambda n: ServerErrorLLMError(
                f"injected fault llm:503-first ({n}/{self.spec['503-first']})",
                status=503,
            )),
            ("fail-first", lambda n: ConnectionError(
                f"injected fault llm:fail-first ({n}/{self.spec['fail-first']})",
            )),
            ("timeout-first", lambda n: TimeoutLLMError(
                f"injected fault llm:timeout-first ({n}/{self.spec['timeout-first']})",
            )),
        ):
            limit = int(self.spec.get(kind) or 0)
            if limit <= 0:
                continue
            n = _FaultProvider._counter.get(kind, 0) + 1
            if n <= limit:
                _FaultProvider._counter[kind] = n
                log.warning(
                    "injected fault %s:%s (%d/%d) model=%s",
                    _FAULT_STAGE, kind, n, limit,
                    getattr(self._inner, "model", "?"),
                )
                raise exc_factory(n)
        return self._inner.complete(system, user, json_mode=json_mode,
                                    max_tokens=max_tokens)


def _faults_active() -> dict[str, int]:
    """The `llm` fault spec currently declared ({} normally)."""
    try:
        from dubtool import faults as faults_mod

        return faults_mod.get("llm")
    except Exception:
        return {}


def _reset_llm_faults() -> None:
    """Tests only: clear the process-global fault counters."""
    _FaultProvider._counter.clear()


def get_provider(config, provider_id: str | None = None,
                 api_keys: list[str] | None = None, cancel=None,
                 mode: str | None = None, model_override: str | None = None) -> LLMProvider:
    """Build the provider for `provider_id`. `api_keys` gives the Gemini
    provider an explicit pool (AGENT_UPDATE_5.md 2.4): the app passes the
    secret-store list, the CLI the env-derived list; None keeps the read-only
    env fallback.    `cancel` hooks the pool's waits into the pipeline's event.
    `llm.key_cooldown_s` / `llm.max_all_keys_wait_s` (config.yaml) tune the
    pool.

    AGU8 3.5: `mode` selects the per-mode request timeout — llm request
    timeout fast (default 90 s) in Fast mode, request timeout s (180 s)
    otherwise; None keeps the legacy single value. Debug-only fault
    injection may wrap the built provider (see _wrap_faults)."""
    pid = (provider_id or getattr(config, "llm_provider", None) or "gemini").lower()
    if pid not in PROVIDERS:
        raise LLMError(f"unknown llm provider: {pid!r} (choose from {', '.join(PROVIDERS)})")
    cfg = getattr(config, "llm", {}).get(pid, {}) or {}
    model = model_override or cfg.get("model") or PROVIDER_DEFAULTS[pid]["model"]
    if pid == "gemini":
        from dubtool.llm.gemini import GeminiProvider

        llm_cfg = getattr(config, "llm", {}) or {}
        if mode == "fast":
            timeout_val = llm_cfg.get("request_timeout_s_fast", 90.0)
            timeout_default = 90.0
        else:
            timeout_val = llm_cfg.get("request_timeout_s", 180.0)
            timeout_default = 180.0
        provider = GeminiProvider(
            model=model,
            api_keys=api_keys,
            cooldown_s=float(llm_cfg.get("key_cooldown_s", 60.0) or 60.0),
            max_total_wait_s=float(llm_cfg.get("max_all_keys_wait_s", 600.0) or 600.0),
            timeout_s=float(timeout_val or timeout_default) or None,
            cancel=cancel,
        )
        return _wrap_faults(provider)
    if pid == "ollama":
        from dubtool.llm.ollama import OllamaProvider

        provider = OllamaProvider(
            model=model,
            host=cfg.get("host") or PROVIDER_DEFAULTS[pid]["host"],
            timeout_sec=int(cfg.get("timeout_sec") or PROVIDER_DEFAULTS[pid]["timeout_sec"]),
        )
        return _wrap_faults(provider)
    from dubtool.llm.anthropic import AnthropicProvider

    return _wrap_faults(AnthropicProvider(model=model))


def _wrap_faults(provider: LLMProvider) -> LLMProvider:
    """Debug-only fault injection (AGU8 2.5): `--fault llm:503-first=N`
    (declared by the CLI/headless only) replaces provider.complete with a
    faulting wrapper. Faults must survive the stage's OverloadFallbackFast
    rebuild, so they are installed here at the factory (and their counter is
    process-global)."""
    spec = _faults_active()
    if not spec:
        return provider
    log.warning("fault injection active for llm: %s (debug-only)", spec)
    return _FaultProvider(provider, spec)


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
