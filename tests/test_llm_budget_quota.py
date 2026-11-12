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

    def shorten_batch(self, items, policy=None):
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

        def shorten_batch(self, items, policy=None):
            self.calls += 1
            return [{"id": item["id"], "vi": "ok-short"} for item in items]

    class _MainQuotaClient:
        total_input_tokens = 0
        total_output_tokens = 0

        def __init__(self, provider=None):
            self.provider = provider

        def shorten_batch(self, items, policy=None):
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


def test_from_config_scales_budget_with_key_count():
    cfg = SimpleNamespace(llm={"max_requests_per_run": 120})
    assert budget.from_config(cfg, key_count=1).limit == 120
    assert budget.from_config(cfg, key_count=4).limit == 480
    assert budget.from_config(cfg, key_count=10).limit == 120 * budget.DEFAULT_MAX_KEY_MULTIPLIER


def test_budget_exhausted_fails_fast_without_retries_single_and_pooled(caplog):
    import logging

    from dubtool.llm.base import LLMResult
    from dubtool.llm.client import LLMClient
    from dubtool.llm.keypool import KeyPool

    class _SingleProvider:
        name = "fake"
        model = "fake-m"

        def __init__(self):
            self.calls = 0

        def complete(self, system, user, *, schema=None, json_mode=True,
                     max_tokens=8000, thinking_level=None):
            self.calls += 1
            return LLMResult(text="{}", input_tokens=1, output_tokens=1)

    class _PooledProvider(_SingleProvider):
        def __init__(self):
            super().__init__()
            self.pool = KeyPool(["fake-k1", "fake-k2"], model="fake-m")
            self.switches = 0

        @property
        def api_key(self) -> str:
            return self.pool.acquire()

    for prov_cls in (_SingleProvider, _PooledProvider):
        prov = prov_cls()
        b = budget.RequestBudget(1)
        tok = budget.bind(b)
        client = LLMClient(prov)
        caplog.clear()
        try:
            with caplog.at_level(logging.WARNING, logger="dubtool"):
                client._call_and_validate("sys", "usr", validate=lambda d: True, error_note_builder=lambda e: str(e))
                with pytest.raises(budget.BudgetExhausted):
                    client._call_and_validate("sys", "usr", validate=lambda d: True, error_note_builder=lambda e: str(e))
                with pytest.raises(budget.BudgetExhausted):
                    client._call("sys", "usr")
        finally:
            budget.reset(tok)
        assert prov.calls == 1, f"{prov_cls.__name__} must not call provider or retry after BudgetExhausted"
        budget_logs = [r.getMessage() for r in caplog.records if "request budget used up" in r.getMessage()]
        assert len(budget_logs) == 1


def test_classify_error_maps_budget_exhausted_to_quota():
    from dubtool.api import _classify_error

    code, _detail = _classify_error(budget.BudgetExhausted("LLM request budget used up (40 requests this run)"))
    assert code == "quota"


def test_s3_translate_budget_exhausted_preserves_partial_work_and_reraises(tmp_path, monkeypatch):
    from dubtool.config import Config
    from dubtool.llm.base import LLMResult
    from dubtool.stages import s3_translate as s3

    job_dir = tmp_path / "job_budget"
    job_dir.mkdir(parents=True)
    long_sentence = ("This is a detailed explanatory sentence for splitting chunks cleanly. " * 22).strip()
    transcript = [
        {"id": 0, "start": 0.0, "end": 60.0, "text": f"{long_sentence} First part."},
        {"id": 1, "start": 61.0, "end": 120.0, "text": f"{long_sentence} Second part."},
    ]
    (job_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

    class _FakeJob:
        dir = job_dir
        transcript_path = job_dir / "transcript.json"
        translated_path = job_dir / "translated.json"
        units_path = job_dir / "units.json"
        dub_units_path = job_dir / "dub_units.json"
        translate_chunks_path = job_dir / "translate_chunks.json"

    class _OneShotProvider:
        name = "gemini"
        model = "gemini-3.5-flash-lite"

        def complete(self, system, user, *, schema=None, json_mode=True,
                     max_tokens=8000, thinking_level=None):
            return LLMResult(
                text=json.dumps([{"id": 0, "vi": "Câu đầu tiên."}]),
                input_tokens=1,
                output_tokens=1,
            )

    monkeypatch.setattr("dubtool.stages.s3_translate.get_provider", lambda *a, **k: _OneShotProvider())
    monkeypatch.setattr(
        "dubtool.stages.s3_translate._filler_state",
        lambda *a, **k: {"words": [], "settings": {}, "actions": {}, "policy": "", "count": 0},
    )

    cfg = Config()
    cfg.llm_provider = "gemini"
    cfg.translate = {**(cfg.translate or {}), "unit_mode": "sentence", "punctuate": False, "concurrency": 1}
    cfg.units = {**(cfg.units or {}), "max_chars_per_chunk": 10, "max_units_per_chunk": 1}

    b = budget.RequestBudget(1)
    tok = budget.bind(b)
    try:
        with pytest.raises(budget.BudgetExhausted):
            s3.run(_FakeJob(), cfg, force=False, api_keys=["fake-key"])
    finally:
        budget.reset(tok)

    saved_entries = json.loads((job_dir / "translated.json").read_text(encoding="utf-8"))
    assert saved_entries[0]["vi"] == "Câu đầu tiên."
    assert not saved_entries[1].get("vi")
    saved_dub_units = json.loads((job_dir / "dub_units.json").read_text(encoding="utf-8"))
    assert len(saved_dub_units) == 2
    assert saved_dub_units[0]["vi"] == "Câu đầu tiên."
    assert not saved_dub_units[1].get("vi")
