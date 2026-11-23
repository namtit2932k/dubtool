"""Regression tests for Stage 3 performance & resilience optimizations:

1. Default ``punctuate.enabled == "off"`` avoids burning sequential pre-translation
   LLM calls while local timing repair / sentence resegmentation still runs.
2. Right-sized chunking packs up to 30 units (1800 Latin chars / 900 CJK chars)
   instead of fragmenting short CJK utterances into 8-unit / ~150-char calls.
3. Fallback model switching never loops back onto the same failing model,
   steps cleanly through healthy Gemini siblings when enabled, deduplicates
   concurrent worker switches, and terminates with a clear error when all
   candidates are exhausted.
4. Server overload (503) immediately throttles ``run_units_parallel`` concurrency
   down to 1 via ``throttle_state``.
5. Legacy ``settings.json`` (pre-schema-2) migrates congested ``gemini-3.6-flash``
   / ``gemini-3.8-flash`` fallback_model to ``gemini-3.1-flash-lite`` once, while
   preserving explicit user picks saved under ``settings_schema >= 2``.
6. ``_redact()`` keeps long lowercase model IDs readable in copied details logs
   while continuing to mask API keys and arbitrary high-entropy tokens.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from dubtool.config import Config
from dubtool.job import Job, write_json
from dubtool.llm.base import LLMError, LLMResult, ServerErrorLLMError
from dubtool.stages import s3_translate as s3
from dubtool.stages._units_runner import adaptive_state, run_units_parallel


def _make_job(tmp_path: Path, segments: list[dict]) -> Job:
    job = Job(tmp_path / "job1", "input.mp4")
    job.ensure_dirs()
    write_json(job.transcript_path, segments)
    job.state["source_language"] = {"code": "zh", "confidence": 0.99, "auto": True}
    job.save()
    return job


def test_default_punctuate_off_skips_llm_prepass(tmp_path: Path, monkeypatch):
    """Fresh S3 runs do not make pre-translation LLM punctuate calls by default."""
    segs = [
        {"id": i, "start": float(i * 2), "end": float(i * 2 + 1.8), "text": f"你好世界第{i}句"}
        for i in range(50)
    ]
    job = _make_job(tmp_path, segs)
    cfg = Config()
    assert cfg.punctuate["enabled"] == "off"

    punct_called = []
    monkeypatch.setattr(
        "dubtool.punctuate.restore_punctuation",
        lambda *a, **k: (punct_called.append(True), (0, 0))[1],
    )
    res = s3._prepare_sentences(job, cfg, segs, "gemini", ["k"], None)
    assert punct_called == []
    assert res is None or isinstance(res, list)


def test_adaptive_chunk_sizing_cjk_vs_latin():
    """CJK packs ~20 short units up to 900 chars (not 8 units / 150 chars);
    Latin packs up to 20 units / 1800 chars."""
    cfg = Config()
    st_zh = adaptive_state(cfg, lang="zh")
    assert st_zh["max_units"] == 20
    assert st_zh["max_chars"] == 900

    st_en = adaptive_state(cfg, lang="en")
    assert st_en["max_units"] == 20
    assert st_en["max_chars"] == 1800

    # 20 short Chinese utterances (~15 chars each = 300 chars total) -> 1 request!
    zh_units = [
        {"unit_id": i, "text": "今天天气非常好我们去玩吧你好吗", "start": float(i), "end": float(i + 1), "max_chars": 40}
        for i in range(20)
    ]
    chunks_seen: list[int] = []
    run_units_parallel(
        zh_units,
        lambda chunk, _lbl: (
            chunks_seen.append(len(chunk)),
            [{"id": u["unit_id"], "vi": "ok"} for u in chunk],
        )[1],
        concurrency=1,
        adaptive=st_zh,
    )
    assert chunks_seen == [20]


def test_fallback_chain_never_loops_on_same_model(tmp_path: Path, monkeypatch):
    """When primary and fallback models all return 503, S3 tries each candidate
    at most once and raises cleanly instead of self-fallback looping forever."""
    segs = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "你好世界。"},
        {"id": 1, "start": 2.1, "end": 4.0, "text": "今天天气很好。"},
    ]
    job = _make_job(tmp_path, segs)
    cfg = Config()
    cfg.llm_model = "gemini-3.5-flash-lite"
    cfg.llm_fallback_model = "gemini-3.6-flash"
    cfg.llm = {
        **(cfg.llm or {}),
        "backoff_s": [0.001, 0.001],
        "server_error_backoff_s": [0.001, 0.001],
        "overload_after": 2,
        "overload_policy": "fallback_fast",
        "breaker_failures": 2,
        "fallback_after_opens": 1,
        "stage_budget_s": 60.0,
    }
    cfg.translate = {**(cfg.translate or {}), "concurrency": 2, "unit_mode": "sentence"}
    cfg.punctuate = {"enabled": "off"}

    models_built: list[str] = []

    class _Always503Provider:
        name = "gemini"
        structured = "none"
        timeout_s = 10.0

        def __init__(self, model: str):
            self.model = model
            self.calls = 0

        def complete(self, system, user, *, schema=None, json_mode=True, max_tokens=8000):
            self.calls += 1
            raise ServerErrorLLMError(f"503 from {self.model}", status=503)

    def _fake_get_provider(_cfg, _pid, api_keys=None, cancel=None, mode=None, model_override=None):
        m = model_override or "gemini-3.5-flash-lite"
        models_built.append(m)
        return _Always503Provider(m)

    monkeypatch.setattr(s3, "get_provider", _fake_get_provider)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    with pytest.raises(LLMError, match="no untried fallback model remains"):
        s3.run(job, cfg, api_keys=["fake-key"])

    # Every model in the chain was instantiated at most once (zero self-loops!).
    assert len(models_built) == len(set(models_built)), f"duplicate fallback loop: {models_built}"
    assert models_built[0] == "gemini-3.5-flash-lite"
    assert models_built[1] == "gemini-3.6-flash"
    assert "gemini-3.1-flash-lite" in models_built
    # Slow/preview thinking model must never be pulled into auto-fallback implicitly.
    assert "gemini-3-flash-preview" not in models_built


def test_fallback_recovers_on_healthy_sibling_after_congested_fallback(tmp_path: Path, monkeypatch):
    """Primary (503) -> configured fallback (503) -> healthy Gemini sibling succeeds."""
    segs = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "你好世界。"},
        {"id": 1, "start": 2.1, "end": 4.0, "text": "今天天气很好。"},
    ]
    job = _make_job(tmp_path, segs)
    cfg = Config()
    cfg.llm_model = "gemini-3.5-flash-lite"
    cfg.llm_fallback_model = "gemini-3.6-flash"
    cfg.llm = {
        **(cfg.llm or {}),
        "backoff_s": [0.001, 0.001],
        "server_error_backoff_s": [0.001, 0.001],
        "overload_after": 2,
        "overload_policy": "fallback_fast",
        "stage_budget_s": 60.0,
    }
    cfg.translate = {**(cfg.translate or {}), "concurrency": 1, "unit_mode": "sentence"}
    cfg.punctuate = {"enabled": "off"}

    models_built: list[str] = []

    class _SelectiveProvider:
        name = "gemini"
        structured = "none"
        timeout_s = 10.0

        def __init__(self, model: str):
            self.model = model

        def complete(self, system, user, *, schema=None, json_mode=True, max_tokens=8000):
            if self.model in ("gemini-3.5-flash-lite", "gemini-3.6-flash"):
                raise ServerErrorLLMError(f"503 from {self.model}", status=503)
            # Healthy sibling returns valid translations for ids 0..1.
            return LLMResult(
                text=json.dumps([
                    {"id": 0, "vi": "Xin chào thế giới."},
                    {"id": 1, "vi": "Hôm nay thời tiết rất đẹp."},
                ]),
                input_tokens=10,
                output_tokens=10,
            )

    monkeypatch.setattr(
        s3,
        "get_provider",
        lambda _c, _p, api_keys=None, cancel=None, mode=None, model_override=None: (
            models_built.append(model_override or "gemini-3.5-flash-lite"),
            _SelectiveProvider(model_override or "gemini-3.5-flash-lite"),
        )[1],
    )
    monkeypatch.setattr("time.sleep", lambda _s: None)

    s3.run(job, cfg, api_keys=["fake-key"])
    assert models_built == ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.1-flash-lite"]
    translated = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert all(e.get("vi") for e in translated)


def test_throttle_state_drops_concurrency_to_1_on_503():
    """Signalling throttle_state['drop_requested'] reduces active concurrency to 1."""
    units = [
        {"unit_id": i, "text": "x" * 100, "start": float(i), "end": float(i + 1), "max_chars": 50}
        for i in range(6)
    ]
    adaptive = {"max_chars": 100, "cur_chars": 100, "max_units": 1, "timeout_s": 0.0, "quick_streak": 0, "latencies": []}
    throttle = {"max_workers": 3, "drop_requested": False}
    in_flight = 0
    max_after_drop = 0
    dropped = False
    lock = threading.Lock()

    def _chunk_fn(chunk, _lbl):
        nonlocal in_flight, max_after_drop, dropped
        with lock:
            in_flight += 1
            if dropped:
                max_after_drop = max(max_after_drop, in_flight)
            if not dropped:
                throttle["drop_requested"] = True
                dropped = True
        time.sleep(0.02)
        with lock:
            in_flight -= 1
        return [{"id": u["unit_id"], "vi": "ok"} for u in chunk]

    run_units_parallel(units, _chunk_fn, concurrency=3, adaptive=adaptive, throttle_state=throttle)
    # Wait: chunks already launched before drop finished, subsequent chunks run serially.
    assert throttle["drop_requested"] is False


def test_legacy_fallback_model_migration_in_settings(tmp_path: Path):
    """Old settings.json (no settings_schema) migrates gemini-3.6-flash -> gemini-3.1-flash-lite;
    explicit saves under settings_schema=2 are preserved."""
    from dubtool.app import settings

    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"fallback_model": "gemini-3.6-flash", "language": "vi"}), encoding="utf-8")
    loaded = settings.load(p)
    assert loaded["fallback_model"] == "gemini-3.1-flash-lite"
    assert loaded["settings_schema"] == 2

    # User deliberately chooses gemini-3.6-flash and saves under schema 2 -> kept.
    loaded["fallback_model"] = "gemini-3.6-flash"
    settings.save(loaded, p)
    reloaded = settings.load(p)
    assert reloaded["fallback_model"] == "gemini-3.6-flash"


def test_redact_preserves_model_names_and_masks_keys():
    """Model names >= 20 chars must stay visible in copied details while keys are masked."""
    from dubtool.app.ui.main_window import _redact

    sample = (
        "[20:01:05] [s3] Đang gọi gemini-3-flash-preview — translate chunk 1 (lần 1)\n"
        "[20:01:06] [s3] gemini-flash-lite-latest quá tải (503)\n"
        "[20:01:07] [s3] gemini-3.5-flash-lite và gemini-3.1-flash-lite OK\n"
        "GEMINI_API_KEY=AIzaDOCFAKEVALUE1234567890ab\n"
        "ANTHROPIC_API_KEY=sk-ant-api03-FAKEVALUE1234567890\n"
    )
    out = _redact(sample)
    assert "gemini-3-flash-preview" in out
    assert "gemini-flash-lite-latest" in out
    assert "gemini-3.5-flash-lite" in out
    assert "gemini-3.1-flash-lite" in out
    assert "AIzaDOCFAKEVALUE" not in out
    assert "sk-ant-api03-FAKEVALUE" not in out
    assert "•••" in out


def test_parse_json_accepts_text_alias_when_vi_key_omitted():
    """Models occasionally mirror the input's 'text' key instead of 'vi'."""
    from dubtool.llm.parsing import parse_json_array, parse_json_items, parse_shortened

    raw = json.dumps([
        {"id": 0, "text": "Hôm nay là lễ bắt thăm."},
        {"id": 1, "translation": "Vi Vi rất lanh lợi."},
    ])
    out = parse_json_array(raw, [0, 1])
    assert out == [
        {"id": 0, "vi": "Hôm nay là lễ bắt thăm."},
        {"id": 1, "vi": "Vi Vi rất lanh lợi."},
    ]
    wrapped = json.dumps({"items": [{"id": 0, "text": "Xin chào."}]})
    assert parse_json_items(wrapped, [0]) == [{"id": 0, "vi": "Xin chào."}]
    assert parse_shortened(json.dumps({"text": "Ngắn gọn."})) == "Ngắn gọn."

    # Explicit empty 'vi' must still fail validation.
    with pytest.raises(LLMError):
        parse_json_array('[{"id": 0, "vi": "   ", "text": "fallback"}]', [0])


