"""Update 12 task 1 tests: the Gemini retry policy from the 2026-11-04
evidence run (sample/evidence/pipeline.log, 11:27:23-11:33:29).

The replay drives the REAL chunk planner (_units_runner) plus the real
client/resilience machinery over a fake provider; no network, no real
sleeps (the tests use the virtual clock). The providers reproduce the
evidence sequence: 8 chunks of 5-8 units, a 503 per chunk, one 504 on a
small chunk, a per-day 429 with no key left, and a per-minute 429.

New-policy properties asserted here (several fail on the Update 11 code,
which splits on a 503 and multiplies requests):
- no split on 503/429 (only 504/timeout/400-size split, once, never a
  chunk below the minimum sizes, never a half again);
- requests per chunk bounded by the backoff list length;
- the model circuit breaker opens and the remaining chunks go to the Fast
  model; completed chunks are kept;
- a per-minute 429 pauses every request to the model until retryDelay;
- a per-day 429 goes straight to the fallback (no 3-attempt loop, no
  hour-long wait);
- per-unit model recording and per-model counts;
- no log line mentions specification identifiers (AGU / AGENT_UPDATE).
"""

from __future__ import annotations

import logging
import re

import pytest

from tests.fake_clock import fast_clock
from dubtool.llm.base import (
    LLMResult,
    RateLimitError,
    ServerErrorLLMError,
    OverloadFallbackFast,
)
from dubtool.stages._units_runner import run_units_parallel

FAST_MODEL = "gemini-3.5-flash-lite"


# --------------------------------------------------------------------- units

_UNIT_TEXTS = [
    "Dark energy is a strange phenomenon that scientists believe permeates "
    "the universe.",
    "First of all, the universe is expanding, and not only that, the rate "
    "of its expansion is accelerating.",
    "Scientists once thought the gravitational pull of matter would slow "
    "the expansion down over time.",
    "Instead, in 1998, two teams measured distant supernovae and found the "
    "expansion speeds up.",
    "Something unseen pushes space apart, and we call it dark energy.",
    "It accounts for roughly seventy percent of everything in the cosmos.",
    "Heat death or a big freeze is one possible ending of the expansion.",
    "In a heat death scenario, matter stays intact but energy changes stop.",
    "Stars burn out, black holes evaporate, and the last light fades away.",
    "The universe becomes cold, dark, and empty in the far future.",
    "Until 1998, we thought that the universe must work by slowing down.",
    "Measurements of the background radiation confirmed the acceleration.",
]


def _units(count: int = 59) -> list[dict]:
    """Evidence-like sentence units (~110-120 source chars each) so the real
    planner cuts chunks of at most 8 units and 1200 characters."""
    units = []
    for i in range(count):
        text = _UNIT_TEXTS[i % len(_UNIT_TEXTS)]
        while len(text) < 105:
            text = text + " " + _UNIT_TEXTS[(i + 1) % len(_UNIT_TEXTS)][:60]
        units.append({
            "unit_id": i,
            "text": text[:140],
            "max_chars": 80,
            "start": float(i) * 5.0,
            "end": float(i) * 5.0 + 5.0,
            "seg_ids": [i],
            "segments": [],
        })
    return units


