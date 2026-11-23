"""Auto-Rotate Free-Tier LLM Provider (`auto-rotate`).

Combines ALL saved free-tier API keys across Gemini and Groq into a single
tiered round-robin pool so long videos finish fast without getting stuck on
Gemini 503 bursts or Groq 1,000 OTPM waits:

- Tier 1 (interleaved round-robin across providers and keys):
  * Gemini Flash-Lite models (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`,
    `gemini-flash-lite-latest`) across every saved Gemini key.
  * Groq `qwen/qwen3.8-27b` across every saved Groq key.
- Tier 2 (overflow only when every Tier 1 slot is temporarily cooling down):
  * Groq `openai/gpt-oss-120b` and `openai/gpt-oss-20b` across every saved Groq key.

Never touches paid providers (OpenAI / Anthropic) so users are never billed
unexpectedly. Thread-safe for parallel translation workers (`concurrency=2`).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from dubtool import heartbeat
from dubtool.job import StageCancelled
from dubtool.llm.base import (
    InvalidKeyError,
    KeyExhaustedError,
    LLMError,
    LLMResult,
    MissingKeyError,
    NetworkLLMError,
    RateLimitError,
    RateLimitExhausted,
    RequestTooLargeError,
    SchemaRejectedError,
    ServerErrorLLMError,
    TimeoutLLMError,
)

log = logging.getLogger("dubtool")

AUTO_ROTATE_MODEL_ID = "auto-rotate"

_GEMINI_TIER1_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
)
_GROQ_TIER1_MODELS = ("qwen/qwen3.8-27b",)
# Note: Groq's gpt-oss-120b / gpt-oss-20b are reasoning models that consume
# 400-600 tokens of internal reasoning before emitting JSON, causing repeated
# finish_reason=length truncations under Groq Free Tier's 1,000 OTPM cap.
# Waiting a few seconds for a clean Tier-1 slot (Gemini or qwen3.8-27b) is
# both faster and far more reliable than overflowing into reasoning models.
_GROQ_TIER2_MODELS: tuple[str, ...] = ()

_MODEL_503_COOLDOWN_S = 45.0
_MODEL_TIMEOUT_COOLDOWN_S = 30.0
_DEFAULT_MINUTE_429_COOLDOWN_S = 25.0
_MAX_TOTAL_WAIT_PER_CALL_S = 600.0


@dataclass
class _Slot:
    provider: str        # "gemini" | "groq"
    model: str
    key: str             # never logged
    key_pos: int         # 1-based position within its provider
    tier: int            # 1 (preferred) | 2 (overflow)
    cooldown_until: float = 0.0
    dead: bool = False   # invalid key or daily quota exhausted

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model} (key #{self.key_pos})"


def _build_interleaved_slots(
    gemini_keys: list[str],
    groq_keys: list[str],
) -> list[_Slot]:
    """Build interleaved Tier-1 slots (alternating Gemini & Groq across all
    keys so consecutive chunks spread load evenly), followed by Tier-2 slots."""
    gemini_t1: list[_Slot] = []
    if gemini_keys:
        # Interleave keys first, then models: (m1,k1), (m2,k2), (m3,k3), ...
        n_keys = len(gemini_keys)
        n_models = len(_GEMINI_TIER1_MODELS)
        for step in range(n_models * n_keys):
            m_idx = step % n_models
            k_idx = (step + (step // n_models)) % n_keys
            gemini_t1.append(
                _Slot(
                    provider="gemini",
                    model=_GEMINI_TIER1_MODELS[m_idx],
                    key=gemini_keys[k_idx],
                    key_pos=k_idx + 1,
                    tier=1,
                )
            )
        # Deduplicate while preserving order (when gcd(n_models, n_keys) > 1).
        seen_gm: set[tuple[str, int]] = set()
        dedup_gm: list[_Slot] = []
        for s in gemini_t1:
            sig = (s.model, s.key_pos)
            if sig not in seen_gm:
                seen_gm.add(sig)
                dedup_gm.append(s)
        for m in _GEMINI_TIER1_MODELS:
            for k_idx, k in enumerate(gemini_keys):
                if (m, k_idx + 1) not in seen_gm:
                    seen_gm.add((m, k_idx + 1))
                    dedup_gm.append(
                        _Slot(provider="gemini", model=m, key=k, key_pos=k_idx + 1, tier=1)
                    )
        gemini_t1 = dedup_gm

    groq_t1: list[_Slot] = [
        _Slot(provider="groq", model=m, key=k, key_pos=k_idx + 1, tier=1)
        for m in _GROQ_TIER1_MODELS
        for k_idx, k in enumerate(groq_keys)
    ]

    # Interleave Gemini & Groq Tier-1 slots 1:1 (repeating the shorter list's
    # pace naturally via round-robin cursor) so both providers share traffic.
    tier1: list[_Slot] = []
    if gemini_t1 and groq_t1:
        gi = qi = 0
        while gi < len(gemini_t1) or qi < len(groq_t1):
            if gi < len(gemini_t1):
                tier1.append(gemini_t1[gi])
                gi += 1
            if qi < len(groq_t1):
                tier1.append(groq_t1[qi])
                qi += 1
    else:
        tier1 = gemini_t1 or groq_t1

    tier2: list[_Slot] = [
        _Slot(provider="groq", model=m, key=k, key_pos=k_idx + 1, tier=2)
        for m in _GROQ_TIER2_MODELS
        for k_idx, k in enumerate(groq_keys)
    ]
    return tier1 + tier2


class AutoRotateProvider:
    """Round-robin free-tier provider spanning all saved Gemini & Groq keys."""

    name = "auto"
    structured = "json_schema"

    def __init__(
        self,
        api_keys: dict[str, list[str]] | None = None,
        *,
        timeout_s: float = 60.0,
        cancel=None,
        clock: Callable[[], float] | None = None,
        provider_factory: Callable[[str, str, str], object] | None = None,
    ):
        from dubtool.llm.base import looks_like_placeholder

        raw = api_keys if isinstance(api_keys, dict) else {}
        g_keys = [k for k in (raw.get("gemini") or []) if k and not looks_like_placeholder(k)]
        q_keys = [k for k in (raw.get("groq") or []) if k and not looks_like_placeholder(k)]
        if not g_keys and not q_keys:
            # CLI fallback: try reading from env vars.
            from dubtool.llm.gemini import GeminiProvider
            from dubtool.llm.groq import _keys_from_env as _groq_env_keys

            g_keys = [k for k in GeminiProvider._keys_from_env() if k and not looks_like_placeholder(k)]
            q_keys = [k for k in _groq_env_keys() if k and not looks_like_placeholder(k)]

        self._slots = _build_interleaved_slots(g_keys, q_keys)
        if not self._slots:
            raise MissingKeyError(
                "No free-tier API key found for Auto-Rotate mode. Add at least one "
                "Gemini or Groq API key in Settings → API keys."
            )
        self._timeout_s = float(timeout_s or 60.0)
        self._cancel = cancel
        self._clock = clock if clock is not None else time.monotonic
        self._provider_factory = provider_factory
        self._lock = threading.Lock()
        self._cursor_t1 = 0
        self._cursor_t2 = 0
        self._inner_cache: dict[tuple[str, str, str], object] = {}
        self._model_cooldowns: dict[tuple[str, str], float] = {}
        self._n_gemini = len(g_keys)
        self._n_groq = len(q_keys)
        self._announced_pool = False
        self.last_slot_label = ""
        self.switches = 0
        self._schema_degraded: set[tuple[str, str]] = set()
        log.info(
            "auto-rotate: initialized with %d Gemini key(s), %d Groq key(s) -> %d total slots",
            len(g_keys), len(q_keys), len(self._slots),
        )

    @property
    def model(self) -> str:
        return AUTO_ROTATE_MODEL_ID

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    def _check_cancel(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise StageCancelled("auto-rotate: cancelled by user")

    def _inner_for(self, slot: _Slot):
        cache_key = (slot.provider, slot.model, slot.key)
        with self._lock:
            inst = self._inner_cache.get(cache_key)
            if inst is not None:
                return inst
        if self._provider_factory is not None:
            inst = self._provider_factory(slot.provider, slot.model, slot.key)
        elif slot.provider == "gemini":
            from dubtool.llm.gemini import GeminiProvider

            inst = GeminiProvider(
                model=slot.model,
                api_keys=[slot.key],
                timeout_s=min(self._timeout_s, 60.0),
                cancel=self._cancel,
            )
        else:
            from dubtool.llm.groq import GroqProvider

            inst = GroqProvider(
                model=slot.model,
                api_keys=[slot.key],
                timeout_s=min(self._timeout_s, 60.0),
                cancel=self._cancel,
            )
        with self._lock:
            self._inner_cache[cache_key] = inst
        return inst

    def _slot_ready_at(self, slot: _Slot) -> float:
        model_cd = self._model_cooldowns.get((slot.provider, slot.model), 0.0)
        return max(slot.cooldown_until, model_cd)

    def _has_ready_slot(self, provider: str) -> bool:
        now = self._clock()
        with self._lock:
            return any(
                (not s.dead) and s.provider == provider and self._slot_ready_at(s) <= now
                for s in self._slots
            )

    def _pick_slot(self) -> tuple[_Slot | None, float]:
        """Pick the next ready slot (Tier 1 first, then Tier 2), or return
        ``(None, wait_seconds)`` if every live slot is cooling down."""
        now = self._clock()
        with self._lock:
            live = [s for s in self._slots if not s.dead]
            if not live:
                return None, -1.0

            t1 = [s for s in live if s.tier == 1]
            if t1:
                n = len(t1)
                for off in range(n):
                    idx = (self._cursor_t1 + off) % n
                    cand = t1[idx]
                    if self._slot_ready_at(cand) <= now:
                        self._cursor_t1 = (idx + 1) % n
                        return cand, 0.0

            t2 = [s for s in live if s.tier == 2]
            if t2:
                n = len(t2)
                for off in range(n):
                    idx = (self._cursor_t2 + off) % n
                    cand = t2[idx]
                    if self._slot_ready_at(cand) <= now:
                        self._cursor_t2 = (idx + 1) % n
                        return cand, 0.0

            earliest = min(self._slot_ready_at(s) for s in live)
            return None, max(0.2, earliest - now)

    def _mark_model_cooldown(self, slot: _Slot, seconds: float) -> None:
        until = self._clock() + max(1.0, seconds)
        with self._lock:
            prev = self._model_cooldowns.get((slot.provider, slot.model), 0.0)
            self._model_cooldowns[(slot.provider, slot.model)] = max(prev, until)

    def _mark_slot_cooldown(self, slot: _Slot, seconds: float) -> None:
        until = self._clock() + max(1.0, seconds)
        with self._lock:
            slot.cooldown_until = max(slot.cooldown_until, until)

    def _mark_key_invalid(self, slot: _Slot) -> None:
        with self._lock:
            for s in self._slots:
                if s.provider == slot.provider and s.key == slot.key:
                    s.dead = True

    def _mark_slot_daily_dead(self, slot: _Slot) -> None:
        with self._lock:
            slot.dead = True

    def _sync_groq_pool_cooldown(self, slot: _Slot, inner) -> None:
        """If Groq's proactive TPM header check cooled down its single-key
        pool after a successful call, mirror that cooldown onto this slot so
        the next chunk skips straight to another key/provider with zero wait."""
        pool = getattr(inner, "pool", None)
        if pool is None:
            return
        try:
            # Check if the single key in this inner GroqProvider was cooled down.
            with pool._lock:
                for val, _inv, _day, cd in pool._states:
                    if val == slot.key and cd > self._clock():
                        self._mark_slot_cooldown(slot, (cd - self._clock()) + 0.25)
                        break
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
        with self._lock:
            announce = not self._announced_pool
            if announce:
                self._announced_pool = True
        if announce:
            try:
                heartbeat.emit(
                    f"Xoay tua tự động: {self._n_gemini} khóa Gemini + "
                    f"{self._n_groq} khóa Groq ({len(self._slots)} slot miễn phí)"
                )
            except Exception:
                pass

        waited_total = 0.0
        attempts = 0
        max_attempts = max(20, len(self._slots) * 6)
        last_err: Exception | None = None

        while attempts < max_attempts:
            self._check_cancel()
            slot, wait_s = self._pick_slot()
            if slot is None:
                if wait_s < 0:
                    raise RateLimitExhausted(
                        "All Gemini & Groq API keys in Auto-Rotate mode are exhausted "
                        "or invalid for today. Stages are resumable."
                    ) from last_err
                if waited_total + wait_s > _MAX_TOTAL_WAIT_PER_CALL_S:
                    raise ServerErrorLLMError(
                        f"All Auto-Rotate slots stayed busy for >{int(_MAX_TOTAL_WAIT_PER_CALL_S)}s "
                        f"(last error: {last_err})",
                        status=503,
                    ) from last_err
                log.info("auto-rotate: all slots cooling down — waiting %.1fs", wait_s)

                def _wait_msg(rem: float) -> str:
                    try:
                        from dubtool.app import i18n

                        return i18n.tr("llm.rate_pause", n=max(1, int(rem + 0.999)))
                    except Exception:
                        return f"all keys cooling down ({int(rem)} s)"

                slept = heartbeat.sleeping(wait_s, _wait_msg, self._cancel)
                waited_total += max(0.0, float(slept))
                self._check_cancel()
                continue

            attempts += 1
            inner = self._inner_for(slot)
            use_schema = (
                schema
                if (
                    schema is not None
                    and getattr(inner, "structured", None) == "json_schema"
                    and (slot.provider, slot.model) not in self._schema_degraded
                )
                else None
            )
            try:
                res = inner.complete(
                    system, user, schema=use_schema, json_mode=json_mode, max_tokens=max_tokens
                )
                if slot.provider == "groq":
                    self._sync_groq_pool_cooldown(slot, inner)
                self.last_slot_label = slot.label
                log.info("auto-rotate: succeeded via %s", slot.label)
                return res
            except StageCancelled:
                raise
            except RequestTooLargeError as exc:
                # Groq has a 950-token output ceiling while Gemini allows 8,000
                # tokens. If a Groq slot hit output length and a Gemini slot is
                # ready right now, hand the chunk to Gemini instead of splitting.
                if slot.provider == "groq" and self._has_ready_slot("gemini"):
                    last_err = exc
                    log.warning(
                        "auto-rotate: %s hit output length — rotating chunk to ready Gemini slot",
                        slot.label,
                    )
                    self._mark_slot_cooldown(slot, 10.0)
                    self.switches += 1
                    continue
                # Chunk-size issue, not a slot outage — let LLMClient split the chunk.
                raise
            except SchemaRejectedError:
                with self._lock:
                    self._schema_degraded.add((slot.provider, slot.model))
                # Retry immediately on the same slot without schema.
                try:
                    res = inner.complete(
                        system, user, schema=None, json_mode=json_mode, max_tokens=max_tokens
                    )
                    self.last_slot_label = slot.label
                    log.info("auto-rotate: succeeded via %s (json-only)", slot.label)
                    return res
                except Exception as exc2:
                    last_err = exc2
                    self._mark_slot_cooldown(slot, 15.0)
                    self.switches += 1
                    continue
            except KeyExhaustedError as exc:
                last_err = exc
                pool = getattr(inner, "pool", None)
                if pool is not None and callable(getattr(pool, "all_daily_exhausted", None)) and pool.all_daily_exhausted():
                    log.warning("auto-rotate: %s already daily-exhausted — skipping for run", slot.label)
                    self._mark_slot_daily_dead(slot)
                else:
                    log.warning("auto-rotate: %s pool temporarily busy — cooling 15s, rotating", slot.label)
                    self._mark_slot_cooldown(slot, 15.0)
                self.switches += 1
                continue
            except InvalidKeyError as exc:
                last_err = exc
                log.warning("auto-rotate: %s invalid key — disabled for run", slot.label)
                self._mark_key_invalid(slot)
                self.switches += 1
                continue
            except RateLimitError as exc:
                last_err = exc
                kind = getattr(exc, "kind", None) or "minute"
                if kind == "day":
                    log.warning("auto-rotate: %s hit daily quota — skipping for run", slot.label)
                    self._mark_slot_daily_dead(slot)
                else:
                    retry_after = float(getattr(exc, "retry_after", None) or _DEFAULT_MINUTE_429_COOLDOWN_S)
                    cd = min(retry_after + 1.5, 90.0)
                    log.warning("auto-rotate: %s hit 429 (minute) — cooling %.1fs, rotating", slot.label, cd)
                    self._mark_slot_cooldown(slot, cd)
                self.switches += 1
                continue
            except ServerErrorLLMError as exc:
                last_err = exc
                status = getattr(exc, "status", None)
                cd = _MODEL_TIMEOUT_COOLDOWN_S if status == 504 else _MODEL_503_COOLDOWN_S
                log.warning(
                    "auto-rotate: %s server error (%s) — cooling model %.0fs, rotating immediately",
                    slot.label, status or "5xx", cd,
                )
                self._mark_model_cooldown(slot, cd)
                self.switches += 1
                continue
            except (TimeoutLLMError, NetworkLLMError) as exc:
                last_err = exc
                log.warning(
                    "auto-rotate: %s timeout/network — cooling %.0fs, rotating immediately",
                    slot.label, _MODEL_TIMEOUT_COOLDOWN_S,
                )
                self._mark_slot_cooldown(slot, _MODEL_TIMEOUT_COOLDOWN_S)
                self.switches += 1
                continue
            except LLMError as exc:
                last_err = exc
                log.warning("auto-rotate: %s failed (%s) — cooling 20s, rotating", slot.label, exc)
                self._mark_slot_cooldown(slot, 20.0)
                self.switches += 1
                continue

        raise LLMError(
            f"auto-rotate: exhausted {attempts} rotation attempts (last error: {last_err})"
        ) from last_err
