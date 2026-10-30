"""Tests for the LLM fault injection (AGENT_UPDATE_8.md 2.5, 10)."""

from __future__ import annotations

import pytest

from dubtool import llm as llm_pkg
from dubtool.llm.base import LLMResult, ServerErrorLLMError, TimeoutLLMError


class FakeProvider:
    name = "fake"
    model = "fake-model"
    timeout_s = 10.0

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, *, json_mode=True, max_tokens=8000) -> LLMResult:
        self.calls += 1
        return LLMResult(text='{"lines": ["ok"]}', input_tokens=1, output_tokens=1)


@pytest.fixture(autouse=True)
def _clear_counters(monkeypatch):
    llm_pkg._reset_llm_faults()
    monkeypatch.delenv("DUBTOOL_FAULTS", raising=False)
    yield
    llm_pkg._reset_llm_faults()


def _wrap(monkeypatch, spec: dict[str, int]):
    """Build a fake provider and its fault-wrapped wrapper."""
    monkeypatch.setattr(llm_pkg, "_faults_active", lambda: spec)
    inner = FakeProvider()
    return inner, llm_pkg._FaultProvider(inner, spec)


def test_503_first_fails_then_passes(monkeypatch):
    inner, wrapped = _wrap(monkeypatch, {"503-first": 2})
    with pytest.raises(ServerErrorLLMError) as exc:
        wrapped.complete("s", "u")
    assert exc.value.status == 503
    with pytest.raises(ServerErrorLLMError):
        wrapped.complete("s", "u")
    assert inner.calls == 0  # the fault never reached the real provider
    wrapped.complete("s", "u")
    assert inner.calls == 1


def test_counters_are_process_global_not_per_provider(monkeypatch):
    """A rebuilt provider (OverloadFallbackFast) must NOT re-fault forever."""
    inner1, w1 = _wrap(monkeypatch, {"503-first": 1})
    inner2, w2 = _wrap(monkeypatch, {"503-first": 1})
    with pytest.raises(ServerErrorLLMError):
        w1.complete("s", "u")
    w2.complete("s", "u")  # the rebuilt client succeeds where the 1st faulted
    assert inner1.calls == 0 and inner2.calls == 1


def test_timeout_first_raises_timeout_llm_error(monkeypatch):
    inner, wrapped = _wrap(monkeypatch, {"timeout-first": 1})
    with pytest.raises(TimeoutLLMError):
        wrapped.complete("s", "u")
    wrapped.complete("s", "u")
    assert inner.calls == 1


def test_parse_specs_accepts_llm_faults():
    from dubtool import faults as faults_mod

    parsed = faults_mod.parse_specs(["llm:503-first=4", "tts:fail-first=6"])
    assert parsed == {"llm": {"503-first": 4}, "tts": {"fail-first": 6}}
    with pytest.raises(ValueError):
        faults_mod.parse_specs(["llm:bogus=1"])


def test_get_provider_wraps_when_faults_declared(monkeypatch):
    monkeypatch.setenv("DUBTOOL_FAULTS", "llm:503-first=1")
    monkeypatch.setattr(
        llm_pkg, "_faults_active", lambda: {"503-first": 1}
    )
    from dubtool.llm.gemini import GeminiProvider

    provider = llm_pkg.get_provider(
        _Cfg(), "gemini", api_keys=["k-test-only"],
        model_override="m-1",
    )
    assert isinstance(provider, llm_pkg._FaultProvider)
    assert isinstance(provider._inner, GeminiProvider)
    assert provider.model == "m-1"


def test_get_provider_unwrapped_without_faults(monkeypatch):
    monkeypatch.delenv("DUBTOOL_FAULTS", raising=False)
    monkeypatch.setattr(llm_pkg, "_faults_active", lambda: {})
    provider = llm_pkg.get_provider(_Cfg(), "ollama")
    assert not isinstance(provider, llm_pkg._FaultProvider)
    assert provider.model == llm_pkg.PROVIDER_DEFAULTS["ollama"]["model"]


class _Cfg:
    llm = {}
