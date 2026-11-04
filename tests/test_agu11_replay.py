"""AGENT_UPDATE_11 rule 1: replay the user's observed run before fixing.

The user ran the packaged exe on `test2` (16 units, 3,827 characters) and
the run sat for hours. From the exported log.txt / pipeline.log /
tts_failures.json (AGENT_UPDATE_11.md §0):

- the whole 16-unit chunk was sent at least six times; 504 DEADLINE_EXCEEDED
  arrived after the full 180 s client timeout, 503 UNAVAILABLE in between;
  the chunk was never split although llm.split_on_timeout was true;
- the overload policy ('ask') left no trace;
- the details panel shows NOTHING between 13:26:10 and 13:35:18 while the
  model requests were outstanding;
- in stage 4, each refused segment cost 93-498 s and one pass had a
  30.5-minute silent gap; a hung edge-tts request has no hard timeout.

These are replay tests against the REAL wrapper/stage code with a fake
provider and a fake clock; on the pre-update code several of them FAIL —
that is the point (rule 1: reproduce first, then fix).
"""

from __future__ import annotations

import json
import threading

import pytest

from tests.fake_clock import fast_clock
from dubtool.llm.base import LLMResult, ServerErrorLLMError, TimeoutLLMError
from dubtool.heartbeat import bind, reset, interval
from dubtool.stages.s3_translate import run_chunks_parallel


# --------------------------------------------------------------------------
# shared fake provider: refuses large requests the way the overloaded
# service did (504 DEADLINE_EXCEEDED / 503 UNAVAILABLE), answers quickly for
# small ones, and can block per request to emulate the 180 s client timeout.
class OverloadFakeProvider:
    name = "gemini"
    model = "gemini-fake-flash"
    timeout_s = 180.0

    def __init__(self, big_from: int = 12, fail_kinds=(504,)):
        self.big_from = big_from
        self.fail_kinds = list(fail_kinds)
        self.sends: list[tuple[float, int]] = []  # (virtual now, units)
        self.last_latency = 0.0
        self.expect_ids: list[int] | None = None

    def complete(self, system, user, *, json_mode=True, max_tokens=8000):
        # The payload ids are handed over by the test's translate_call (the
        # rendered prompt also contains context rows, so counting ids there
        # would mis-size the reply).
        units = len(self.expect_ids) if self.expect_ids else 0
        self.sends.append((_clock_now(), units))
        if units < self.big_from:
            return LLMResult(text=json.dumps(
                [{"id": i, "vi": f"dịch {i}"} for i in (self.expect_ids or [])]
            ), input_tokens=units * 10, output_tokens=units * 5)
        if self.fail_kinds:
            kind = self.fail_kinds.pop(0) if len(self.fail_kinds) > 1 else self.fail_kinds[0]
            self.fail_kinds  # noqa: B018  (kept alive by the caller)
            if kind == 504:
                raise ServerErrorLLMError("504 DEADLINE_EXCEEDED", status=504)
            raise ServerErrorLLMError("503 UNAVAILABLE (high demand)", status=503)
        return LLMResult(text=json.dumps(
            [{"id": i, "vi": f"dịch {i}"} for i in (self.expect_ids or [])]
        ), input_tokens=units * 10, output_tokens=units * 5)


class _NowProbe:
    """Tracks the shared fake clock across the replayed timeline."""

    def __init__(self):
        self.clock = None

    def __call__(self) -> float:
        return self.clock.now if self.clock is not None else 0.0


_now = _NowProbe()


def _clock_now() -> float:
    return _now()


def _big_payload(units: int = 16, chars: int = 239) -> list[dict]:
    return [
        {"id": i, "text": "x" * chars, "max_chars": 50} for i in range(units)
    ]


