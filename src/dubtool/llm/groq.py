"""Groq Cloud provider via the bundled OpenAI-compatible SDK.

Groq exposes an OpenAI-compatible Chat Completions endpoint at
``https://api.groq.com/openai/v1`` with a generous Free Tier and very low
latency (rarely 503). Key details handled here:

- Uses Chat Completions (never the OpenAI Responses API).
- ``json_object`` mode on Groq requires a JSON object root (``{"items": [...]}``
  or ``{"vi": "..."}``); when ``json_mode=True`` we append a concise object-root
  reminder and still tolerate a one-time fallback to plain JSON prompting if a
  model returns HTTP 400 ``json_validate_failed``.
- Strips ``<think>...</think>`` blocks cleanly and passes
  ``reasoning_effort="none"`` for Qwen3 models so translation stays fast.
- Maps ``finish_reason == "length"`` to ``RequestTooLargeError(cut=True)`` so
  oversized chunks split automatically instead of failing validation.
- Honors ``Retry-After`` and ``x-ratelimit-reset-*`` headers on 429 and
  distinguishes daily (RPD) vs per-minute (RPM/TPM) limits.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from dubtool import heartbeat
from dubtool.job import StageCancelled
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
    ServerErrorLLMError,
    TimeoutLLMError,
    _is_internal,
    _transport_kind,
    looks_like_placeholder,
    read_env_key,
)
from dubtool.llm.openai import _error_code, _parse_timespan, _retry_after_of

log = logging.getLogger("dubtool")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Groq Free Tier allows 8,000 TPM per model; trimming duplicated boilerplate from
# the ~800-token TRANSLATE_SYSTEM saves ~650 tokens on every single chunk while
# keeping every translation rule intact.
_COMPACT_TRANSLATE_SYSTEM = (
    "You are a professional translator for video dubbing into natural, neutral "
    "Vietnamese. Rules: translate meaning faithfully (no dropped clauses, no "
    "added facts, no slang unless in source); keep subjects/pronouns clear and "
    "consistent with the user's pronoun choices (avoid literary 'ta' unless "
    "asked); apply glossary terms exactly; treat 'max_chars' as a soft guide "
    "(trim filler first, never drop meaning); write complete sentences with "
    "matching punctuation; reply ONLY with a valid JSON object: "
    '{"items": [{"id": <int>, "vi": "<str>"}, ...]}.'
)
_GROQ_MAX_OUTPUT_CAP = 2400
_GROQ_MIN_REMAINING_TOKENS = 2400

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_SIZE_CODES = {"context_length_exceeded", "context_token_exceeded", "string_above_max_length"}
_NON_CHAT_MODEL_FRAGMENTS = (
    "whisper",
    "tts",
    "playai",
    "guard",
    "safeguard",
    "orpheus",
    "allam",
)


def is_groq_chat_model(model_id: str) -> bool:
    """Filter out ASR/TTS/guard models from Groq's /models list."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    return not any(tok in mid for tok in _NON_CHAT_MODEL_FRAGMENTS)


def _strip_think_tags(text: str) -> str:
    if not text:
        return text
    cleaned = _THINK_TAG_RE.sub("", text)
    return cleaned.strip()


def _keys_from_env() -> list[str]:
    out: list[str] = []
    first = read_env_key("GROQ_API_KEY")
    if first and not looks_like_placeholder(first):
        out.append(first)
    i = 2
    while True:
        val = read_env_key(f"GROQ_API_KEY_{i}")
        if not val:
            break
        if not looks_like_placeholder(val) and val not in out:
            out.append(val)
        i += 1
    return out


