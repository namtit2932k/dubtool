"""Anthropic provider via the official `anthropic` SDK.

`POST /v1/messages` with `max_tokens` required; reply text is the
concatenation of the `text` blocks. No assistant prefill. Structured
output rides in `output_config={"format": {"type": "json_schema",
"schema": ...}}` (a stricter beta used the `structured-outputs` header
and `output_format`; the plain body parameter is preferred and the
fallback keeps working when a model only knows the beta form).
`max_retries=0` — the app's own policy owns every retry.

Error mapping uses exception type + status + error code (never message
text) into the shared client classes: 429 honors `retry-after`; 529 /
500 / 502 / 503 are capacity (never split, consider failover); 413 and
stop_reason==max_tokens are request-size (split once); 401/403 are
invalid keys (rotation allowed — Anthropic key rejection is per key);
a body saying the credit balance is too low is quota exhausted (the
ONE message-based rule; it raises QuotaExhausted — never retried,
never rotated, because the limit belongs to the org).
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
    QuotaExhaustedError,
    RateLimitError,
    RequestTooLargeError,
    SchemaRejectedError,
    ServerErrorLLMError,
    TimeoutLLMError,
    _is_internal,
    _transport_kind,
    looks_like_placeholder,
)

log = logging.getLogger("dubtool")


def _keys_from_env() -> list[str]:
    out: list[str] = []
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        value = _read(name)
        if value and not looks_like_placeholder(value) and value not in out:
            out.append(value)
    i = 2
    while True:
        value = _read(f"ANTHROPIC_API_KEY_{i}")
        if not value:
            break
        if not looks_like_placeholder(value) and value not in out:
            out.append(value)
        i += 1
    i = 2
    while True:
        value = _read(f"CLAUDE_API_KEY_{i}")
        if not value:
            break
        if not looks_like_placeholder(value) and value not in out:
            out.append(value)
        i += 1
    return out


def _read(name: str) -> str:
    from dubtool.llm.base import read_env_key

    return read_env_key(name)


def _error_type(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        code = err.get("code") or err.get("type")
        if isinstance(code, str):
            return code
    return None


def _retry_after_of(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
    if headers is None:
        return None
    for name in ("retry-after", "Retry-After"):
        try:
            value = headers.get(name)
        except Exception:
            value = None
        if value:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


class AnthropicProvider:
    name = "anthropic"
    structured = "json_schema"

    def __init__(self, model: str, api_keys: list[str] | None = None, *,
                 timeout_s: float | None = None,
                 max_tokens: int | None = None,
                 mock_transport=None):
        self.model = model
        if api_keys is None:
            api_keys = _keys_from_env()
        self._keys = [k for k in (api_keys or []) if k and not looks_like_placeholder(k)]
        if not self._keys:
            raise MissingKeyError(
                "No Anthropic API key. In the app set it in Settings → API keys "
                "(stored in Windows Credential Manager); for the CLI put "
                "ANTHROPIC_API_KEY in .env (Anthropic Console, paid per token; "
                "a Claude subscription does not include an API key)."
            )
        self._lock = threading.Lock()
        self._clients: dict[str, object] = {}
        self._transport = mock_transport
        self._timeout_s = float(timeout_s) if timeout_s else 180.0
        self.default_max_tokens = int(max_tokens or 8000)
        self.paid_requests = 0
        self.paid_request_cap = 0
        self._switches = 0

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def api_key(self) -> str:
        return self._keys[0]

    @property
    def switches(self) -> int:
        return self._switches

    def _client_for(self, key: str):
        from anthropic import Anthropic

        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = Anthropic(
                    api_key=key,
                    timeout=max(1.0, float(self._timeout_s)),
                    max_retries=0,
                    http_client=self._transport,
                )
                self._clients[key] = client
            return client

    def list_models(self) -> list[str]:
        """The Models API (`GET /v1/models`) with the current key — free,
        used by the key test and Settings → Refresh model list."""
        try:
            page = self._client_for(self._keys[0]).models.list()
            ids = [m.id for m in page.data]
            return sorted(str(i) for i in ids if i)
        except Exception as exc:
            raise self._map_error(exc) from exc

    # ---------------------------------------------------------- complete
    def complete(self, system: str, user: str, *,
                 schema: dict | None = None, json_mode: bool = True,
                 max_tokens: int = 8000) -> LLMResult:
        if self.paid_request_cap and self.paid_requests >= self.paid_request_cap:
            raise QuotaExhaustedError(
                f"the paid-request cap is reached ({self.paid_requests} requests) "
                f"for {self.model} — the rest of the run uses the fallback"
            )
        budget = max(self.default_max_tokens, int(max_tokens or 8000))
        reply = self._complete_once(system, user, schema=json_mode and schema,
                                    json_mode=json_mode, max_tokens=budget)
        self.paid_requests += 1
        return reply

    def _complete_once(self, system: str, user: str, *, schema: dict | None,
                       json_mode: bool, max_tokens: int) -> LLMResult:
        key = self._keys[0]
        tries_endpoints = 0
        while True:
            try:
                kwargs: dict = dict(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                if json_mode and schema:
                    kwargs["output_config"] = {
                        "format": {"type": "json_schema", "schema": schema}
                    }
                    if self._beta_header_needed:
                        kwargs.pop("output_config")
                        kwargs["betas"] = ["structured-outputs-2025-11-13"]
                        kwargs["output_format"] = {
                            "format": {"type": "json_schema", "schema": schema}
                        }
                response = self._client_for(key).messages.create(**kwargs)
                break
            except Exception as exc:
                if (
                    not self._beta_header_needed
                    and json_mode and schema
                    and getattr(exc, "status_code", None) == 400
                    and _error_type(exc) == "invalid_request_error"
                ):
                    # The plain output_config form was rejected by a model
                    # that only knows the beta header — recorded one step.
                    log.warning(
                        "anthropic: plain output_config rejected by %s — "
                        "trying the beta form", self.model,
                    )
                    self._beta_header_needed = True
                    tries_endpoints += 1
                    if tries_endpoints < 3:
                        continue
                mapped = self._map_error(exc)
                if isinstance(mapped, InvalidKeyError):
                    self._switch_on_invalid_key(key)
                raise mapped from exc

        parts = []
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", ""))
        text = "\n".join(p for p in parts if p).strip()
        if getattr(response, "stop_reason", None) == "max_tokens":
            # A reply cut by the output budget: one internal retry with a
            # higher cap when the cut was early, else raise the size error.
            if getattr(self, "_size_retry_once", None) is not True and text:
                self._size_retry_once = True
                raised = min(int(max_tokens * 2), self.default_max_tokens * 4)
                return self._complete_once(
                    system, user, schema=schema, json_mode=json_mode, max_tokens=raised
                )
            raise RequestTooLargeError(
                "the reply was cut by the output budget (stop_reason=max_tokens)",
                status=400, cut=True,
            )
        if not text:
            raise LLMError(f"anthropic returned no text (model={self.model})")
        usage = getattr(response, "usage", None)
        return LLMResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    def _switch_on_invalid_key(self, used_key: str) -> None:
        if len(self._keys) > 1:
            self._keys = [k for k in self._keys if k != used_key] or [used_key]
            self._clients.pop(used_key, None)
            self._switches += 1
            log.warning(
                "anthropic: switched to the next key after a rejected key (the "
                "value is never logged)"
            )

    _beta_header_needed = False

    # -------------------------------------------------- error mapping
    def _map_error(self, exc: Exception):
        kind = _transport_kind(exc)
        if kind == "timeout":
            return TimeoutLLMError("the request timed out before a reply (anthropic)")
        if kind == "network":
            return NetworkLLMError("the connection to anthropic failed")
        status = getattr(exc, "status_code", None)
        code = _error_type(exc)
        if status == 400:
            if self._body_mentions_low_credit(exc):
                return QuotaExhaustedError(
                    "anthropic says the credit balance is too low for this "
                    "request — billing, not a retry problem",
                    reason="low_credit_balance",
                )
            if code and "schema" in str(code):
                return SchemaRejectedError(f"anthropic rejected the structured format ({code})")
            if _is_internal(exc):
                return InternalLLMError(
                    "an internal error prevented the request (not a connection "
                    f"problem): {exc}"
                )
            return LLMError(f"anthropic API error 400 ({code}): {exc}")
        if status in (401, 403):
            return InvalidKeyError(
                f"anthropic rejected the API key ({status})", key_used=self._keys[0]
            )
        if status == 429:
            return RateLimitError(
                "anthropic", "rate limit (requests or tokens per minute)",
                kind="minute", retry_after=_retry_after_of(exc),
            )
        if status == 413:
            return RequestTooLargeError("anthropic request too large", status=413)
        if status in (500, 502, 503) or getattr(exc, "type", None) == "overloaded_error":
            return ServerErrorLLMError(
                f"anthropic {status or 529} {'overloaded error' if status == 529 else 'server error'}",
                status=status or 529,
            )
        if self._body_mentions_low_credit(exc):
            return QuotaExhaustedError(
                "anthropic says the credit balance is too low — billing, not a "
                "retry problem",
                reason="low_credit_balance",
            )
        if _is_internal(exc):
            return InternalLLMError(
                "an internal error prevented the request (not a connection "
                f"problem): {exc}"
            )
        return LLMError(f"anthropic API error: {exc}")

    def _body_mentions_low_credit(self, exc: Exception) -> bool:
        """The ONE message-text rule of the table: only the low-credit
        body is identified by text (verified against a real 400 body in
        the tests)."""
        text = str(exc).lower()
        return "credit balance is too low" in text or "credit balance" in text and "low" in text