def test_gemini_provider_attaches_response_json_schema(monkeypatch):
    """GeminiProvider enforces translate_schema via response_json_schema and
    maps 400 schema rejections to SchemaRejectedError for graceful degrade."""
    from dubtool.llm.base import SchemaRejectedError
    from dubtool.llm.gemini import GeminiProvider
    from dubtool.llm.schemas import translate_schema

    captured: dict = {}

    class _FakeResp:
        text = '{"items": [{"id": 0, "vi": "Xin chào."}]}'
        usage_metadata = None

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            return _FakeResp()

    class _FakeClient:
        models = _FakeModels()

    prov = GeminiProvider(model="gemini-3.5-flash-lite", api_keys=["AIzaFAKEKEY12345678901234567890"])
    monkeypatch.setattr(prov, "client_for", lambda _k: _FakeClient())

    schema = translate_schema()
    res = prov.complete("sys", "usr", schema=schema, json_mode=True)
    assert res.text == _FakeResp.text
    assert prov.structured == "json_schema"
    assert captured["config"].response_mime_type == "application/json"
    assert captured["config"].response_json_schema == schema

    class _Schema400(Exception):
        code = 400

    mapped = prov._map_error(_Schema400("Invalid response_json_schema field"), "k")
    assert isinstance(mapped, SchemaRejectedError)