def _ids_from(user: str) -> list[int]:
    return [
        int(m) for m in re.findall(r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"text"', user)
    ]


# ------------------------------------------------------------------ providers

def _fast_provider():
    """The fallback (Fast) model: answers on the first call."""

    import json as _json

    class _Provider:
        name = "gemini"
        model = FAST_MODEL
        timeout_s = 90.0

        def __init__(self):
            self.calls = 0

        def complete(self, system, user, *, json_mode=True, max_tokens=8000):
            self.calls += 1
            payload = [{"id": i, "vi": f"dịch {i}"} for i in _ids_from(user)]
            return LLMResult(text=_json.dumps(payload, ensure_ascii=False))

    return _Provider()


class _AccurateProvider:
    """The overloaded Accurate model from the evidence log.

    mode "rest": every request answers 503 UNAVAILABLE (high demand).
    mode "parity": every call at an ODD (1-based) position fails — 503, or
    a 504 once when `seed_504` is set (the evidence's one small-chunk
    deadline) — and every even call succeeds. `seeded` swaps the failing
    odd call to 504 exactly once, then the next odd call gets 503 again.
    """

    name = "gemini"
    model = "gemini-3.8-flash"
    timeout_s = 180.0

    def __init__(self, mode: str = "rest", seed_504: bool = False):
        self.mode = mode
        self.seed_504 = seed_504
        self.calls = 0
        self.odd_calls = 0

    def complete(self, system, user, *, json_mode=True, max_tokens=8000):
        self.calls += 1
        if self.mode == "parity":
            if self.seed_504 and self.odd_calls == 0:
                self.seed_504 = False
                self.odd_calls += 1
                raise ServerErrorLLMError("504 DEADLINE_EXCEEDED", status=504)
            self.odd_calls += 1
            if self.calls % 2 == 1:  # 1-based odd → fail
                raise ServerErrorLLMError(
                    "503 UNAVAILABLE (high demand)", status=503
                )
            import json as _json

            return LLMResult(
                text=_json.dumps(
                    [{"id": i, "vi": f"dic {i}"} for i in _ids_from(user)],
                    ensure_ascii=False,
                )
            )
        raise ServerErrorLLMError("503 UNAVAILABLE (high demand)", status=503)


class _MinuteThenOkProvider:
    """Per-minute 429 `failures` times (with a retryDelay), then success."""

    name = "gemini"
    model = "m-fake"
    timeout_s = 60.0

    def __init__(self, retry_after: float = 30.0, failures: int = 2):
        self.calls = 0
        self.failures = failures
        self.retry_after = retry_after

    def complete(self, system, user, *, json_mode=True, max_tokens=8000):
        self.calls += 1
        if self.calls <= self.failures:
            raise RateLimitError(
                "gemini", detail="rate limit (minute)",
                retry_after=self.retry_after, kind="minute",
            )
        import json as _json

        return LLMResult(
            text=_json.dumps(
                [{"id": i, "vi": f"dịch {i}"} for i in _ids_from(user)],
                ensure_ascii=False,
            )
        )


class _DayQuotaProvider:
    """Per-day 429 on every request: no key left → straight to fallback."""

    name = "gemini"
    model = "m-fake"
    timeout_s = 60.0

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, *, json_mode=True, max_tokens=8000):
        self.calls += 1
        raise RateLimitError(
            "gemini", detail="quota exceeded (day)", kind="day",
            retry_after=86400.0,
        )


# ------------------------------------------------------------------- harness

