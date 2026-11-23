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

import json
import logging
import re
import threading
import time

from dubtool.llm.base import (
    InternalLLMError,
    InvalidKeyError,
    LLMError,
    LLMResult,
    MissingKeyError,
    NetworkLLMError,
    QuotaExhaustedError,
    RateLimitError,
    RateLimitExhausted,
    RequestTooLargeError,
    ServerErrorLLMError,
    TimeoutLLMError,
    _is_internal,
    _transport_kind,
    looks_like_placeholder,
    read_env_key,
)
from dubtool.llm.keypool import KeyPool
from dubtool.llm.openai import _error_code, _parse_timespan, _retry_after_of

log = logging.getLogger("dubtool")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Groq Free Tier allows 8,000 TPM per (key, model); trimming duplicated
# boilerplate from system + user prompts cuts per-chunk input by ~65% while
# preserving every translation rule, brief, context, glossary and pronoun pick.
_COMPACT_TRANSLATE_SYSTEM = (
    "Translate video subtitles into natural, neutral Vietnamese for dubbing. "
    "Rules: faithful meaning, no dropped clauses, no added facts, no slang "
    "unless in source; keep subjects and pronoun choices consistent (avoid "
    "literary 'ta' unless brief asks); apply glossary exactly; treat max_chars "
    "as a soft guide (cut filler first, never meaning); complete sentences with "
    "matching punctuation; reply ONLY with JSON object: "
    '{"items":[{"id":<int>,"vi":"<str>"}]}.'
)
_GROQ_MAX_OUTPUT_CAP = 950
_GROQ_MIN_REMAINING_TOKENS = 1800

_USER_LANG_RE = re.compile(r"Translate these fragments from ([^\n.]+) into Vietnamese", re.IGNORECASE)
_USER_FIRST_RE = re.compile(r"- First person[^\n:]*:\s*(.+)")
_USER_SECOND_RE = re.compile(r"- Second person[^\n:]*:\s*(.+)")


