"""Unit & integration tests for the Groq Cloud LLM provider."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dubtool.config import Config
from dubtool.job import Job, write_json
from dubtool.llm import catalog as catalog_mod, get_provider
from dubtool.llm.base import (
    InvalidKeyError,
    LLMResult,
    RateLimitError,
    RequestTooLargeError,
    ServerErrorLLMError,
)
from dubtool.llm.groq import GROQ_BASE_URL, GroqProvider, is_groq_chat_model
from dubtool.stages import s3_translate as s3


def test_groq_models_in_catalog_and_not_paid():
    cat = catalog_mod.load()
    for mid in (
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    ):
        assert mid in cat, mid
        info = cat[mid]
        assert info.provider == "groq"
        assert info.paid is False
        assert info.translate_concurrency == 1
    assert catalog_mod.infer_provider("qwen/qwen3.8-27b") == "groq"
    assert catalog_mod.infer_provider("openai/gpt-oss-120b") == "groq"
    assert catalog_mod.infer_provider("openai/gpt-oss-20b") == "groq"


def test_groq_key_detection_and_redaction():
    from dubtool.app import diagnostics, failguard, secrets
    from dubtool.app.ui.main_window import _redact

    fake = "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    assert secrets.detect_provider(fake) == "groq"
    assert secrets.account_for("groq") == "groq_api_keys"
    assert fake not in diagnostics.scrub_line(f"GROQ_API_KEY={fake}")
    assert fake not in failguard.scrub(f"err {fake}")
    assert fake not in _redact(f"key={fake} model=llama-3.3-70b-versatile")
    assert "llama-3.3-70b-versatile" in _redact(f"key={fake} model=llama-3.3-70b-versatile")


def test_groq_chat_model_filter():
    assert is_groq_chat_model("llama-3.3-70b-versatile") is True
    assert is_groq_chat_model("qwen/qwen3-32b") is True
    assert is_groq_chat_model("whisper-large-v3") is False
    assert is_groq_chat_model("playai-tts") is False
    assert is_groq_chat_model("meta-llama/llama-guard-4-12b") is False


def test_groq_provider_complete_qwen3_think_strip_and_json_fallback(monkeypatch):
    calls: list[dict] = []

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                # Simulate json_validate_failed on first attempt -> auto degrades!
                exc = Exception("Error code: 400 - json_validate_failed")
                exc.status_code = 400
                exc.body = {"error": {"code": "json_validate_failed"}}
                raise exc
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content='<think>internal reasoning</think>{"items":[{"id":0,"vi":"Xin chào."}]}'
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=15),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    prov = GroqProvider(model="qwen/qwen3.8-27b", api_keys=["gsk_FAKEKEY12345678901234567890"])
    monkeypatch.setattr(prov, "_client_for", lambda _k: fake_client)

    res = prov.complete("sys", "usr", json_mode=True)
    assert res.text == '{"items":[{"id":0,"vi":"Xin chào."}]}'
    assert res.input_tokens == 20 and res.output_tokens == 15
    assert len(calls) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["extra_body"] == {"reasoning_effort": "none"}
    assert "response_format" not in calls[1]


def test_groq_provider_length_truncation_and_error_mapping(monkeypatch):
    class _CutCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(content='{"items":[{"id":0,"vi":"cut'),
                    )
                ],
                usage=None,
            )

    prov = GroqProvider(model="llama-3.3-70b-versatile", api_keys=["gsk_FAKEKEY12345678901234567890"])
    monkeypatch.setattr(
        prov, "_client_for", lambda _k: SimpleNamespace(chat=SimpleNamespace(completions=_CutCompletions()))
    )
    with pytest.raises(RequestTooLargeError):
        prov.complete("sys", "usr")

    e401 = Exception("unauthorized")
    e401.status_code = 401
    assert isinstance(prov._map_error(e401), InvalidKeyError)

    e429_min = Exception("Rate limit reached for tokens per minute (TPM)")
    e429_min.status_code = 429
    e429_min.headers = {"retry-after": "12"}
    mapped_min = prov._map_error(e429_min)
    assert isinstance(mapped_min, RateLimitError)
    assert mapped_min.kind == "minute"
    assert mapped_min.retry_after == 12.0

    e429_day = Exception("Rate limit reached for requests per day (RPD)")
    e429_day.status_code = 429
    mapped_day = prov._map_error(e429_day)
    assert isinstance(mapped_day, RateLimitError)
    assert mapped_day.kind == "day"

    e503 = Exception("service unavailable")
    e503.status_code = 503
    assert isinstance(prov._map_error(e503), ServerErrorLLMError)


def test_get_provider_and_key_test_route_groq(monkeypatch):
    from dubtool.app import key_test

    cfg = Config()
    prov = get_provider(
        cfg,
        "groq",
        api_keys={"groq": ["gsk_FAKEKEY12345678901234567890"]},
        model_override="qwen/qwen3.8-27b",
    )
    assert isinstance(prov, GroqProvider)
    assert prov.model == "qwen/qwen3.8-27b"
    assert prov._base_url == GROQ_BASE_URL

    seen_urls: list[str] = []

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url=None, **_kw):
            seen_urls.append(str(base_url))
            self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[]))

    import openai as openai_mod

    monkeypatch.setattr(openai_mod, "OpenAI", _FakeOpenAI)
    assert key_test.run_key_test("gsk_FAKEKEY12345678901234567890", provider="groq") == "ok"
    assert seen_urls == [GROQ_BASE_URL]


def test_cross_provider_fallback_gemini_to_groq(tmp_path: Path, monkeypatch):
    """Gemini primary (503) switches cleanly to user's Groq fallback_model."""
    job = Job(tmp_path / "j1", "input.mp4")
    job.ensure_dirs()
    write_json(job.transcript_path, [{"id": 0, "start": 0.0, "end": 2.0, "text": "你好世界。"}])
    job.state["source_language"] = {"code": "zh", "confidence": 0.99, "auto": True}
    job.save()

    cfg = Config()
    cfg.llm_model = "gemini-3.5-flash-lite"
    cfg.llm_fallback_model = "qwen/qwen3.8-27b"
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

    built: list[tuple[str, str]] = []

    class _Prov:
        structured = "none"
        timeout_s = 10.0

        def __init__(self, pid: str, model: str):
            self.name = pid
            self.model = model

        def complete(self, system, user, *, schema=None, json_mode=True, max_tokens=8000):
            if self.name == "gemini":
                raise ServerErrorLLMError("503", status=503)
            return LLMResult(
                text='{"items":[{"id":0,"vi":"Xin chào thế giới."}]}',
                input_tokens=10,
                output_tokens=10,
            )

    monkeypatch.setattr(
        s3,
        "get_provider",
        lambda _c, pid, api_keys=None, cancel=None, mode=None, model_override=None: (
            built.append((pid, model_override or "")),
            _Prov(pid, model_override or ""),
        )[1],
    )
    monkeypatch.setattr("time.sleep", lambda _s: None)

    s3.run(
        job,
        cfg,
        api_keys={
            "gemini": ["AIzaFAKE12345678901234567890"],
            "groq": ["gsk_FAKE12345678901234567890"],
        },
    )
    assert built == [("gemini", "gemini-3.5-flash-lite"), ("groq", "qwen/qwen3.8-27b")]
    translated = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert translated[0]["vi"] == "Xin chào thế giới."


def test_stable_job_id_reuses_s1_s2_across_model_switch(tmp_path: Path):
    """Switching LLM provider/model must reuse the same job_id so S1/S2
    (Whisper transcription) are skipped instead of re-running for 15+ min."""
    from dubtool.api import DubOptions, _stable_job_id

    vid = tmp_path / "demo.mp4"
    vid.write_bytes(b"mp4data")
    id_gemini = _stable_job_id(vid, DubOptions(provider="gemini", model="gemini-3.5-flash-lite"))
    id_groq = _stable_job_id(vid, DubOptions(provider="groq", model="qwen/qwen3.8-27b"))
    assert id_gemini == id_groq