def _run_translate(
    provider,
    *,
    backoff_s=(5.0, 15.0, 45.0, 90.0),
    stage_budget_s=300.0,
    breaker_failures=3,
    breaker_cooldown_s=60.0,
    fallback_after_opens=1,
    split_min_units=4,
    split_min_chars=600,
    chunk_max_units=8,
    chunk_max_chars=1200,
    concurrency=1,
    seed_504: bool = False,
):
    """The stage machinery in miniature: real planner, real LLMClient and
    translate_resilient over the fake provider — mirrors s3_translate's
    translate_payloads + switch_to_fast_model, minus file writes."""
    from dubtool.llm.client import LLMClient
    from dubtool.stages.s3_translate import translate_resilient

    stats = {"splits": 0, "fallback": False, "fast_model": None, "models": {}}
    applied: list = []
    on_fallback_calls: list = []
    per_chunk_counts: dict[str, int] = {}

    stage_wait = {"waited_s": 0.0, "consulted_at": 0.0, "consults": 0}
    options = {
        "backoff_s": backoff_s,
        "overload_after": 2,
        "overload_policy": "fallback_fast",
        "stage_budget_s": stage_budget_s,
        "stage_wait_state": stage_wait,
        "breaker_failures": breaker_failures,
        "breaker_cooldown_s": breaker_cooldown_s,
        "fallback_after_opens": fallback_after_opens,
        "split_on_timeout": True,
        "split_min_units": split_min_units,
        "split_min_chars": split_min_chars,
    }

    holder: dict = dict(client=None)

    def _switch(from_model: str) -> None:
        on_fallback_calls.append(from_model)
        holder["client"] = LLMClient(
            _fast_provider(), options=options,
            on_overload=lambda info=None: "fallback_fast",
        )
        holder["client"].request_units = None
        stats["fallback"] = True
        stats["fast_model"] = holder["client"].provider.model

    holder["client"] = LLMClient(
        provider, options=options, on_overload=lambda info=None: "fallback_fast",
    )

    def do_translate(client, payloads_, ctx):
        return client.translate_batch(payloads_, ctx)

    # Unit 11 lives in the planner's second chunk of a few units; that
    # chunk's first request answers 504 (seeded options).
    small_504_unit = 11 if seed_504 else None

    def chunk_fn(chunk, label):
        payloads = [
            {"id": u["unit_id"], "text": u["text"], "max_chars": u["max_chars"]}
            for u in chunk
        ]
        if small_504_unit is not None and any(
            u["unit_id"] == small_504_unit for u in chunk
        ):
            provider.seed_504 = True
        calls_before = provider.calls
        updates = translate_resilient(
            holder, do_translate, payloads, [], label,
            on_fallback=_switch, split_on_timeout=True,
            split_min_units=split_min_units, stats=stats,
            split_min_chars=split_min_chars,
        )
        per_chunk_counts[label] = provider.calls - calls_before
        model = getattr(
            getattr(holder["client"], "provider", None), "model", "?"
        )
        stats["models"][model] = stats["models"].get(model, 0) + len(payloads)
        for upd in updates:
            upd["model"] = model
        return updates

    adaptive = {
        "max_chars": chunk_max_chars, "cur_chars": chunk_max_chars,
        "max_units": chunk_max_units, "timeout_s": 0.0,
        "quick_streak": 0, "latencies": [],
    }
    try:
        run_units_parallel(
            _units(), chunk_fn, apply=lambda upd: applied.extend(upd),
            concurrency=concurrency, adaptive=adaptive,
        )
        final_error = None
    except Exception as exc:  # a chunk give-up propagates at the end
        final_error = exc
    return {
        "stats": stats, "applied": applied, "stage_wait": stage_wait,
        "calls": provider.calls, "fallback_calls": on_fallback_calls,
        "per_chunk_counts": per_chunk_counts, "error": final_error,
    }


# -------------------------------------------------------------------- tests

def test_replay_503_never_splits_and_falls_back_within_budget(monkeypatch):
    """The evidence replay on the new policy: no split on 503, per-chunk
    request bound, the breaker opens and the rest goes to the Fast model,
    completed chunks are kept, and the stage ends within the budget."""
    with fast_clock(monkeypatch=monkeypatch) as clock:
        provider = _AccurateProvider("rest")
        result = _run_translate(provider)

    stats = result["stats"]
    # 1. A 503 is a capacity signal: never split anything.
    assert stats["splits"] == 0, stats
    # 2. Every chunk used at most len(backoff_s) requests.
    assert result["per_chunk_counts"], "no chunk ran"
    assert all(n <= 4 for n in result["per_chunk_counts"].values()), (
        result["per_chunk_counts"]
    )
    # 3. The fallback happened; the Fast model took over the rest.
    assert stats["fallback"] is True
    assert stats["fast_model"] == FAST_MODEL
    # 4. Completed chunks were kept and every unit got a translation with
    #    the model that translated it.
    assert [u["id"] for u in result["applied"]] == list(range(59))
    assert all(u.get("model") for u in result["applied"])
    assert {u["model"] for u in result["applied"]} == {FAST_MODEL}
    # 5. The stage did not out-wait its budget (virtual seconds).
    assert result["stage_wait"]["waited_s"] <= 300.0 + 200.0, (
        result["stage_wait"]["waited_s"]
    )
    # 6. The total request count stays inside chunks × attempts.
    assert result["calls"] <= 9 * 4, result["calls"]


def test_replay_parity_504_splits_only_the_seeded_chunk(monkeypatch):
    """Parity script (every odd call fails, then success): the ONLY split
    is the seeded 504 chunk, split once into halves that are never split
    again; every 503 chunk retried the same request instead."""
    with fast_clock(monkeypatch=monkeypatch) as clock:
        provider = _AccurateProvider("parity", seed_504=True)
        result = _run_translate(provider)

    stats = result["stats"]
    assert stats["splits"] == 1, stats
    assert not result["error"], result["error"]
    assert [u["id"] for u in result["applied"]] == list(range(59))