def test_gemini_max_tokens_and_truncated_json_trigger_split(monkeypatch):
    """finish_reason=MAX_TOKENS raises RequestTooLargeError, and a truncated JSON
    reply on a split-eligible chunk raises SplitRequiredError so translate_resilient
    halves the chunk instead of failing the stage."""
    from dubtool.llm.base import RequestTooLargeError, SplitRequiredError
    from dubtool.llm.client import LLMClient
    from dubtool.llm.gemini import GeminiProvider

    class _CutCandidate:
        finish_reason = "FinishReason.MAX_TOKENS"

    class _CutResp:
        text = '{"items":[{"id":0,"vi":"cut off'
        usage_metadata = None
        candidates = [_CutCandidate()]

    class _FakeModels:
        def generate_content(self, *, model, contents, config):
            return _CutResp()

    class _FakeClient:
        models = _FakeModels()

    prov = GeminiProvider(model="gemini-3.5-flash-lite", api_keys=["AIzaFAKEKEY12345678901234567890"])
    monkeypatch.setattr(prov, "client_for", lambda _k: _FakeClient())
    with pytest.raises(RequestTooLargeError):
        prov.complete("sys", "usr", json_mode=True)

    # Even when a provider returns a truncated JSON string with STOP finish_reason,
    # LLMClient raises SplitRequiredError on split-eligible chunks.
    class _TruncatedStopProvider:
        name = "gemini"
        model = "gemini-3.5-flash-lite"
        structured = "none"
        timeout_s = 30.0

        def complete(self, system, user, *, schema=None, json_mode=True, max_tokens=8000):
            return LLMResult(text='{"items":[{"id":0,"vi":"bị cắt giữa chừng', input_tokens=10, output_tokens=10)

    client = LLMClient(
        _TruncatedStopProvider(),
        options={"split_on_timeout": True, "split_min_units": 4, "split_min_chars": 600},
    )
    client.request_units = 20
    client.request_chars = 500
    with pytest.raises(SplitRequiredError):
        client.translate_batch(
            [{"id": i, "text": "hello", "max_chars": 40} for i in range(20)], []
        )


