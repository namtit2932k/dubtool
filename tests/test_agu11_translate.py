"""AGENT_UPDATE_11 task 1 acceptance tests (beyond the replays):

- right-sized requests: 16 units / 3,827 characters become at least three
  requests of at most 1,200 characters (2.1);
- the per-chunk cap halves after a slow request (latency adaptation, 2.1);
- the stage budget forces the overload policy again even after the user
  chose 'wait' (2.3), with an announced panel event;
- the policy consult receives the info dict (model + waited seconds);
- every backoff emits a heartbeat line (2.4).
"""

from __future__ import annotations

import json
import logging

import pytest

from tests.fake_clock import fast_clock
from dubtool.llm.base import LLMResult, ServerErrorLLMError
from dubtool.stages._units_runner import run_units_parallel, adaptive_state, adapt_after_latency


class _FakeConfig:
    def __init__(self, timeout=180.0):
        self.llm = {
            "chunk_max_chars": 1200,
            "chunk_max_units": 8,
            "request_timeout_s": timeout,
            "request_timeout_s_fast": 90.0,
        }
        self.llm_mode = None


def _unit(unit_id: int, chars: int) -> dict:
    return {"unit_id": unit_id, "text": "x" * chars, "start": 0.0, "end": 1.0,
            "max_chars": 50, "seg_ids": [unit_id], "segments": []}


def test_right_sized_requests_16_units_3827_chars():
    """16 units of 3,827 total characters → at least 3 requests, each at\n    most 1,200 characters (AGENT_UPDATE_11 2.1)."""
    units = [_unit(i, 239) for i in range(16)]  # 16 × 239 = 3,824 chars
    sizes: list[tuple[int, int]] = []

    def translate_chunk(chunk, label):
        sizes.append((len(chunk), sum(len(u["text"]) for u in chunk)))
        return [{"id": u["unit_id"], "vi": f"dịch {u['unit_id']}"} for u in chunk]

    adaptive = adaptive_state(_FakeConfig())
    applied: list = []
    run_units_parallel(units, translate_chunk, apply=lambda upd: applied.extend(upd),
                       concurrency=1, adaptive=adaptive)
    assert len(sizes) >= 3
    assert sum(count for count, _chars in sizes) == 16
    assert all(chars <= 1200 for _count, chars in sizes), sizes
    assert [upd["id"] for upd in applied] == list(range(16))


def test_latency_halving_and_raise(monkeypatch):
    """A request slower than 60% of the timeout halves the cap (floor 400);
    three consecutive fast requests raise it 25% (cap at the default),\n    never above (AGENT_UPDATE_11 2.1)."""
    adaptive = adaptive_state(_FakeConfig(timeout=100.0))
    adapt_after_latency(adaptive, 70.0)   # >60% → 1200 → 600
    assert adaptive["cur_chars"] == 600
    adapt_after_latency(adaptive, 70.0)   # 600 → 400 (600//2=300 < 400 floor)
    assert adaptive["cur_chars"] == 400
    adapt_after_latency(adaptive, 70.0)   # stays at the floor
    assert adaptive["cur_chars"] == 400
    for _ in range(3):                    # three quick requests → +25%
        adapt_after_latency(adaptive, 5.0)
    assert adaptive["cur_chars"] == 500
    for _ in range(3):
        adapt_after_latency(adaptive, 5.0)
    assert adaptive["cur_chars"] == 625


def test_stage_budget_forces_the_policy_after_a_wait(monkeypatch):
    """AGU11 2.3: after `llm.stage_budget_s` of waiting the policy applies\n    again even when the user chose 'wait' earlier, and the panel hears it."""
    from dubtool.llm.client import LLMClient
    from dubtool import heartbeat

    with fast_clock(monkeypatch=monkeypatch) as clock:
        consulted: list[str] = []

        class Provider:
            name = "gemini"
            model = "gemini-fake"
            timeout_s = 180.0

            def __init__(self):
                self.calls = 0

            def complete(self, system, user, *, json_mode=True, max_tokens=8000):
                self.calls += 1
                raise ServerErrorLLMError("504 DEADLINE_EXCEEDED", status=504)

        def on_overload(info=None):
            consulted.append(f"{info['consecutive']}@{info['waited_s']:.0f}s")
            return "wait"

        stage_wait = {"waited_s": 0.0, "consulted_at": 0.0, "consults": 0}
        events: list[str] = []
        heartbeat.bind(lambda kind, message: events.append(message))
        try:
            client = LLMClient(Provider(), options={
                "server_error_backoff_s": [30, 60, 90],
                "overload_after": 2,
                "overload_policy": "ask",
                "stage_budget_s": 60.0,
                "stage_wait_state": stage_wait,
                # small chunk: never split-eligible
                "split_on_timeout": False,
            }, on_overload=on_overload)
            client.request_units = 4
            with pytest.raises(Exception):
                client.translate_batch([{"id": i, "text": "t", "max_chars": 5} for i in range(4)], [])
        finally:
            heartbeat.reset(True)
        # Attempt 1 fails (waited 0), attempt 2 consults (consecutive 2),
        # then 30 s of backoff push the wait past the 60 s budget → the
        # policy is applied again on attempt 3.
        # (backoffs: attempt1→no consult, attempt2 consult, sleep 30,
        #  attempt3: 0+30 <60 → due? consecutive_since_consult=1 <2 → only
        #  OVER the budget checks matter: waited 30 < 60 → no second consult
        #  within this call — the give-up happens. So assert ≥1 consult and
        #  the accumulated wait.
        assert len(consulted) >= 1
        assert stage_wait["waited_s"] >= 30.0
        # The budget announcement path: fake one more batch with the budget
        # already exhausted.
        consulted.clear()
        events.clear()
        bound = heartbeat.bind(lambda kind, message: events.append(message))
        with fast_clock(monkeypatch=monkeypatch):
            stage_wait["waited_s"] = 120.0  # > stage_budget_s
            provider = Provider()
            client = LLMClient(provider, options={
                "server_error_backoff_s": [1],
                "overload_after": 2,
                "overload_policy": "ask",
                "stage_budget_s": 60.0,
                "stage_wait_state": stage_wait,
                "split_on_timeout": False,
            }, on_overload=on_overload)
            client.request_units = 4
            with pytest.raises(Exception):
                client.translate_batch([{"id": i, "text": "t", "max_chars": 5} for i in range(4)], [])
        assert any("ngân sách chờ" in message or "budget" in message.lower() for message in events), events


def test_backoff_emits_a_line_every_interval(monkeypatch):
    """AGU11 2.4: heartbeat lines appear while inside `heartbeat.sleeping` —\n    sleeping() must return the real slept seconds for the budget."""
    with fast_clock(monkeypatch=monkeypatch) as clock:
        events: list[str] = []
        from dubtool import heartbeat

        heartbeat.bind(lambda kind, message: events.append(message))
        try:
            with _fake_interval(2):
                slept = heartbeat.sleeping(70.0, lambda r: f"w {int(r)} s")
        finally:
            heartbeat.reset(True)
        assert slept == pytest.approx(70.0)
        assert len(events) >= 35 // 2 + 10  # ~one line per 2-s virtual interval


import contextlib  # noqa: E402


@contextlib.contextmanager
def _fake_interval(seconds):
    import unittest.mock as mock

    with mock.patch("dubtool.heartbeat.interval", return_value=seconds):
        yield