def _compact_user_prompt(user: str) -> str:
    """Trim duplicated rules & pretty-printed JSON whitespace from
    ``render_translate_user`` for Groq's 8,000 TPM budget, preserving brief,
    non-empty context, non-empty glossary, pronouns, source language, segments,
    and any retry validation note."""
    if not user or "\nFragments:\n" not in user:
        return user
    head, _, tail = user.partition("\nFragments:\n")
    # Preserve any validation retry note appended after the JSON block.
    retry_note = ""
    seg_raw = tail
    marker = "\n\nYour previous reply was invalid:"
    if marker in tail:
        seg_raw, _, note_rest = tail.partition(marker)
        retry_note = "\nFix: " + note_rest.strip()

    try:
        segs = json.loads(seg_raw.strip())
    except ValueError:
        return user
    if not isinstance(segs, list):
        return user

    parts: list[str] = []
    # Optional Video brief
    m_brief = re.search(
        r"Video brief \(context only[^\n]*\):\n(.*?)\n\nContext from earlier",
        head,
        flags=re.DOTALL,
    )
    if m_brief and m_brief.group(1).strip():
        parts.append("Brief: " + m_brief.group(1).strip())

    # Optional non-empty context (compress to previous Vietnamese lines only)
    m_ctx = re.search(
        r"Context from earlier[^\n]*:\n(.*?)\n\nGlossary terms",
        head,
        flags=re.DOTALL,
    )
    if m_ctx:
        try:
            ctx = json.loads(m_ctx.group(1).strip())
            if isinstance(ctx, list) and ctx:
                prev_lines = [
                    str(c.get("vi") or c.get("text") or "").strip()
                    for c in ctx[-2:]
                    if isinstance(c, dict)
                ]
                prev_lines = [p for p in prev_lines if p]
                if prev_lines:
                    parts.append("Prev (context only, do not output): " + " | ".join(prev_lines))
        except ValueError:
            pass

    # Optional non-empty glossary
    m_gloss = re.search(
        r"Glossary terms[^\n]*:\n(.*?)\n\n",
        head,
        flags=re.DOTALL,
    )
    if m_gloss:
        try:
            gloss = json.loads(m_gloss.group(1).strip())
            if isinstance(gloss, dict) and gloss:
                parts.append(
                    "Glossary: " + json.dumps(gloss, ensure_ascii=False, separators=(",", ":"))
                )
        except ValueError:
            pass

    # Compact pronoun pick
    m_first = _USER_FIRST_RE.search(head)
    m_second = _USER_SECOND_RE.search(head)
    if m_first or m_second:
        p1 = m_first.group(1).strip() if m_first else "tôi"
        p2 = m_second.group(1).strip() if m_second else "bạn / các bạn"
        parts.append(f"Pronouns: 1st={p1}; 2nd={p2}")

    # Source language + compact JSON segments
    m_lang = _USER_LANG_RE.search(head)
    src_lang = m_lang.group(1).strip() if m_lang else "source language"
    compact_segs = json.dumps(segs, ensure_ascii=False, separators=(",", ":"))
    parts.append(f"Translate from {src_lang} to Vietnamese. Return exact ids in order:\n{compact_segs}")
    if retry_note:
        parts.append(retry_note.strip())
    return "\n".join(parts)

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
        self._clients: dict[str, object] = {}
        self._base_url = base_url
        self._cancel = cancel
        self._transport = mock_transport
        self._timeout_s = float(timeout_s) if timeout_s else 60.0
        self.default_max_output_tokens = int(max_output_tokens or _GROQ_MAX_OUTPUT_CAP)
        self._json_mode_degraded: set[str] = set()
        self._reasoning_opt_degraded: set[str] = set()
        self.pool = KeyPool(
            self._keys,
            cooldown_s=30.0,
            max_wait_s=120.0,
            max_total_wait_s=900.0,
            cancel=cancel,
            model=model,
        )

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def api_key(self) -> str:
        return self.pool.acquire()

    @property
    def switches(self) -> int:
        return self.pool.switches

    def current_key(self) -> str:
        return self.pool.acquire()

    def handle_rate_limit(self, exc: RateLimitError, used_key: str) -> str:
        """Mark the 429 against ``used_key`` in the KeyPool so multiple Groq
        keys rotate immediately on per-minute TPM limits (zero wait)."""
        kind = getattr(exc, "kind", None) or "minute"
        retry_after = getattr(exc, "retry_after", None)
        if kind == "day":
            until_epoch = time.time() + min(float(retry_after or 86400.0), 86400.0)
            self.pool.mark_daily_limit(used_key, until_epoch=until_epoch)
        else:
            cooldown = (float(retry_after) + 1.5) if retry_after is not None else 30.0
            self.pool.mark_rate_limited(used_key, cooldown)
        return self._next_key_after_failure("daily" if kind == "day" else "per_minute")

    def handle_invalid_key(self, used_key: str) -> str:
        self.pool.mark_invalid(used_key)
        return self._next_key_after_failure("invalid")

    def _next_key_after_failure(self, kind: str) -> str:
        try:
            self.pool.acquire()
        except Exception:
            if kind == "invalid":
                return "exhausted"
            return "fallback" if kind == "daily" else "wait"
        self.pool.switches += 1
        log.warning(
            "groq keypool: switched to next available key (previous key %s)",
            "invalid" if kind == "invalid" else "rate limited",
        )
        return "switched"

    def wait_for_key(self) -> bool:
        return self.pool.wait_for_key()

    def raise_exhausted(self) -> None:
        tried = self.pool.failed_count()
        if self.pool.all_daily_exhausted():
            raise RateLimitExhausted(
                f"all {self.pool.keys_count()} Groq API key(s) have used up their "
                f"free-tier daily quota for {self.model}."
            )
        raise RateLimitExhausted(
            f"giving up: {self.pool.keys_count()} Groq API key(s) failed or were "
            f"rate limited ({tried} permanently out); stages are resumable."
        )

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
        """Keep Groq max_tokens safely under Groq Free Tier's 1,000 OTPM cap."""
        cap = min(int(requested or _GROQ_MAX_OUTPUT_CAP), self.default_max_output_tokens, _GROQ_MAX_OUTPUT_CAP)
        estimated = max(700, int(len(user or "") * 1.0))
        return max(512, min(cap, estimated))

    def _update_pace_from_headers(self, headers, used_key: str) -> None:
        """When this key's remaining TPM drops below threshold, mark ONLY this
        key cooling down in the KeyPool: with 2+ Groq keys the next chunk
        instantly switches to another key with zero wait; with 1 key the pool
        sleeps just until the rolling TPM window resets."""
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
                log.info(
                    "groq/%s: key #%d low TPM (%d left) — cooling down %.1f s",
                    self.model, self.pool.position(used_key), remaining, reset_s + 0.8,
                )
                self.pool.mark_rate_limited(used_key, min(reset_s + 0.8, 65.0))
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
        compact_user = _compact_user_prompt(user)
        budget = self._compute_max_tokens(compact_user, max_tokens)
        key = self.pool.acquire()
        try:
            return self._call_chat(key, system, compact_user, json_mode=json_mode, max_tokens=budget)
        except LLMError:
            raise
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            code = _error_code(exc)
            low = str(exc).lower()
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
                    return self._call_chat(key, system, compact_user, json_mode=False, max_tokens=budget)
                except LLMError:
                    raise
                except Exception as exc2:
                    raise self._map_error(exc2, key) from exc2
            if (
                status == 400
                and "reasoning_effort" in low
                and self.model not in self._reasoning_opt_degraded
            ):
                self._reasoning_opt_degraded.add(self.model)
                try:
                    return self._call_chat(key, system, compact_user, json_mode=json_mode, max_tokens=budget)
                except LLMError:
                    raise
                except Exception as exc2:
                    raise self._map_error(exc2, key) from exc2
            raise self._map_error(exc, key) from exc

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
            self._update_pace_from_headers(getattr(raw_resp, "headers", None), key)
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

    def _map_error(self, exc: Exception, used_key: str = ""):
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
                f"groq rejected the API key ({status})", key_used=used_key or self.pool.values()[0]
            )
        if status == 404:
            return InternalLLMError(f"groq model not found (404): {exc}")
        if status == 413:
            return RequestTooLargeError("groq request too large (413)", status=413)
        if status == 429:
            # Groq Free Tier can reject a single request whose max_tokens exceeds
            # the model's OTPM ceiling ("Request too large ... on output tokens per minute").
            if "request too large" in low or "reduce max_tokens" in low:
                return RequestTooLargeError(
                    f"groq output budget exceeded per-minute ceiling ({exc})",
                    status=400,
                    cut=False,
                )
            is_minute_msg = any(
                tok in low for tok in ("per minute", "tpm", "otpm", "rpm")
            )
            is_day = (not is_minute_msg) and any(
                tok in low for tok in ("per day", "rpd", "tokens per day", "tpd")
            )
            retry_after = self._groq_retry_after(exc, prefer_tokens=is_minute_msg)
            return RateLimitError(
                "groq",
                "daily quota reached on Groq" if is_day else "rate limit (requests or tokens per minute)",
                kind="day" if is_day else "minute",
                retry_after=retry_after,
                key_used=used_key or self.pool.values()[0],
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

    @staticmethod
    def _groq_retry_after(exc: Exception, *, prefer_tokens: bool = True) -> float | None:
        """Read Retry-After first; on per-minute TPM/OTPM 429s, prefer
        x-ratelimit-reset-tokens rather than x-ratelimit-reset-requests (which
        on Groq reflects the 24-hour RPD bucket and can be >600s)."""
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
        if headers is not None:
            for key in ("retry-after", "Retry-After"):
                try:
                    val = headers.get(key)
                except Exception:
                    val = None
                if val:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        pass
            if prefer_tokens:
                try:
                    val = headers.get("x-ratelimit-reset-tokens")
                except Exception:
                    val = None
                parsed = _parse_timespan(val)
                if parsed is not None:
                    return parsed
        # Also parse "Please try again in 12.34s" from Groq's error text if present.
        m = re.search(r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|s|m)", str(exc), re.IGNORECASE)
        if m:
            num, unit = float(m.group(1)), m.group(2).lower()
            if unit == "ms":
                return max(0.5, num / 1000.0)
            if unit == "m":
                return num * 60.0
            return num
        return _retry_after_of(exc) if not prefer_tokens else None
