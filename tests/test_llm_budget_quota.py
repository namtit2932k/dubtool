"""AGENT_UPDATE_6.md 5.5 checks: request budget (5.2), quota-aware key pool
with persistence (5.3), the batched stage-5 shortening cap (5.1), and the
fallback to the selected fallback mode when the daily quota is out (5.3).

Offline: fake providers, injected clocks, no network, no key values anywhere.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dubtool.llm import budget
from dubtool.llm import quota_state
from dubtool.llm.base import RateLimitExhausted


# ------------------------------------------------------------------ budget
def test_budget_consumes_up_to_limit_then_refuses_and_raises():
    b = budget.RequestBudget(2)
    assert b.try_consume() and b.try_consume()
    assert not b.try_consume()          # refused BEFORE any request is made
    assert b.remaining == 0 and b.exhausted


def test_budget_contextvar_raise_and_reset():
    b = budget.RequestBudget(1)
    token = budget.bind(b)
    b.try_consume()
    with pytest.raises(budget.BudgetExhausted):
        budget.raise_if_exhausted()
    budget.reset(token)
    budget.raise_if_exhausted()  # no bound budget: no-op, must not raise


def test_from_config_reads_llm_max_requests():
    assert budget.from_config(SimpleNamespace(llm={"max_requests_per_run": 7})).limit == 7
    from dubtool.config import Config

    assert budget.from_config(Config()).limit == budget.DEFAULT_MAX_REQUESTS_PER_RUN


# --------------------------------------------------- quota_state (5.3)
@pytest.fixture()
def qpath(tmp_path, monkeypatch) -> Path:
    p = tmp_path / "quota_state.json"
    monkeypatch.setattr(quota_state, "state_path", lambda: p)
    return p


def test_mark_daily_is_per_key_and_per_model(qpath):
    now = 1_000_000.0
    quota_state.mark_daily("KEY-A", "model-x", now + 3600, now=now)
    assert quota_state.exhausted_until("KEY-A", "model-x", now) == now + 3600
    # same key, different model: NOT exhausted (quota is per (key, model))
    assert quota_state.exhausted_until("KEY-A", "model-y", now) is None
    # different key, same model: NOT exhausted
    assert quota_state.exhausted_until("KEY-B", "model-x", now) is None


def test_expired_entries_removed_on_load(qpath, tmp_path, monkeypatch):
    now = 2_000_000.0
    quota_state.mark_daily("K", "m", now + 60, now=now)
    assert json.loads(qpath.read_text(encoding="utf-8"))  # non-empty on disk
    data = quota_state.load(now + 120)
    assert data == {}

    monkeypatch.setattr(quota_state, "state_path", lambda: tmp_path / "missing.json")
    assert quota_state.load(now) == {}


def test_quota_state_file_has_only_fingerprints(qpath):
    """Update 5 no-leak rule, extended to quota_state.json (AGU6 5.5)."""
    values = ["sk-ant-very-secret-value-1", "AIzaAnotherSecretValue42xyz"]  # fixture-fake-key
    later = time.time() + 3 * 86400
    for v in values:
        quota_state.mark_daily(v, "gemini-3.8-flash", later)
    text = qpath.read_text(encoding="utf-8")
    for v in values:
        assert v not in text and v[:6] not in text
        assert quota_state.fingerprint(v) in text  # still identifiable by slot


def test_next_pacific_midnight_is_monotonic_and_below_two_days():
    now = 1_760_000_000.0  # a concrete 2025-timezone-agnostic epoch
    first = quota_state.next_pacific_midnight(now)
    assert first > now
    assert first - now <= 2 * 86400
    assert quota_state.next_pacific_midnight(first) > first


# ------------------------------------------------- key pool fail-fast (5.3)
@pytest.fixture()
def pool_keys(monkeypatch):
    stored = ["pool-key-1", "pool-key-2", "pool-key-3"]
    monkeypatch.setattr(quota_state, "state_path",
                        lambda: Path(quota_state.state_path() or "")
                        if hasattr(quota_state, "state_path") else Path("."))
    return stored


def test_pool_persists_daily_mark_by_fingerprint(qpath):
    from dubtool.llm.keypool import KeyPool

    pool = KeyPool(["KEY-A", "KEY-B"], model="gemini-3.8-flash",
                   until_epoch=lambda: time.time() + 1000.0)
    pool.mark_daily_limit("KEY-A", until_epoch=time.time() + 10 * 86400)
    data = json.loads(qpath.read_text(encoding="utf-8"))
    assert list(data) == [f"{quota_state.fingerprint('KEY-A')}|gemini-3.8-flash"]
    assert data[f"{quota_state.fingerprint('KEY-A')}|gemini-3.8-flash"]["until"] > time.time()
    assert json.loads(qpath.read_text(encoding="utf-8"))
    # And a NEW pool consulting the file pre-marks the same pairing:
    assert KeyPool(["KEY-A"], model="gemini-3.8-flash").all_daily_exhausted() is True
    # Other model remains usable with the same key:
    assert KeyPool(["KEY-A"], model="gemini-3.5-flash-lite").acquire() == "KEY-A"


def test_pool_fail_fast_when_all_daily(qpath):
    from dubtool.llm.keypool import KeyPool

    sleeps: list[float] = []
    pool = KeyPool(["K1", "K2"], model="m", clock=lambda: 100.0, sleep=sleeps.append)
    pool.mark_daily_limit("K1")
    pool.mark_daily_limit("K2")
    assert pool.all_daily_exhausted() is True
    assert pool.wait_for_key() is False   # fails fast: no pointless waiting
    assert sleeps == [], "an all-daily pool must never sleep"


# ----------------------------------------------- batched s5 shortening (5.1)
class _Job82:
    """82 tts-stuffed segments, each far longer than its slot."""

    def __init__(self, tmp_path: Path):
        root = tmp_path / "j82"
        (root / "tts").mkdir(parents=True)
        (root / "fitted").mkdir(parents=True)
        self.dir = root
        self.translated_path = root / "translated.json"
        entries = []
        for i in range(82):
            entries.append({
                "id": i, "start": i * 4.0, "end": i * 4.0 + 1.0, "text": "hi",
                "vi": f"câu {i} quá dài để vừa khe", "max_chars": 10,
                "reviewed": False, "tts_duration": 5.0, "fit_status": "pending",
            })
            (root / "tts" / f"{i}.mp3").touch()
        self.translated_path.write_text(json.dumps(entries, ensure_ascii=False),
                                        encoding="utf-8")

    tts_dir = property(lambda self: self.dir / "tts")
    fitted_dir = property(lambda self: self.dir / "fitted")


class _Cfg82:
    chars_per_sec = 14
    max_stretch = 1.15
    max_fit_attempts = 3
    tts_voice = "v"
    tts_rate = "+0%"
    tts_trim_silence = False
    tts_silence_threshold_db = -35
    tts_silence_margin_s = 0.05


class _CountingLLM:
    total_input_tokens = 0
    total_output_tokens = 0

    def __init__(self, provider=None):
        self.requests = 0

    def shorten_batch(self, items):
        self.requests += 1
        return [{"id": item["id"], "vi": "ngắn"} for item in items]


class _Tts05:
    """Always regenerates in half a second of audio (fits the slots).
    Cache entries are real files under `cache` so the copyfile in s5 works."""

    def __init__(self, cache: Path):
        self.cache = cache
        self.calls = 0

    def synthesize(self, seg: dict, cancel=None):
        self.calls += 1
        target = self.cache / f"{seg['id']}.mp3"
        target.touch(exist_ok=True)
        return 0.5, target


def test_s5_batched_requests_are_capped_per_round(tmp_path, monkeypatch):
    """AGU6 5.5: 82 over-long segments → at most ceil(82/40) requests/round
    (3), not ~one request per segment, and statuses recompute correctly."""
    import dubtool.stages.s5_fit_duration as s5
    import dubtool.audio_utils
    from dubtool.stages.s5_fit_duration import final_status

    monkeypatch.setattr(dubtool.audio_utils, "probe_duration", lambda p: 3.0)

    job = _Job82(tmp_path)
    cache = tmp_path / "fakecache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(
        "dubtool.stages.s4_tts.build_tts",
        lambda config, cache_dir, **kw: _Tts05(cache),
    )
    monkeypatch.setattr(s5, "materialize_clip", lambda *a, **k: None)
    llm_holder: list[_CountingLLM] = []

    class _LLM(_CountingLLM):
        def __init__(self, provider=None):
            super().__init__(provider)
            llm_holder.append(self)

    monkeypatch.setattr("dubtool.llm.LLMClient", _LLM, raising=False)
    monkeypatch.setattr("dubtool.llm.get_provider", lambda *a, **k: object())
    s5.run(job, _Cfg82(), force=False)

    assert len(llm_holder) == 1, "one client for the whole run"
    assert llm_holder[0].requests == math.ceil(82 / 40) == 3
    entries = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert all(e["vi"] == "ngắn" for e in entries)
    # Same final-status rules as the old per-segment logic: one shorten
    # attempt used → "shortened" for every regenerated segment.
    assert all(e["fit_status"] == "shortened" for e in entries)
    assert not any(e["fit_status"] == "overflow" for e in entries)


# ------------------------------------------------- s5 fallback to fast (5.3)
def test_s5_falls_back_to_fallback_mode_on_quota_out(tmp_path, monkeypatch):
    """When the selected model is daily-exhausted mid-shortening, one
    retry with the configured fallback mode keeps the run going."""
    import dubtool.stages.s5_fit_duration as s5
    import dubtool.audio_utils

    monkeypatch.setattr(dubtool.audio_utils, "probe_duration", lambda p: 3.0)

    class _FastJob:
        def __init__(self, root: Path):
            (root / "tts").mkdir(parents=True, exist_ok=True)
            (root / "fitted").mkdir(parents=True, exist_ok=True)
            self.dir = root
            self.translated_path = root / "translated.json"
            entries = [{
                "id": 0, "start": 0.0, "end": 1.0, "text": "hello",
                "vi": "câu này quá dài để vừa khe", "max_chars": 10,
                "reviewed": False, "tts_duration": 3.0, "fit_status": "pending",
            }]
            self.translated_path.write_text(json.dumps(entries, ensure_ascii=False),
                                            encoding="utf-8")
            (root / "tts" / "0.mp3").touch()

        tts_dir = property(lambda self: self.dir / "tts")
        fitted_dir = property(lambda self: self.dir / "fitted")

    job = _FastJob(tmp_path / "jfast")

    class _FastClient:
        total_input_tokens = 0
        total_output_tokens = 0

        def __init__(self, provider=None):
            self.provider = provider
            self.calls = 0

        def shorten_batch(self, items):
            self.calls += 1
            return [{"id": item["id"], "vi": "ok-short"} for item in items]

    class _MainQuotaClient:
        total_input_tokens = 0
        total_output_tokens = 0

        def __init__(self, provider=None):
            self.provider = provider

        def shorten_batch(self, items):
            raise RateLimitExhausted(
                "all keys daily-exhausted for the selected model"
            )

    built: list[object] = []

    class _LLMFallbackSwitch:
        """Stands in for LLMClient: the first build (selected mode) gets the
        quota-out client, the fallback build gets a working client."""

        total_input_tokens = 0
        total_output_tokens = 0

        def __new__(cls, provider=None, **kw):
            if provider == "fallback-provider-marker":
                return _FastClient(provider)
            return _MainQuotaClient(provider)

        def __init__(self, provider=None):  # never reached (see __new__)
            pass

    calls = {"n": 0}

    def fake_get_provider(config, provider_id=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "selected-provider-marker"
        return "fallback-provider-marker"

    cache = tmp_path / "fakecache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(
        "dubtool.stages.s4_tts.build_tts", lambda c, d, **kw: _Tts05(cache))
    monkeypatch.setattr(s5, "materialize_clip", lambda *a, **k: None)
    monkeypatch.setattr("dubtool.llm.LLMClient", _LLMFallbackSwitch, raising=False)
    monkeypatch.setattr("dubtool.llm.get_provider", fake_get_provider, raising=False)

    cfg = _Cfg82()
    cfg.llm = {"shorten_fallback_mode": "fast"}
    s5.run(job, cfg, force=False, api_keys=["fake-key"])

    entries = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert entries[0]["vi"] == "ok-short"       # the fallback batch applied
    assert calls["n"] == 2                      # selected-mode build + fallback build
