"""AGENT_UPDATE_8 task 2 tests: the redesigned Gemini retry behavior.

No nested transport retries (one inner loop backoff), 503/504 handled as
model-wide server errors with an exponential-with-jitter schedule (no key
rotation), overload policy after `overload_after` consecutive failures,
client-side timeouts split the chunk in two, and no request is logged
without the model, kind and size.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dubtool.llm.base import (
    InternalLLMError,
    LLMError,
    OverloadFallbackFast,
    ServerErrorLLMError,
    TimeoutLLMError,
)


class FakeProvider:
    name = "gemini"

    def __init__(self, script, model="fake-model", timeout_s=None):
        self.model = model
        self.timeout_s = timeout_s
        self.script = list(script)  # values or exceptions per complete() call
        self.calls = 0
        self.requests: list[tuple[str, str]] = []

    def complete(self, system, user, *, json_mode=True, max_tokens=8000):
        self.calls += 1
        self.requests.append((system, user))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(script, options=None, on_overload=None):
    from dubtool.llm.client import LLMClient

    provider = FakeProvider(script)
    return LLMClient(provider, cancel=None, options=options, on_overload=on_overload), provider


def _ok(text='[{"id": 1, "vi": "xin chao"}]'):
    from dubtool.llm.base import LLMResult

    return LLMResult(text=text, input_tokens=10, output_tokens=5)


def test_success_resets_the_consecutive_count():
    """A successful call clears the consecutive server-error count."""
    client, _provider = _client([_ok()])
    client.consecutive_server_errors = 2
    client.translate_batch([{"id": 1, "text": "hi", "max_chars": 20}], [])
    assert client.consecutive_server_errors == 0


def test_no_nested_retries(monkeypatch):
    """AGU8 3.1: a persistent server error yields at most MAX_RETRIES
    requests (no validation retry on a transport failure, ever)."""
    from dubtool.llm.client import MAX_RETRIES

    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    client, provider = _client([ServerErrorLLMError("503")] * 10)
    with pytest.raises(LLMError):
        client.translate_batch([{"id": 1, "text": "hi", "max_chars": 20}], [])
    assert provider.calls == MAX_RETRIES
    assert client.consecutive_server_errors == 3


def test_server_error_backoff_schedule(monkeypatch):
    """503 backoff follows llm.server_error_backoff_s (10/30/90) with no
    key rotation knob in the new schedule."""
    from dubtool.llm.client import MAX_RETRIES

    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    client, provider = _client([ServerErrorLLMError("503")] * 10)
    with pytest.raises(LLMError):
        client.translate_batch([{"id": 1, "text": "hi", "max_chars": 20}], [])
    assert len(sleeps) == MAX_RETRIES - 1
    # 10 and 30 in some jittered form (0.8..1.2), capped at 90.
    assert 8 <= sleeps[0] <= 12
    assert 24 <= sleeps[1] <= 36


def test_overload_policy_counts_consecutive_failures(monkeypatch):
    """The policy fires after `overload_after` CONSECUTIVE server errors."""
    _ = test_overload_policy_counts_consecutive_failures  # keep name
    monkeypatch.setattr("time.sleep", lambda s: None)
    asked: list[bool] = []

    def on_overload():
        asked.append(True)
        return "fallback_fast"

    script = [
        ServerErrorLLMError("503"),  # consecutive 1
        ServerErrorLLMError("503"),  # consecutive 2 -> the policy decides
    ]
    client, provider = _client(list(script), options={"overload_after": 2}, on_overload=on_overload)
    with pytest.raises(OverloadFallbackFast):
        client.translate_batch([{"id": 1, "text": "hi", "max_chars": 20}], [])
    # The decision came after 2 consecutive server errors.
    assert provider.calls == 2
    assert asked == [True]


def test_overload_fallback_fast_raised():
    from dubtool.llm.client import MAX_RETRIES

    client, provider = _client(
        [ServerErrorLLMError("503")] * 5, options={"overload_after": 2},
        on_overload=lambda: "fallback_fast",
    )
    with pytest.raises(OverloadFallbackFast) as info:
        client.translate_batch([{"id": 1, "text": "hi", "max_chars": 20}], [])
    assert "fake-model" in str(info.value)
    # 2 consecutive errors, then the policy raises — exactly 2 calls.
    assert provider.calls == 2


def test_split_on_timeout_splits_the_chunk(monkeypatch):
    """AGU8 3.6: translate_resilient splits a timed-out chunk in two."""
    from dubtool.stages.s3_translate import translate_resilient

    holder: dict = {}
    stats: dict = {}

    def make(payloads, ctx):
        nonlocal holder
        holder["client"] = FakeClient(payloads)
        return holder["client"]

    class FakeClient:
        def __init__(self, payloads):
            self.payloads = payloads
            self.request_label = ""
            self.total = 0

        def translate_batch(self, payloads, ctx, glossary=None, brief=None, pronouns=None):
            self.total += 1
            if len(payloads) >= 6:  # halves of 4 succeed; 8-item chunk times out
                raise TimeoutLLMError("answer took too long")
            return [{"id": p["id"], "vi": "v" + str(p["id"])} for p in payloads]

    def translate_call(client, payloads, ctx):
        return client.translate_batch(payloads, ctx)

    holder["client"] = FakeClient([])
    payloads = [{"id": i, "text": "t", "max_chars": 10} for i in range(8)]
    out = translate_resilient(
        holder, translate_call, payloads, [], "chunk 1",
        on_fallback=lambda m: None, split_on_timeout=True,
        split_min_units=4, stats=stats,
    )
    assert [r["id"] for r in out] == list(range(8))
    assert stats["splits"] >= 1
    # One timed-out whole-chunk call, then the two halves succeed.
    assert holder["client"].total == 3


def test_split_disabled_or_small_chunks_not_split():
    """split_on_timeout=false never splits; small chunks are retried whole."""
    from dubtool.stages.s3_translate import translate_resilient

    class FakeClient:
        calls = 0
        request_label = ""

        def translate_batch(self, payloads, ctx, glossary=None, brief=None, pronouns=None):
            self.calls += 1
            raise TimeoutLLMError("too slow")

    payloads = [{"id": i, "text": "t", "max_chars": 5} for i in range(8)]

    holder = {"client": FakeClient()}
    with pytest.raises(LLMError):
        translate_resilient(
            holder, lambda c, p, x: c.translate_batch(p, x), payloads, [],
            "chunk", on_fallback=lambda m: None, split_on_timeout=False,
            split_min_units=4, stats={"splits": 0},
        )
    assert holder["client"].calls == 1

    holder = {"client": FakeClient()}
    with pytest.raises(LLMError):
        translate_resilient(
            holder, lambda c, p, x: c.translate_batch(p, x), payloads[:2], [],
            "chunk", on_fallback=lambda m: None, split_on_timeout=True,
            split_min_units=4, stats={"splits": 0},
        )
    assert holder["client"].calls == 1


def test_failed_after_timeout_helpers():
    from dubtool.llm.base import failed_after_timeout, _transport_kind

    assert failed_after_timeout(TimeoutLLMError("client timeout"))
    assert failed_after_timeout(ServerErrorLLMError("deadline", status=504))
    assert not failed_after_timeout(ServerErrorLLMError("unavailable", status=503))
    assert not failed_after_timeout(LLMError("no"))


def test_request_started_log_names_model_and_size(caplog):
    """AGU8 3.5: the request log names the model, kind and size, and the
    per-mode timeout comes from the provider."""
    from dubtool.llm.client import LLMClient

    provider = FakeProvider([_ok()], timeout_s=90.0)
    client = LLMClient(provider)
    client.request_label = "translate chunk 1/2 (3 units, 42 chars)"
    with caplog.at_level(logging.INFO, logger="dubtool"):
        client.translate_batch([{"id": 1, "text": "hi", "max_chars": 5}], [])
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "translate chunk 1/2" in text
    assert "fake-model" in text
    assert "timeout 90" in text
    assert "42 chars" in text


def test_no_timeout_suffix_when_provider_has_none(caplog):
    """A provider without a per-mode cap logs no timeout clause."""
    provider = FakeProvider([_ok()], timeout_s=None)
    from dubtool.llm.client import LLMClient

    client = LLMClient(provider)
    client.request_label = "x"
    with caplog.at_level(logging.INFO, logger="dubtool"):
        client.translate_batch([{"id": 1, "text": "hi", "max_chars": 5}], [])
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "fake-model — x" in text
    assert "(timeout" not in text


def test_mode_dependent_timeout(tmp_path: Path):
    """get_provider(mode='fast') uses request_timeout_s_fast (AGU8 3.5)."""
    from dubtool.config import load as load_config
    from dubtool import llm as llm_mod

    config = load_config(tmp_path)  # defaults, no config.yaml needed
    config.llm["gemini"]["model"] = "m1"
    config.llm["request_timeout_s"] = 180
    config.llm["request_timeout_s_fast"] = 90
    p = llm_mod.get_provider(config, "gemini", api_keys=["real-key-123"], mode="fast")
    assert p.timeout_s == 90.0
    p = llm_mod.get_provider(config, "gemini", api_keys=["real-key-123"])
    assert p.timeout_s == 180.0