class GroqProvider:
    name = "groq"
    structured = "json_object"

    def __init__(
        self,
        model: str,
        api_keys: list[str] | None = None,
        *,
        timeout_s: float | None = None,
        max_output_tokens: int | None = None,
        base_url: str = GROQ_BASE_URL,
        cancel=None,
        mock_transport=None,
    ):
        self.model = model
        if api_keys is None:
            api_keys = _keys_from_env()
        self._keys = [k for k in (api_keys or []) if k and not looks_like_placeholder(k)]
        if not self._keys:
            raise MissingKeyError(
                "No Groq API key. In the app set it in Settings → API keys → Groq "
                "(free at console.groq.com); for the CLI put GROQ_API_KEY in .env."
            )
        self._lock = threading.Lock()
        self._position = 0
        self._clients: dict[str, object] = {}
        self._base_url = base_url
        self._cancel = cancel
        self._transport = mock_transport
        self._timeout_s = float(timeout_s) if timeout_s else 60.0
        self.default_max_output_tokens = int(max_output_tokens or _GROQ_MAX_OUTPUT_CAP)
        self._json_mode_degraded: set[str] = set()
        self._reasoning_opt_degraded: set[str] = set()
        self._pace_until: float = 0.0

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
        from openai import OpenAI

        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = OpenAI(
                    api_key=key,
                    base_url=self._base_url,
                    timeout=max(1.0, float(self._timeout_s)),
                    max_retries=0,
                    http_client=self._transport,
                )
                self._clients[key] = client
            return client

    def list_models(self) -> list[str]:
        try:
            page = self._client_for(self.current_key()).models.list()
            return sorted(
                str(m.id) for m in (page.data or [])
                if getattr(m, "id", None) and is_groq_chat_model(str(m.id))
            )
        except Exception as exc:
            raise self._map_error(exc) from exc

    def _system_with_json_object_hint(self, system: str, json_mode: bool) -> str:
        # Replace the verbose ~800-token default translation prompt with the
        # compact equivalent to preserve Groq's 8,000 TPM budget.
        base = system or ""
        if len(base) > 1200 and "professional translator" in base.lower() and "vietnamese" in base.lower():
            return _COMPACT_TRANSLATE_SYSTEM
        if not json_mode or self.model in self._json_mode_degraded:
            return base
        hint = (
            '\n\nOutput requirement: Reply with a single valid JSON object. '
            'For batch lists, wrap the array under the "items" key: '
            '{"items": [{"id": <int>, "vi": "<str>"}, ...]}.'
        )
        return base + hint

    def _compute_max_tokens(self, user: str, requested: int) -> int:
        """Keep Groq max_tokens proportional to chunk size so Groq's TPM
        reservation never blocks a 20-unit chunk against the 8,000 TPM cap."""
        cap = min(int(requested or _GROQ_MAX_OUTPUT_CAP), self.default_max_output_tokens, _GROQ_MAX_OUTPUT_CAP)
        estimated = max(1024, int(len(user or "") * 0.9))
        return max(512, min(cap, estimated))

    def _wait_for_pace(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise StageCancelled("groq: cancelled by user")
        wait_s = self._pace_until - time.monotonic()
        if wait_s <= 0:
            return
        wait_s = min(wait_s, 65.0)
        log.info("groq/%s: proactive TPM pacing %.1f s before next chunk", self.model, wait_s)

        def _pace_msg(rem: float) -> str:
            try:
                from dubtool.app import i18n

                return i18n.tr("llm.rate_pause", n=max(1, int(rem + 0.999)))
            except Exception:
                return f"waiting {int(rem)} s for Groq TPM window"

        heartbeat.sleeping(wait_s, _pace_msg, self._cancel)
        if self._cancel is not None and self._cancel.is_set():
            raise StageCancelled("groq: cancelled by user")

    def _update_pace_from_headers(self, headers) -> None:
        if headers is None:
            return
        try:
            rem_raw = headers.get("x-ratelimit-remaining-tokens")
            reset_raw = headers.get("x-ratelimit-reset-tokens")
            if rem_raw is None or reset_raw is None:
                return
            remaining = int(float(rem_raw))
            reset_s = _parse_timespan(str(reset_raw))
            if reset_s is None:
                try:
                    reset_s = float(reset_raw)
                except (TypeError, ValueError):
                    return
            if remaining < _GROQ_MIN_REMAINING_TOKENS and reset_s > 0:
                self._pace_until = max(self._pace_until, time.monotonic() + reset_s + 0.8)
        except Exception:
            pass

    def complete(
        self,
        system: str,
        user: str,
        *,
        schema: dict | None = None,
        json_mode: bool = True,
        max_tokens: int = 8000,
    ) -> LLMResult:
        self._wait_for_pace()
        budget = self._compute_max_tokens(user, max_tokens)
        key = self.current_key()
        try:
            return self._call_chat(key, system, user, json_mode=json_mode, max_tokens=budget)
        except LLMError:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            code = _error_code(exc)
            low = str(exc).lower()
            # If Groq's strict json_object validator rejects a reply (e.g. model
            # tried to emit a bare array or minor whitespace), retry once without
            # response_format — parsing.py still validates the JSON strictly.
            if (
                status == 400
                and json_mode
                and self.model not in self._json_mode_degraded
                and (code == "json_validate_failed" or "json_validate_failed" in low or "response_format" in low)
            ):
                log.warning(
                    "groq/%s returned json_validate_failed — retrying with plain JSON prompt",
                    self.model,
                )
                self._json_mode_degraded.add(self.model)
                try:
                    return self._call_chat(key, system, user, json_mode=False, max_tokens=budget)
                except LLMError:
                    raise
                except Exception as exc2:
                    mapped2 = self._map_error(exc2)
                    if isinstance(mapped2, InvalidKeyError):
                        self._switch_on_invalid_key(key)
                    raise mapped2 from exc2
            if (
                status == 400
                and "reasoning_effort" in low
                and self.model not in self._reasoning_opt_degraded
            ):
                self._reasoning_opt_degraded.add(self.model)
                try:
                    return self._call_chat(key, system, user, json_mode=json_mode, max_tokens=budget)
                except LLMError:
                    raise
                except Exception as exc2:
                    mapped2 = self._map_error(exc2)
                    if isinstance(mapped2, InvalidKeyError):
                        self._switch_on_invalid_key(key)
                    raise mapped2 from exc2
            mapped = self._map_error(exc)
            if isinstance(mapped, InvalidKeyError):
                self._switch_on_invalid_key(key)
            raise mapped from exc

    def _call_chat(
        self,
        key: str,
        system: str,
        user: str,
        *,
        json_mode: bool,
        max_tokens: int,
    ) -> LLMResult:
        use_json_obj = bool(json_mode and self.model not in self._json_mode_degraded)
        sys_text = self._system_with_json_object_hint(system, use_json_obj)
        kwargs: dict = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": sys_text},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        if use_json_obj:
            kwargs["response_format"] = {"type": "json_object"}
        # Keep reasoning minimal/off on Groq models so translation stays fast &
        # doesn't burn Free Tier TPM on internal chain-of-thought.
        mid_low = self.model.lower()
        if self.model not in self._reasoning_opt_degraded:
            if "qwen3" in mid_low:
                kwargs["extra_body"] = {"reasoning_effort": "none"}
            elif "gpt-oss" in mid_low:
                kwargs["extra_body"] = {"reasoning_effort": "low"}

        completions = self._client_for(key).chat.completions
        raw_api = getattr(completions, "with_raw_response", None)
        if raw_api is not None and callable(getattr(raw_api, "create", None)):
            raw_resp = raw_api.create(**kwargs)
            self._update_pace_from_headers(getattr(raw_resp, "headers", None))
            response = raw_resp.parse()
        else:
            response = completions.create(**kwargs)
        choice = response.choices[0] if getattr(response, "choices", None) else None
        finish_reason = getattr(choice, "finish_reason", None) if choice is not None else None
        if finish_reason == "length":
            raise RequestTooLargeError(
                "the reply hit the output budget (finish_reason=length) and arrived truncated",
                status=400,
                cut=True,
            )
        raw_text = (
            getattr(getattr(choice, "message", None), "content", None)
            if choice is not None
            else None
        )
        text = _strip_think_tags(str(raw_text or ""))
        if not text:
            raise LLMError(f"groq returned no text (model={self.model})")

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        return LLMResult(
            text=text,
            input_tokens=int(input_tokens) if isinstance(input_tokens, int) else None,
            output_tokens=int(output_tokens) if isinstance(output_tokens, int) else None,
        )

    def _switch_on_invalid_key(self, used_key: str) -> None:
        if len(self._keys) > 1:
            self._keys = [k for k in self._keys if k != used_key] or [used_key]
            self._position = 0
            self._clients.pop(used_key, None)
            self._switches = getattr(self, "_switches", 0) + 1
            log.warning(
                "groq: switched to the next key after a rejected key (the value is never logged)"
            )

    def _map_error(self, exc: Exception):
        kind = _transport_kind(exc)
        if kind == "timeout":
            return TimeoutLLMError("the request timed out before a reply (groq)")
        if kind == "network":
            return NetworkLLMError("the connection to groq failed")
        status = getattr(exc, "status_code", None)
        code = _error_code(exc)
        low = str(exc).lower()
        if status == 400:
            if code in _SIZE_CODES or "context length" in low or "maximum context" in low:
                return RequestTooLargeError(
                    f"groq rejected the request as too large ({code or '400'})",
                    status=400,
                    cut=False,
                )
            if _is_internal(exc):
                return InternalLLMError(
                    f"an internal error prevented the request (not a connection problem): {exc}"
                )
            return LLMError(f"groq API error 400: {exc}")
        if status in (401, 403):
            return InvalidKeyError(
                f"groq rejected the API key ({status})", key_used=self.current_key()
            )
        if status == 404:
            return InternalLLMError(f"groq model not found (404): {exc}")
        if status == 413:
            return RequestTooLargeError("groq request too large (413)", status=413)
        if status == 429:
            retry_after = _retry_after_of(exc)
            is_day = (
                "per day" in low
                or "rpd" in low
                or "tokens per day" in low
                or "tpd" in low
                or (retry_after is not None and retry_after > 600.0)
            )
            return RateLimitError(
                "groq",
                "daily quota reached on Groq" if is_day else "rate limit (requests or tokens per minute)",
                kind="day" if is_day else "minute",
                retry_after=retry_after,
            )
        if isinstance(status, int) and status >= 500:
            return ServerErrorLLMError(
                f"groq server error {status} ({code or 'server error'})",
                status=status,
            )
        if code in ("insufficient_quota", "billing_not_active"):
            return QuotaExhaustedError(f"groq quota exhausted ({code})", reason=str(code))
        if _is_internal(exc):
            return InternalLLMError(
                f"an internal error prevented the request (not a connection problem): {exc}"
            )
        return LLMError(f"groq API error: {exc}")
