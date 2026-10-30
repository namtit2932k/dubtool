"""Gemini provider via the official google-genai SDK (default, free tier).

Keys are passed explicitly (`api_keys`, AGENT_UPDATE_5.md 2.4): the app
builds the list from the secret store and the CLI from its environment —
never through os.environ inside the pipeline. Selection rules live in
llm/keypool.py (thread-safe, sequential, cooldown-aware); this module maps
SDK errors onto pool outcomes so the shared client (client.py) can switch
keys immediately and mark failures against the key each call used.
"""

from __future__ import annotations

import logging
import threading

from dubtool.llm.base import (
    InternalLLMError,
    InvalidKeyError,
    LLMError,
    LLMResult,
    MissingKeyError,
    NetworkLLMError,
    RateLimitError,
    RateLimitExhausted,
    ServerErrorLLMError,
    TimeoutLLMError,
    _is_internal,
    _transport_kind,
    classify_rate_limit,
    looks_like_placeholder,
    read_env_key,
)
from dubtool.llm.keypool import (
    DEFAULT_COOLDOWN_S,
    DEFAULT_MAX_TOTAL_WAIT_S,
    DEFAULT_MAX_WAIT_S,
    KeyPool,
)

log = logging.getLogger("dubtool")


def build_gemini_client(api_key: str, timeout_ms: int | None = None):
    """The ONE factory for google-genai clients (AGENT_UPDATE_7.md 4.1).

    The SDK's HttpOptions.timeout is an `Optional[int]` in MILLISECONDS
    (google-genai 2.33.0, verified with inspect). Update 6 passed a string
    like "180000ms", which the pydantic model rejects — every Gemini
    request then failed before it left the machine. Everything that builds
    a client (provider, key test, model-list refresh) goes through here.
    """
    from google import genai

    if timeout_ms is not None:
        return genai.Client(
            api_key=api_key,
            http_options={"timeout": int(timeout_ms)},
        )
    return genai.Client(api_key=api_key)


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        model: str,
        api_keys: list[str] | None = None,
        *,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        max_total_wait_s: float = DEFAULT_MAX_TOTAL_WAIT_S,
        timeout_s: float | None = None,
        cancel=None,
    ):
        self.model = model
        if api_keys is None:
            # Library default for tools that get no explicit list: env vars
            # as today (GEMINI_API_KEY, then _2, _3, …), read-only.
            api_keys = self._keys_from_env()
        keys = [k for k in (api_keys or []) if k and not looks_like_placeholder(k)]
        if not keys:
            raise MissingKeyError(
                "No Gemini API key. In the app set it in Settings (stored in "
                "Windows Credential Manager); for the CLI put "
                'GEMINI_API_KEY = "..." in .env (Google AI Studio).'
            )
        self.pool = KeyPool(
            keys, cooldown_s=cooldown_s, max_wait_s=max_wait_s,
            max_total_wait_s=max_total_wait_s, cancel=cancel, model=model,
        )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise MissingKeyError(
                "google-genai SDK not installed — run: pip install google-genai"
            ) from exc
        self._genai = genai
        self._clients: dict[str, object] = {}
        self._client_lock = threading.Lock()
        # AGU6 5.4: per-request cap (llm.request_timeout_s), applied to each
        # SDK call through the HTTP options so a stalled request cannot hang
        # the pipeline for minutes.
        self._timeout_s = timeout_s

    @property
    def timeout_s(self) -> float | None:
        """AGU8 3.5: the request cap the request logs report (read alias of
        the private _timeout_s)."""
        return self._timeout_s
        # Optional UI-facing event callback for user-facing key notices
        # ("key #2 invalid — skipped"); positions only, never values.
        self.key_event = None

    @staticmethod
    def _keys_from_env() -> list[str]:
        keys = [read_env_key("GEMINI_API_KEY")]
        i = 2
        while True:
            value = read_env_key(f"GEMINI_API_KEY_{i}")
            if not value:
                break
            keys.append(value)
            i += 1
        return keys

    # ----------------------------------------------------------- key utils
    @property
    def keys(self) -> list[str]:
        """Key values in priority order — in memory only for tests/tools;
        never logged or written anywhere."""
        return self.pool.values()

    @property
    def api_key(self) -> str:
        """Current key — callers only ever see the object, not the value."""
        return self.pool.acquire()

    @property
    def switches(self) -> int:
        return self.pool.switches

    def client_for(self, key: str):
        with self._client_lock:
            client = self._clients.get(key)
            if client is None:
                timeout_ms = int(self._timeout_s * 1000) if self._timeout_s else None
                client = build_gemini_client(key, timeout_ms)
                self._clients[key] = client
            return client

    @property
    def client(self):
        """Backward-compatible accessor: client for the current key."""
        return self.client_for(self.api_key)

    # -------------------------------------------- pool outcome handling
    def handle_rate_limit(self, exc: RateLimitError, used_key: str) -> str:
        """Mark the 429 against the key that failed; return the outcome for
        the client: 'switched' | 'wait' (all keys limited)."""
        kind, retry_after = classify_rate_limit(exc)
        if kind == "daily":
            self.pool.mark_daily_limit(used_key, until_epoch=self._daily_until(exc))
        else:
            self.pool.mark_rate_limited(used_key, retry_after)
        return self._next_key_after_failure(kind)

    @staticmethod
    def _daily_until(exc: Exception) -> float:
        """Epoch until which the per-day quota stays out for this (key,
        model): the API's retryDelay hint when present (often many hours),
        otherwise the next midnight Pacific (AGU6 5.3)."""
        import time

        _, retry_after = classify_rate_limit(exc)
        now = time.time()
        if retry_after is not None:
            # A daily payload's retryDelay can be expressed in seconds (e.g.
            # 7h = 25200s). Sanity cap it at 24h so a per-minute style hint
            # mistaken as huge is bounded.
            return now + min(float(retry_after), 24 * 3600.0)
        from dubtool.llm import quota_state

        return quota_state.next_pacific_midnight(now)

    def handle_invalid_key(self, used_key: str) -> str:
        """401/403: mark the key invalid for the run; 'switched' | 'exhausted'."""
        self.pool.mark_invalid(used_key)
        if self.key_event is not None:
            try:
                self.key_event(f"key #{self.pool.position(used_key)} invalid — skipped")
            except Exception:  # UI callbacks must never break the run
                pass
        return self._next_key_after_failure("invalid")

    def _next_key_after_failure(self, kind: str) -> str:
        try:
            self.pool.acquire()
        except Exception:
            if kind == "invalid":
                return "exhausted"
            return "wait"
        self.pool.switches += 1
        log.warning(
            "keypool: switched to the next available key (previous key %s)",
            "invalid" if kind == "invalid" else "rate limited",
        )
        return "switched"

    def wait_for_key(self) -> bool:
        """All keys limited: wait until the earliest cooldown (honors cancel).
        False when the wait was interrupted or the total wait ran out."""
        return self.pool.wait_for_key()

    def raise_exhausted(self) -> None:
        tried = self.pool.failed_count()
        if self.pool.all_daily_exhausted():
            raise RateLimitExhausted(
                f"all {self.pool.keys_count()} API keys have used up their "
                f"free-tier daily quota for this model ({self.model}). Free-tier "
                "daily quotas reset around midnight Pacific time — the run "
                "will pick up again from the failed stage when a reset or a "
                "new key is available."
            )
        raise RateLimitExhausted(
            f"giving up: {self.pool.keys_count()} API keys failed or were rate "
            f"limited ({tried} permanently out); stages are resumable — wait "
            "and re-run the same command."
        )

    # ------------------------------------------------------------- complete
    @property
    def _types(self):
        from google.genai import types

        return types

    def complete(
        self, system: str, user: str, *, json_mode: bool = True, max_tokens: int = 8000,
        thinking_level: str | None = None,
    ) -> LLMResult:
        """thinking_level, when given, is passed as the official thinking
        control (ai.google.dev); None keeps the model default."""
        types = self._types
        used_key = self.pool.acquire()
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.3,
            max_output_tokens=max_tokens,
        )
        if thinking_level:
            config.thinking_config = types.ThinkingConfig(thinking_level=thinking_level)
        if json_mode:
            config.response_mime_type = "application/json"
        try:
            response = self.client_for(used_key).models.generate_content(
                model=self.model,
                contents=[user],
                config=config,
            )
        except Exception as exc:
            raise self._map_error(exc, used_key) from exc

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage else None
        thinking_tokens = getattr(usage, "thoughts_token_count", None) if usage else None

        text = None
        try:
            text = response.text
        except (ValueError, AttributeError):
            text = None
        if text is None or not text:
            feedback = getattr(response, "prompt_feedback", None) or getattr(
                response, "candidates", None
            )
            raise LLMError(
                f"gemini returned no text (model={self.model}, usage={usage}); "
                f"response signals: {feedback}"
            )
        return LLMResult(
            text=text,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            thinking_tokens=thinking_tokens if isinstance(thinking_tokens, int) else None,
        )

    def _map_error(self, exc: Exception, used_key: str) -> Exception:
        """Tag the failure with the key used so the client can mark the pool.

        AGENT_UPDATE_7.md 4.3: deterministic internal failures (validation
        errors, programming errors — the update-6 case was a bad timeout
        format rejected by the SDK) become InternalLLMError, which the
        client fails fast on instead of retrying 3 times and misreading as
        a network problem."""
        # Transport failures first: connection problems vs read timeouts.
        # (The caller raises the result `from exc`, which chains the original
        # so api._classify_error can still walk up to the transport cause.)
        kind = _transport_kind(exc)
        if kind == "timeout":
            return TimeoutLLMError(
                f"the request timed out before a reply ({self.name})"
            )
        if kind == "network":
            return NetworkLLMError(f"the connection to {self.name} failed")
        code = getattr(exc, "code", None)
        if code == 429:
            return RateLimitError(self.name, str(exc), key_used=used_key)
        if code in (401, 403):
            return InvalidKeyError(
                f"gemini rejected the API key (401/403): check this key at "
                "Google AI Studio (the key value is never shown here)",
                key_used=used_key,
            )
        # AGU8 task 2: 5xx are model-wide (a key switch cannot help) and
        # map to the server-error type with its HTTP status.
        if isinstance(code, int) and code >= 500:
            label = {503: "UNAVAILABLE (high demand)", 504: "DEADLINE_EXCEEDED"}.get(
                code, f"HTTP {code}"
            )
            return ServerErrorLLMError(
                f"{self.name} server error {code} {label}", status=code
            )
        text = str(exc)
        # Some SDK error shapes carry the status only in the message.
        if "UNAVAILABLE" in text or "unavailable" in text:
            return ServerErrorLLMError(
                f"{self.name} server error 503 UNAVAILABLE (high demand)", status=503
            )
        if "DEADLINE_EXCEEDED" in text:
            return ServerErrorLLMError(
                f"{self.name} server error 504 DEADLINE_EXCEEDED", status=504
            )
        if "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
            return RateLimitError(self.name, text, key_used=used_key)
        if "API key" in text or "API_KEY" in text:
            return InvalidKeyError(
                f"gemini rejected the API key: {text} (the key value is never "
                "shown here)",
                key_used=used_key,
            )
        # Validation or programming errors (e.g. pydantic rejecting SDK
        # options, TypeError/AttributeError/NameError) are deterministic —
        # never retried, never a network problem.
        if _is_internal(exc):
            mapped = InternalLLMError(
                "an internal error prevented the request (not a connection "
                f"problem): {text}"
            )
        else:
            mapped = LLMError(f"gemini API error: {text}")
        return mapped