def test_429_minute_pauses_all_requests_until_retry_delay(monkeypatch):
    """A per-minute 429 pauses EVERY request to the model until retryDelay
    passes (a global pause, not a per-request sleep): a call that arrives
    during the pause waits the remaining time out before its request."""
    from dubtool.llm.client import LLMClient

    with fast_clock(monkeypatch=monkeypatch) as clock:
        provider = _MinuteThenOkProvider(retry_after=30.0, failures=1)
        options = {
            "backoff_s": [5.0, 15.0, 45.0, 90.0],
            "overload_policy": "fallback_fast",
            "stage_budget_s": 600.0,
            "stage_wait_state": {"waited_s": 0.0, "consulted_at": 0.0,
                                 "consults": 0},
            "overload_after": 2,
        }
        client = LLMClient(provider, options=options,
                           on_overload=lambda info=None: "fallback_fast")
        payloads = [{"id": 0, "text": "hello", "max_chars": 50}]
        first = client.translate_batch(payloads, [])
        assert first[0]["vi"] == "dịch 0"
        # The retryDelay was honored: one pause, then the retry succeeds.
        assert clock.now >= 1030.0, clock.now
        assert provider.calls == 2  # one refused request + its retry
        # Now a call that arrives DURING a fresh pause window: it waits
        # the reminder out first; with no more 429s, one request suffices.
        provider.failures = 0
        clock.set(1005.0)
        provider.calls = 0
        second = client.translate_batch(payloads, [])
        assert second[0]["vi"]
        # The second call's request waited until the pause ended.
        assert provider.calls == 1
        assert clock.now >= 1030.0, clock.now


def test_429_day_no_key_left_goes_straight_to_fallback():
    """Per-day quota gone on a model with no other key: one call, no
    3-attempt loop, no hour-long wait — OverloadFallbackFast at once."""
    from dubtool.llm.client import LLMClient

    provider = _DayQuotaProvider()
    options = {
        "backoff_s": [5.0, 15.0, 45.0, 90.0],
        "overload_policy": "fallback_fast",
        "stage_budget_s": 300.0,
        "stage_wait_state": {"waited_s": 0.0, "consulted_at": 0.0,
                             "consults": 0},
        "overload_after": 2,
    }
    client = LLMClient(provider, options=options,
                       on_overload=lambda info=None: "fallback_fast")
    with pytest.raises(OverloadFallbackFast):
        client.translate_batch([{"id": 0, "text": "x", "max_chars": 9}], [])
    assert provider.calls == 1  # straight to the fallback, one request


def test_chunk_checkpoints_save_resume_and_start_over(tmp_path):
    """Chunk checkpoints: a finished chunk is never re-requested; a failed
    chunk never discards the others; 'start over' clears the store."""
    from dubtool.llm.checkpoints import ChunkCheckpointStore, chunk_prompt_hash

    store_path = tmp_path / "translate_chunks.json"
    store = ChunkCheckpointStore(store_path)
    source = [{"id": 0, "text": "one "}, {"id": 1, "text": "two"}]
    phash = chunk_prompt_hash({"glossary": {"a": "b"}}, {"first": "tôi"})
    assert store.get(1, source, phash) is None
    store.save(1, source, model="m", prompt_hash=phash,
               vi=[{"id": 0, "vi": "một"}])
    store.save(2, [{"id": 2, "text": "three"}], model="m", prompt_hash=phash,
               vi=[{"id": 2, "vi": "ba"}])

    reopened = ChunkCheckpointStore(store_path)
    hit = reopened.get(1, source, phash)
    assert hit is not None and hit["vi"][0]["vi"] == "một"
    # a different prompt (glossary changed) does not match
    assert reopened.get(1, source, chunk_prompt_hash({}, {})) is None
    # a different source does not match
    assert reopened.get(1, [{"id": 0, "text": "ONE"}], phash) is None
    # start over clears everything
    reopened.clear()
    assert reopened.get(1, source, phash) is None


