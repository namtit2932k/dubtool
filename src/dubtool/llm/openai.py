"""OpenAI provider via the official `openai` SDK.

The Responses API is preferred (`POST /v1/responses`, the recommended
endpoint for new projects); Chat Completions is the recorded fallback
when a model rejects the Responses call. Structured output rides in
`text.format` (strict json_schema). GPT-5/o-series are reasoning models:
`temperature`/`top_p` are never sent, `reasoning.effort` is, and the
output budget must leave room for reasoning tokens. `max_retries=0` —
the app's own policy owns every retry, never the SDK.

Errors are mapped from exception type + status + error code (never from
message text), into the shared client classes. A 429 honors `Retry-After`
and the `x-ratelimit-reset-*` durations; an `insufficient_quota`
family code is billing — never retried, never rotated across keys
(quota belongs to the org/project, not to one key).
"""

from __future__ import annotations

import json
import logging
import re
import threading

from dubtool.llm.base import (
    InternalLLMError,
    InvalidKeyError,
    LLMError,
    LLMResult,
    MissingKeyError,
    NetworkLLMError,
    QuotaExhaustedError,
    RateLimitError,
    RequestTooLargeError,
    SchemaRejectedError,
    ServerErrorLLMError,
    TimeoutLLMError,
    _is_internal,
    _transport_kind,
    looks_like_placeholder,
    read_env_key,
)

log = logging.getLogger("dubtool")

# Error codes that mean billing is out — never retried, never rotated.
_QUOTA_CODES = {
    "insufficient_quota",
    "credit_balance_exhausted",
    "insufficient_user_funds",
    "billing_not_active",
    "spend_limit_reached",
}

_SIZE_CODES = {"context_length_exceeded", "context_token_exceeded"}


def _error_code(exc: Exception) -> str | None:
    """The provider's machine-readable error code from the exception
    body (metadata, not message text; absent -> None)."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        code = err.get("code") or err.get("type")
        if isinstance(code, str):
            return code
    return None


def _parse_timespan(value: str | None) -> float | None:
    """`x-ratelimit-reset-*` durations: '1s', '6m0s', '1h30m10s'."""
    if not value:
        return None
    total = 0.0
    ok = False
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)(h|m|s)", str(value)):
        total += float(number) * {"h": 3600.0, "m": 60.0, "s": 1.0}[unit]
        ok = True
    return total if ok else None


def _retry_after_of(exc: Exception) -> float | None:
    """Retry-After (seconds) or the reset durations from the response
    headers attached to a 429."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
    if headers is None:
        return None
    try:
        direct = headers.get("retry-after")
    except Exception:
        try:
            direct = headers.get("Retry-After")
        except Exception:
            direct = None
    if direct:
        try:
            return float(direct)
        except (TypeError, ValueError):
            pass
    for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        try:
            value = headers.get(key)
        except Exception:
            try:
                value = headers.get(key.replace("-", "_"))
            except Exception:
                value = None
        seconds = _parse_timespan(value)
        if seconds is not None:
            return seconds
    return None


def _key_from_env() -> list[str]:
    """The env fallback: OPENAI_API_KEY first, then the user's own name
    GPT_API_KEY, then numbered suffixes."""
    out: list[str] = []
    for name in ("OPENAI_API_KEY", "GPT_API_KEY"):
        value = read_env_key(name)
        if value and not looks_like_placeholder(value) and value not in out:
            out.append(value)
    i = 2
    while True:
        value = read_env_key(f"OPENAI_API_KEY_{i}")
        if not value:
            break
        if not looks_like_placeholder(value) and value not in out:
            out.append(value)
        i += 1
    i = 2
    while True:
        value = read_env_key(f"GPT_API_KEY_{i}")
        if not value:
            break
        if not looks_like_placeholder(value) and value not in out:
            out.append(value)
        i += 1
    return out


def is_reasoning_model(model_id: str, catalog_flag: bool | None = None) -> bool:
    """Older GPT/o-series ids (the 'reasoning' family): no temperature or
    top_p, `reasoning.effort` instead. The catalog flag wins when given."""
    if catalog_flag is not None:
        return catalog_flag
    mid = (model_id or "").lower()
    return mid.startswith(("gpt-5", "gpt6", "o3", "o4"))