# --------------------------------------------------------------------------
def test_replay_translate_504_never_resends_the_big_request(monkeypatch):
    """After the FIRST 504 of a chunk the wrapper must split it in halves
    (llm.split_min_units, default 2) and never resend the identical big
    request again (AGENT_UPDATE_11.md 2.2)."""
    from tests.fake_clock import fast_clock
    from dubtool.llm.client import LLMClient
    from dubtool.stages.s3_translate import translate_resilient

    with fast_clock(monkeypatch=monkeypatch) as clock:
        _now.clock = clock
        provider = OverloadFakeProvider(big_from=12, fail_kinds=[504])
        holder: dict = {"client": LLMClient(provider, options={
            "server_error_backoff_s": [10, 30, 90],
            "overload_after": 2,
            "overload_policy": "fallback_fast",  # CLI/headless semantics
            "split_on_timeout": True,
            "split_min_units": 2,
        })}
        stats: dict = {"splits": 0, "fallback": False, "fast_model": None}

        def translate_call(client, payloads, ctx):
            # The stage drives these per chunk (AGU8 3.5 label + new units).
            client.request_label = f"translate chunk ({len(payloads)} units)"
            client.request_units = len(payloads)
            provider.expect_ids = [p["id"] for p in payloads]
            return client.translate_batch(payloads, ctx)

        def on_fallback(_from_model: str) -> None:
            stats["fallback"] = True

        out = translate_resilient(
            holder, translate_call, _big_payload(16), [], "translate chunk 1/1 (16 units, 3827 chars)",
            on_fallback=on_fallback, split_on_timeout=True, split_min_units=2,
            stats=stats,
        )
        assert [r["id"] for r in out] == list(range(16))
        big_sends = [s for s in provider.sends if s[1] >= 12]
        # The 3,827-character request went out exactly once; the service's
        # repeated 504s on it are answered with halves, not identical resends.
        assert len(big_sends) == 1, (
            f"the big request was resent {len(big_sends)} times: {provider.sends}"
        )
        # Halves took over: every other send is smaller.
        assert all(s[1] < 12 for s in provider.sends[1:])
        assert stats["splits"] == 1
        assert stats["fallback"] is False


def test_replay_dialog_is_consulted_again_after_more_overload(monkeypatch):
    """'ask' must ask again after each further `overload_after` failures
    when the user chose to wait (AGENT_UPDATE_11.md 2.3) — the old code
    consulted the user exactly once per run (client.overload_asked)."""
    from tests.fake_clock import fast_clock
    from dubtool.llm.client import LLMClient

    with fast_clock(monkeypatch=monkeypatch) as clock:
        _now.clock = clock
        provider = OverloadFakeProvider(big_from=0, fail_kinds=[504] * 8)
        consulted: list[str] = []

        def on_overload(info=None):
            waited = float(getattr(info or {}, "get", lambda *a: 0.0)("waited_s", 0.0))
            consulted.append(f"ask@{waited:.0f}s")
            return "wait"

        client = LLMClient(provider, options={
            "server_error_backoff_s": [10, 30, 90],
            "overload_after": 2,
            "overload_policy": "ask",
        }, on_overload=on_overload)
        client.request_units = 4  # small chunk: never split-eligible
        provider.expect_ids = list(range(4))
        with pytest.raises(Exception):
            client.translate_batch(_big_payload(4), [])
        # The observed series spans several requests of one stage: give up
        # on this batch, then the next chunk (same client) fails too.
        try:
            client.translate_batch(_big_payload(4), [])
        except Exception:
            pass
        assert len(consulted) >= 2, (
            f"'ask' consulted the user {len(consulted)} time(s) for the observed "
            "504/503 series — it must ask again after every further "
            "overload_after failures"
        )


def test_replay_translation_workers_emit_panel_events(monkeypatch):
    """A model call executed in a translation worker thread must still emit
    heartbeat progress into the details panel (AGENT_UPDATE_11.md 2.4) —
    on the old code the ContextVar binding never reached the worker thread,
    matching the nine silent minutes."""
    chunks = _big_payload(2), _big_payload(2), _big_payload(2)
    events: list[tuple[float, str]] = []
    token = bind(lambda kind, message: events.append((_clock_now(), message)))
    try:
        with fast_clock(monkeypatch=monkeypatch) as clock:
            _now.clock = clock

            class BlockingFakeProvider:
                name = "gemini"
                model = "fake"
                timeout_s = 180.0

                def __init__(self):
                    self.calls = 0

                def complete(self, system, user, *, json_mode=True, max_tokens=8000):
                    self.calls += 1
                    import time as t

                    t.sleep(3.0)  # fake clock: instant, but the wait IS scheduled
                    return LLMResult(text=json.dumps(
                        [{"id": 0, "vi": "dịch 0"}, {"id": 1, "vi": "dịch 1"}]
                    ))

            from dubtool.llm.client import LLMClient as RealClient

            blocker = BlockingFakeProvider()

            def chunk_fn(chunk):
                client = RealClient(blocker, options={})
                client.request_label = "translate chunk"
                return client.translate_batch(chunk, [])

            with _fake_interval(0.05):
                run_chunks_parallel(
                    [(c, None) for c in chunks],
                    lambda item: chunk_fn(item[0]),
                    concurrency=3,
                    cancel=None,
                )
    finally:
        reset(token)
    # While the (virtual) 3 s request was outstanding the panel must have
    # received at least one heartbeat line from each of the three workers.
    assert len(events) >= 3, (
        f"the details panel received {len(events)} event(s) while three "
        "worker threads had model requests outstanding — the silent "
        "nine minutes of the user's run"
    )


from contextlib import contextmanager


@contextmanager
def _fake_interval(seconds: float):
    import unittest.mock as mock

    with mock.patch("dubtool.heartbeat.interval", return_value=seconds):
        yield
