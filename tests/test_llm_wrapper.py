"""Wrapper behavior tests: rate limits, transient retries, tokens (offline)."""

from __future__ import annotations

import pytest

from dubtool.llm.base import LLMError, LLMResult, RateLimitError
from dubtool.llm.client import LLMClient
from dubtool.llm import parsing


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    def complete(self, system, user, *, json_mode=True, max_tokens=8000) -> LLMResult:
        index = self.calls
        self.calls += 1
        outcome = self.behavior[min(index, len(self.behavior) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResult(text=outcome, input_tokens=10, output_tokens=5)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("dubtool.llm.client.time.sleep", lambda seconds: None)


def test_rate_limit_twice_then_success(monkeypatch):
    ids = [0]
    provider = FakeProvider(
        [
            RateLimitError("fake", "quota exhausted"),
            RateLimitError("fake", "slow down"),
            f'{{"id": 0, "vi": "ok vt"}}',
        ]
    )
    client = LLMClient(provider)
    monkeypatch.setattr(
        "dubtool.llm.parsing.strip_code_fences", lambda t: t, raising=False
    )
    # Use translate path: json array response
    provider.behavior = [
        RateLimitError("fake", "429"),
        RateLimitError("fake", "429 again"),
        '[{"id": 0, "vi": "Chào"}]',
    ]
    out = client.translate_batch([{"id": 0, "text": "Hello", "max_chars": 10}], [], None)
    assert out == [{"id": 0, "vi": "Chào"}]
    assert provider.calls == 3


def test_persistent_rate_limit_gives_clear_error():
    provider = FakeProvider([RateLimitError("gemini", "429")])
    client = LLMClient(provider)
    with pytest.raises(LLMError, match="rate limit was hit"):
        client.translate_batch([{"id": 1, "text": "Hello", "max_chars": 10}], [], None)


def test_transient_error_retried_then_success():
    provider = FakeProvider(
        [
            LLMError("provider hiccup"),
            '[{"id": 2, "vi": "ok"}]',
        ]
    )
    client = LLMClient(provider)
    out = client.translate_batch([{"id": 2, "text": "Hi", "max_chars": 5}], [], None)
    assert out == [{"id": 2, "vi": "ok"}]
    assert client.total_input_tokens == 10
    assert client.total_output_tokens == 5


def test_validation_failure_appends_error_and_recovers():
    provider = FakeProvider(["```json\n[not json\n```", '[{"id": 0, "vi": "Tốt"}]'])
    client = LLMClient(provider)
    out = client.translate_batch([{"id": 0, "text": "Hi", "max_chars": 5}], [], None)
    assert out == [{"id": 0, "vi": "Tốt"}]
    assert provider.calls == 2


def test_giving_up_logs_raw_reply_with_cause(caplog):
    """When every validation attempt fails, the last raw reply (capped at
    500 chars) and its likely cause land in the log (AGENT_UPDATE_5.md 5.3)."""
    import logging

    provider = FakeProvider(["쪽 partial answer without closing"] )
    client = LLMClient(provider)
    with caplog.at_level(logging.WARNING, logger="dubtool"):
        with pytest.raises(LLMError, match="giving up"):
            client.translate_batch([{"id": 0, "text": "Hi", "max_chars": 5}], [], None)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "cut off" in joined
    assert "partial answer without closing" in joined


def test_markdown_fence_is_diagnosed(caplog):
    import logging

    provider = FakeProvider([
        "```json\n{\"vi\": \"x\"}\n``` trailing noise",
        "still bad",
        "also bad",
    ])
    client = LLMClient(provider)
    with caplog.at_level(logging.WARNING, logger="dubtool"):
        with pytest.raises(LLMError, match="giving up"):
            client.translate_batch([{"id": 0, "text": "Hi", "max_chars": 5}], [], None)
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "markdown code fence" in joined