class OpenAIProvider:
    name = "openai"
    structured = "json_schema"   # the strict json_schema ability

    def __init__(self, model: str, api_keys: list[str] | None = None, *,
                 timeout_s: float | None = None,
                 reasoning_effort: str | None = None,
                 reasoning: bool | None = None,
                 mock_transport=None,
                 max_output_tokens: int | None = None):
        self.model = model
        if api_keys is None:
            api_keys = _key_from_env()
        self._keys = [k for k in (api_keys or []) if k and not looks_like_placeholder(k)]
        if not self._keys:
            raise MissingKeyError(
                "No OpenAI API key. In the app set it in Settings → API keys "
                "(stored in Windows Credential Manager); for the CLI put "
                "OPENAI_API_KEY (or GPT_API_KEY) in .env (OpenAI Platform, paid "
                "per token)."
            )
        self._lock = threading.Lock()
        self._position = 0
        self._clients: dict[str, object] = {}
        self._transport = mock_transport
        self.reasoning = is_reasoning_model(model, reasoning)
        self.reasoning_effort = reasoning_effort or "low"
        self.default_max_output_tokens = int(max_output_tokens or 16000)
        self._timeout_s = float(timeout_s) if timeout_s else 120.0
        self.paid_requests = 0          # the per-run cost guard counter
        self.paid_request_cap = 0       # 0 = no cap (config llm.paid_request_cap)
        self._chat_fallback: dict[str, bool] = {}

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def api_key(self) -> str:
        return self.current_key()

    @property
    def switches(self) -> int:
        return getattr(self, "_switches", 0)

    def current_key(self) -> str:
        return self._keys[self._position % len(self._keys)]

    def _client_for(self, key: str):
        import time as _time

        from openai import OpenAI

        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = OpenAI(
                    api_key=key,
                    timeout=max(1.0, float(self._timeout_s)),
                    max_retries=0,
                    http_client=self._transport,
                )
                self._clients[key] = client
            return client

    def list_models(self) -> list[str]:
        """`GET /v1/models` with the current key (free; used by the key
        test and Settings -> Refresh model list)."""
        try:
            page = self._client_for(self.current_key()).models.list()
            ids = [m.id for m in page.data]
            return sorted(str(i) for i in ids if i)
        except Exception as exc:
            raise self._map_error(exc) from exc

    # ------------------------------------------------------------ complete
    def complete(self, system: str, user: str, *,
                 schema: dict | None = None, json_mode: bool = True,
                 max_tokens: int = 8000) -> LLMResult:
        # The per-run cost guard for paid models: an exhausted cap stops
        # the paid model for this run (the fallback takes over, so the
        # run is not dead) instead of spending silently.
        if self.paid_request_cap and self.paid_requests >= self.paid_request_cap:
            raise QuotaExhaustedError(
                f"the paid-request cap is reached ({self.paid_requests} requests) "
                f"for {self.model} — the rest of the run uses the fallback"
            )
        budget_used = max(8000, int(max_tokens or 8000))
        if self.reasoning:
            # Reasoning tokens count inside max_output_tokens, so the
            # answer must still fit after them; reasoning models reject
            # temperature/top_p.
            budget_used = max(self.default_max_output_tokens, budget_used * 4)
        reply = self._complete_internal(system, user, schema=json_mode and schema,
                                        json_mode=json_mode, max_tokens=budget_used)
        self.paid_requests += 1
        return reply

    def _complete_internal(self, system: str, user: str, *, schema: dict | None,
                           json_mode: bool, max_tokens: int) -> LLMResult:
        responses_api = self.use_responses_api(schema is not None or json_mode)
        key = self.current_key()
        try:
            if responses_api:
                kwargs: dict = dict(
                    model=self.model,
                    instructions=system,
                    input=user,
                    max_output_tokens=max_tokens,
                )
                fmt = self._text_format(schema, json_mode)
                if fmt is not None:
                    kwargs["text"] = fmt
                if self.reasoning:
                    kwargs["reasoning"] = {"effort": self.reasoning_effort}
                else:
                    kwargs["temperature"] = 0.2
                response = self._client_for(key).responses.create(**kwargs)
            else:
                kwargs = dict(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=max_tokens,
                )
                fmt = self._text_format(schema, json_mode, completions=True)
                if fmt is not None:
                    kwargs["response_format"] = fmt
                if self.reasoning:
                    kwargs["reasoning_effort"] = self.reasoning_effort
                else:
                    kwargs["temperature"] = 0.2
                response = self._client_for(key).chat.completions.create(**kwargs)
        except Exception as exc:
            mapped = self._map_error(exc)
            code = getattr(mapped, "status", None) or None
            if isinstance(mapped, InvalidKeyError):
                self._switch_on_invalid_key(key)
            raise mapped from exc

        text = None
        usage = None
        try:
            if responses_api:
                text = getattr(response, "output_text", None)
                if not text:
                    parts = []
                    for item in getattr(response, "output", None) or []:
                        for piece in getattr(item, "content", None) or []:
                            if getattr(piece, "type", "") == "output_text":
                                parts.append(getattr(piece, "text", ""))
                    text = "\n".join(p for p in parts if p)
            else:
                text = (response.choices[0].message.content if response.choices else None)
        except Exception:
            text = None
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        if input_tokens is None:
            input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if output_tokens is None:
            output_tokens = getattr(usage, "completion_tokens", None)
        thinking_tokens = None
        details = getattr(usage, "output_tokens_details", None)
        try:
            thinking_tokens = getattr(details, "reasoning_tokens", None)
        except Exception:
            thinking_tokens = None
        if not text or not str(text).strip():
            incomplete = getattr(response, "incomplete_details", None)
            if incomplete is not None and getattr(incomplete, "reason", None) == "max_output_tokens":
                raise RequestTooLargeError(
                    "the reply hit the output budget (max_output_tokens) and "
                    "arrived truncated",
                    status=400, cut=True,
                )
            raise LLMError(f"openai returned no text (model={self.model})")
        return LLMResult(
            text=str(text),
            input_tokens=int(input_tokens) if isinstance(input_tokens, int) else None,
            output_tokens=int(output_tokens) if isinstance(output_tokens, int) else None,
            thinking_tokens=int(thinking_tokens) if isinstance(thinking_tokens, int) else None,
        )

    def use_responses_api(self, json_output: bool) -> bool:
        """Responses API preferred; Chat Completions is remembered per
        model after a rejection of the Responses call."""
        import time as _time

        return not self._chat_fallback.get(self.model, False)

    def _text_format(self, schema, json_mode, completions: bool = False):
        if json_mode and schema:
            if completions:
                return {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "dubtool_reply",
                        "strict": True,
                        "schema": schema,
                    },
                }
            return {
                "format": {
                    "type": "json_schema",
                    "name": "dubtool_reply",
                    "strict": True,
                    "schema": schema,
                }
            }
        if json_mode:
            return {"format": {"type": "json_object"}}
        return None

    def _switch_on_invalid_key(self, used_key: str) -> None:
        if len(self._keys) > 1:
            self._keys = [k for k in self._keys if k != used_key] or [used_key]
            self._position = 0
            self._clients.pop(used_key, None)
            self._switches = getattr(self, "_switches", 0) + 1
            log.warning(
                "openai: switched to the next key after a rejected key (the "
                "value is never logged)"
            )

    # -------------------------------------------------- error mapping
    def _map_error(self, exc: Exception):
        kind = _transport_kind(exc)
        if kind == "timeout":
            return TimeoutLLMError("the request timed out before a reply (openai)")
        if kind == "network":
            return NetworkLLMError("the connection to openai failed")
        status = getattr(exc, "status_code", None)
        code = _error_code(exc)
        if status == 400:
            if code in _SIZE_CODES:
                return RequestTooLargeError(
                    f"openai rejected the request as too large ({code})",
                    status=400, cut=False,
                )
            # A 400 that names the structured format itself degrades once.
            if code in ("invalid_schema", "invalid_json_schema", "schema_not_supported"):
                return SchemaRejectedError(f"openai rejected the structured format ({code})")
            if _is_internal(exc):
                return InternalLLMError(
                    "an internal error prevented the request (not a connection "
                    f"problem): {exc}"
                )
            return LLMError(f"openai API error 400: {exc}")
        if status in (401, 403):
            return InvalidKeyError(
                f"openai rejected the API key ({status})", key_used=self.current_key()
            )
        if status == 429:
            if code in _QUOTA_CODES:
                return QuotaExhaustedError(
                    "openai account is out of credit or reached its spend/usage "
                    f"limit ({code}) — billing, not a retry problem",
                    reason=str(code),
                )
            if code == "slow_down":
                return RateLimitError(
                    "openai", "asked to slow down (slow_down)", kind="minute",
                    retry_after=_retry_after_of(exc),
                )
            return RateLimitError(
                "openai", "rate limit (requests or tokens per minute)",
                kind="minute", retry_after=_retry_after_of(exc),
            )
        if status == 413:
            return RequestTooLargeError("openai request too large", status=413)
        if isinstance(status, int) and status >= 500:
            return ServerErrorLLMError(
                f"openai server error {status} ({code or 'server error'})",
                status=status,
            )
        if code in _QUOTA_CODES:
            return QuotaExhaustedError(
                f"openai account is out of credit or reached its spend/usage "
                f"limit ({code}) — billing, not a retry problem",
                reason=str(code),
            )
        if _is_internal(exc):
            return InternalLLMError(
                "an internal error prevented the request (not a connection "
                f"problem): {exc}"
            )
        return LLMError(f"openai API error: {exc}")