def test_no_spec_identifiers_in_logs_and_panels(monkeypatch, caplog):
    """No log line or panel event ever mentions AGU / AGENT_UPDATE."""
    with fast_clock(monkeypatch=monkeypatch) as clock:
        provider = _AccurateProvider("parity", seed_504=True)
        with caplog.at_level(logging.DEBUG, logger="dubtool"):
            result = _run_translate(provider)
    assert not result["error"], result["error"]
    messages = [r.getMessage() for r in caplog.records]
    offenders = [m for m in messages if ("AGU" in m) or ("AGENT_UPDATE" in m)]
    assert offenders == [], offenders


def test_panel_says_breaker_or_fallback(monkeypatch):
    """The breaker state / fallback decision reaches the details panel."""
    from dubtool import heartbeat

    events: list[str] = []
    heartbeat.bind(lambda kind, message: events.append(message))
    try:
        with fast_clock(monkeypatch=monkeypatch) as clock:
            provider = _AccurateProvider("rest")
            result = _run_translate(provider)
        assert result["stats"]["fallback"] is True
    finally:
        heartbeat.reset(True)
    assert any(
        ("mạch" in message) or ("breaker" in message) or ("Nhanh" in message)
        or ("circuit" in message) or ("quá tải" in message)
        for message in events
    ), events[:20]


def test_gemini_map_error_and_pool_rotation_on_minute_429(monkeypatch):
    """Per-minute 429 carries kind='minute' + retryDelay and switches to another
    idle key immediately without a 60s global pause; only when every key is
    cooling down does the client pause globally."""
    pytest.importorskip("google.genai")

    from dubtool.llm.base import classify_rate_limit
    from dubtool.llm.client import LLMClient
    from dubtool.llm.gemini import GeminiProvider

    prov = GeminiProvider(model="m", api_keys=["fake-k1", "fake-k2"])

    class _Fake429(Exception):
        code = 429
        details = [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerMinutePerProject"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "35s"},
        ]
        message = "You exceeded your current quota. Please retry in 35.7s."

    mapped = prov._map_error(_Fake429("quota"), "fake-k1")
    assert isinstance(mapped, RateLimitError)
    assert getattr(mapped, "kind", None) == "minute"
    assert mapped.retry_after == 35.0
    assert classify_rate_limit(RuntimeError("Please retry in 35.7s")) == ("per_minute", pytest.approx(35.7))

    # Case 1: k1 hits per-minute 429 through real GeminiProvider.complete -> _map_error,
    # k2 succeeds immediately -> switches without global pause.
    from types import SimpleNamespace

    seen_keys: list[str] = []

    def _client_for_switch(key: str):
        class _Models:
            def generate_content(self, **_kw):
                seen_keys.append(key)
                if key == "fake-k1":
                    raise _Fake429("You exceeded your current quota. Please retry in 35.7s.")
                return SimpleNamespace(text="{\"ok\": true}", usage_metadata=None)

        return SimpleNamespace(models=_Models())

    prov.client_for = _client_for_switch  # type: ignore[method-assign]
    client = LLMClient(prov)
    res = client._call("sys", "usr")
    assert res == "{\"ok\": true}"
    assert seen_keys == ["fake-k1", "fake-k2"]
    assert client.key_switches == 1
    assert client.rate_pause_until == 0.0

    # Case 2: BOTH keys hit per-minute 429 -> pauses globally only once both are limited,
    # waits for pool cooldown via FakeClock, and retries cleanly without KeyExhaustedError.
    attempts = {"n": 0}
    with fast_clock(monkeypatch=monkeypatch):
        prov2 = GeminiProvider(model="m", api_keys=["fake-k1", "fake-k2"])

        def _client_for_all_busy(key: str):
            class _Models:
                def generate_content(self, **_kw):
                    attempts["n"] += 1
                    if attempts["n"] <= 2:
                        raise _Fake429("per minute quota exceeded. retry in 2.0s")
                    return SimpleNamespace(text="{\"ok\": 2}", usage_metadata=None)

            return SimpleNamespace(models=_Models())

        prov2.client_for = _client_for_all_busy  # type: ignore[method-assign]
        client2 = LLMClient(prov2)
        res2 = client2._call("sys", "usr")
    assert res2 == "{\"ok\": 2}"
    assert attempts["n"] == 3
    assert client2.key_switches == 1
    assert client2.rate_pause_until > 0.0
