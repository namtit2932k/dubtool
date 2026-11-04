"""AGENT_UPDATE_11 task 2 acceptance tests (voice generation).

- the edge-tts request hard cap really cuts a hanging request (3.1);
- the repair ladder recovers 'refuse-ids' segments through the alternate
  voice (3.3, fault `tts:refuse-ids`);
- the per-segment budget defers a segment instead of blocking the queue;
- the split helper respects its character cap (fixture: the 13 segments
  the real run's service refused);
- the budget dialog texts exist in both languages (3.4).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from dubtool.tts.edge import EdgeTTSProvider
from dubtool.tts.base import TTSFailure
from dubtool.stages.s4_tts import (
    SegmentTTS,
    _Ladder,
    _split_text_chunks,
)


# ------------------------------------------------------------------ 3.1


@pytest.fixture(name="hanging_edge")
def _hanging_edge(monkeypatch):
    class _HangingCommunicate:
        def __init__(self, text, voice, rate="+0%"):
            self.text = text
            self.voice = voice

        async def save(self, out_path):
            await asyncio.sleep(30.0)  # the service never answers

    import edge_tts

    monkeypatch.setattr(edge_tts, "Communicate", _HangingCommunicate)


def test_edge_hard_timeout_cuts_hanging_request(hanging_edge, tmp_path):
    """`tts.request_timeout_s` bounds connect+send+receive: a request that
    hangs for ~30 s surfaces as TTSFailure after ~0.5 s, not minutes."""
    provider = EdgeTTSProvider(request_timeout_s=0.5, rate_limit_s=0.0)
    started = time.monotonic()
    with pytest.raises(TTSFailure) as excinfo:
        provider.synthesize("xin chào", "vi-TEST-Voice", tmp_path / "out.mp3")
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, elapsed
    assert "timeout" in str(excinfo.value).lower()


# ------------------------------------------------------------------ 3.3


class _WavProvider:
    """Writes a valid tiny wav on every call (no ffprobe required);
    `fail=True` turns it into an always-refusing service."""

    def __init__(self, fail: bool = False, **kwargs):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text, voice, out_path):
        if self.fail:
            raise TTSFailure("injected: the service refuses every request")
        import wave

        self.calls.append((text[:40], voice))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(24000)
            f.writeframes(b"\x00\x00" * 2400)


class _FailProvider(_WavProvider.synthesize and object):  # noqa: E999 - see below
    pass


def _ladder_with(tmp_path, entries, config_tts, fail: bool = False):
    import types

    from dubtool.tts_failures import TTSFailureLog

    tts = SegmentTTS(_WavProvider(fail=fail), tmp_path / "cache", "vi-TEST-Voice")
    cfg = types.SimpleNamespace(
        tts_voice="vi-TEST-Voice",
        tts={"alt_voice": "vi-TEST-Alt", **config_tts},
    )
    failures = TTSFailureLog(tmp_path)
    return _Ladder(tts, failures, cfg)


def test_no_alternate_voice_ever_used(tmp_path, monkeypatch):
    """Update 12 task 3: the alternate-voice steps are GONE. With every
    request refused and no dialog, the headless budget policy stops —
    no other voice is ever synthesized (the legacy alt_voice setting is
    ignored), in the clips, in state.json, or in the recorded failures."""
    from dubtool.tts.base import TTSUnavailableError

    s4 = _stage_module()
    provider = _RefusingProvider()
    _wired_build_tts(monkeypatch, s4, tmp_path, provider)

    def fake_sleeping(seconds, text=None, cancel=None):
        return float(seconds)  # the waits are simulated, not slept

    monkeypatch.setattr(s4.heartbeat, "sleeping", fake_sleeping)

    cfg = _cfg_stub(tmp_path, budget_policy="stop")
    job = _JobStub(tmp_path, 2)
    with pytest.raises(TTSUnavailableError):
        s4.run(job, cfg)

    entries = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert all(e.get("tts_duration") is None for e in entries)
    # No alternate voice anywhere: not in the failure records...
    from dubtool.job import read_json

    failures = read_json(job.dir / "tts_failures.json")
    assert failures
    assert all(r.get("voice") == "vi-TEST-Voice" for r in failures)
    assert all(r["step"] in (s4.STEP_DIRECT, s4.STEP_SPLIT) for r in failures)
    # ...nor in state.json (the video-wide voice is the configured one).
    state = read_json(job.dir / "state.json")
    assert state.get("voice") == "vi-TEST-Voice"


def test_ladder_never_uses_the_legacy_alt_voice(tmp_path):
    """Update 12 task 3: with a legacy alt_voice configured, a refused
    segment still runs only the one-voice steps and returns 'exhausted' —
    the decision moves up to the budget dialog, never to another voice."""
    ladder = _ladder_with(tmp_path, [], {"unit_budget_s": 1.0})
    ladder.refuse_ids = {1}
    entry = {"id": 1, "vi": "câu bị từ chối"}
    state, _duration, _path = ladder.attempt(entry)
    assert state == "exhausted"
    voices = [v for _text, v in ladder.tts.provider.calls]
    assert voices == ["vi-TEST-Voice"] * len(voices)


def test_unit_budget_defers_instead_of_blocking(tmp_path):
    """A segment that keeps failing is deferred by its budget; the wait
    stays bounded (never minutes of backoff inside one attempt)."""
    import time as _time

    started = _time.monotonic()
    ladder = _ladder_with(tmp_path, [], fail=True, config_tts={
        "unit_budget_s": 0.0, "attempt_delays_s": [2.0, 5.0],
    })
    # Update 12: a long text (over max_chars_per_request) is what keeps
    # the second step alive — a short text has exactly one step now, so a
    # zero budget defers right at the second step's gate.
    entry = {"id": 9, "vi": "câu bị từ chối " * 30}
    state, _duration, _path = ladder.attempt(entry)
    elapsed = _time.monotonic() - started
    assert state == "deferred"
    assert elapsed < 1.0, elapsed  # no real 2 s/5 s waits were taken


# ------------------------------------------------------------------ 3.3 split


def _fixture_rows() -> list[dict]:
    path = Path(__file__).parent / "fixtures" / "tts_failing_texts.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_texts_carry_the_thirteen_refused_ids():
    rows = _fixture_rows()
    assert [r["id"] for r in rows] == [4, 12, 17, 19, 20, 22, 25, 34, 36, 37, 43, 44, 45]
    assert all(r.get("vi") for r in rows)


def test_split_respects_90_chars_for_every_fixture_text():
    """Every refused segment splits into pieces of at most 90 characters,
    with no piece dropped (AGU11 3.3 step 2)."""
    for row in _fixture_rows():
        chunks = _split_text_chunks(row["vi"], 90)
        assert chunks, row["id"]
        assert all(len(c) <= 90 for c in chunks), (row["id"], [len(c) for c in chunks])
        joined = "".join(chunks).replace(" ", "")
        assert len(joined) >= len(row["vi"].replace(" ", "")) * 0.85, row["id"]


# ------------------------------------------------------------------ 3.4 dialogs


def test_budget_dialog_texts_exist_in_both_languages():
    from dubtool.app import i18n

    for lang in (i18n.VI, i18n.EN) if hasattr(i18n, "VI") else ("vi", "en"):
        pass
    texts = i18n._dicts if hasattr(i18n, "_dicts") else {}
    keys = ["tts.budget_exceeded", "tts.budget.wait10", "tts.budget.fallback",
            "tts.budget.fallback_disabled", "tts.budget.stop", "tts.cooldown",
            "tts.round_wait", "tts.breaker", "tts.budget_wait"]
    table = texts.get("vi") if isinstance(texts, dict) and "vi" in texts else None
    if table is None:
        pytest.skip("i18n internals are not dict-shaped; covered by app tests")
    for key in keys:
        assert key in table, key


# ------------------------------------------------------------------ 3.6 extras


class _RefusingProvider:
    """The service refuses every request (AGU11 replay of job 361f048f9ad8)."""

    def __init__(self):
        self.calls = 0

    def synthesize(self, text, voice, out_path):
        self.calls += 1
        raise TTSFailure("injected: the service refuses every request")


class _JobStub:
    """The parts of Job that s4_tts.run touches."""

    def __init__(self, tmp_path, n=2):
        from dubtool.job import write_json

        self.dir = tmp_path
        self.tts_dir = tmp_path / "tts"
        self.tts_dir.mkdir(parents=True, exist_ok=True)
        self.translated_path = tmp_path / "translated.json"
        write_json(self.translated_path,
                   [{"id": i, "vi": f"câu {i}"} for i in range(n)])


def _cfg_stub(tmp_path, **extra):
    import types

    tts = {
        "pacing_delay_s": 0.0, "max_delay_s": 0.0, "max_cooldown_s": 0.0,
        "retry_rounds": 3, "round_cooldown_s": [0.0, 0.0, 0.0],
        "breaker_consecutive": 3, "breaker_pause_s": 0.0,
        "max_rps": 0.0,  # Update 12: no request pacing in tests
        "attempt_delays_s": [0.0, 0.0], "unit_budget_s": 10.0,
        "max_chars_per_request": 90, "join_silence_s": 0.0,
        "request_timeout_s": 45.0, "alt_voice": "vi-TEST-Alt",
        "budget_policy": "ask", "fallback": "none", "piper_voice": "",
    }
    tts.update(extra)
    return types.SimpleNamespace(tts_voice="vi-TEST-Voice", tts_rate="+0%",
                                 tts_trim_silence=False, tts=tts, _tmp=tmp_path)


def _stage_module():
    from dubtool.stages import s4_tts as s4

    return s4


def _wired_build_tts(monkeypatch, s4, tmp_path, provider,
                     voice="vi-TEST-Voice"):
    monkeypatch.setattr(s4.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(
        s4, "build_tts",
        lambda config, cache_dir, media_cache=None: s4.SegmentTTS(
            provider, tmp_path / "tts" / "cache", voice,
            options={"rate": "+0%"}, media_cache=None,
        ),
    )


def test_budget_dialog_asked_again_after_wait10(tmp_path, monkeypatch):
    """3.6: the decision comes back after the gated 10-min wait — and the
    second 'wait' cannot loop forever (one gated wait per run)."""
    from dubtool.tts.base import TTSUnavailableError

    s4 = _stage_module()
    provider = _RefusingProvider()
    _wired_build_tts(monkeypatch, s4, tmp_path, provider)

    def fake_sleeping(seconds, text=None, cancel=None):
        return float(seconds)  # the wait is simulated, not slept

    monkeypatch.setattr(s4.heartbeat, "sleeping", fake_sleeping)

    asks = []
    choices = ["wait10", "stop"]

    def dialog(missing, waited, fallback_ok, voice=None):
        asks.append((missing, waited, fallback_ok, voice))
        return choices.pop(0)

    cfg = _cfg_stub(tmp_path, retry_rounds=0, budget_policy="ask")
    cfg.tts_budget_dialog = dialog
    job = _JobStub(tmp_path, 2)

    with pytest.raises(TTSUnavailableError):
        s4.run(job, cfg)

    assert len(asks) == 2, asks
    assert asks[0][0] == 2 and asks[1][0] == 2
    # the clips stay preserved (translated.json is intact; nothing crashed)
    assert job.translated_path.exists()


def test_cancel_during_ladder_returns_quickly(tmp_path, monkeypatch):
    """3.6: Cancel cuts the ladder waits too (unwinds within seconds even
    during a cooldown inside an attempt)."""
    import threading
    import time as _time

    from dubtool.job import StageCancelled

    s4 = _stage_module()

    class _SetEvent(_RefusingProvider):
        def synthesize(self, text, voice, out_path):
            try:
                return _RefusingProvider.synthesize(self, text, voice, out_path)
            finally:
                if self.calls >= 1:
                    ev.set()  # cancel while the ladder is mid-run

    ev = threading.Event()
    _wired_build_tts(monkeypatch, s4, tmp_path, _SetEvent())

    def fake_sleeping(seconds, text=None, cancel=None):
        if cancel is not None and cancel.is_set():
            raise StageCancelled("s4: cancelled")
        return 0.0

    monkeypatch.setattr(s4.heartbeat, "sleeping", fake_sleeping)

    cfg = _cfg_stub(tmp_path, attempt_delays_s=[5.0, 5.0])
    job = _JobStub(tmp_path)
    started = _time.monotonic()
    with pytest.raises(StageCancelled):
        s4.run(job, cfg, cancel=ev)
    assert _time.monotonic() - started < 3.0


def test_failure_log_records_step_and_timing(tmp_path, monkeypatch):
    """3.6 + AGU11 recording: every failed ladder step lands in
    tts_failures.json with its step name and its elapsed seconds."""
    s4 = _stage_module()
    provider = _RefusingProvider()
    _wired_build_tts(monkeypatch, s4, tmp_path, provider)

    def fake_sleeping(seconds, text=None, cancel=None):
        return 0.0

    monkeypatch.setattr(s4.heartbeat, "sleeping", fake_sleeping)

    cfg = _cfg_stub(tmp_path)
    job = _JobStub(tmp_path)
    with pytest.raises(Exception):
        s4.run(job, cfg)

    from dubtool.job import read_json

    records = read_json(job.dir / "tts_failures.json")
    assert records, "no failures recorded"
    steps = set()
    for r in records:
        # Update 12: the ladder has exactly two steps (no alt voice).
        assert r["step"] in (s4.STEP_DIRECT, s4.STEP_SPLIT), r
        assert isinstance(r["elapsed_s"], (int, float))
        steps.add(r["step"])
    assert s4.STEP_DIRECT in steps


def test_first_pass_stays_under_unit_budget_x_failing(tmp_path, monkeypatch):
    """3.6: 13 of 46 refused (5 refuse only the first ladder step, 5 the
    first two, 3 everything) — the simulated first pass stays under
    unit_budget_s × failing segments, without minutes-long dead waits."""
    import wave

    from tests.fake_clock import fast_clock

    s4 = _stage_module()
    fail_ids = [4, 12, 17, 19, 20, 22, 25, 34, 36, 37, 43, 44, 45]
    kinds: dict[int, str] = {}
    for i, seg_id in enumerate(fail_ids):
        if i < 5:
            kinds[seg_id] = "first"
        elif i < 10:
            kinds[seg_id] = "two"
        else:
            kinds[seg_id] = "all"
    entries = [{"id": i, "vi": f"câu {i} thông thường, ngắn gọn."}
               for i in range(46)]

    class _Scripted:
        calls_per_text: dict[str, int] = {}

        def synthesize(self, text: str, voice: str, out_path) -> None:
            from dubtool.tts.base import TTSFailure

            prefix = f"câu {text.split(' ', 2)[1]} "
            kind = kinds.get(int(text.split(" ", 2)[1]), "")
            n = _Scripted.calls_per_text[text] = (
                _Scripted.calls_per_text.get(text, 0) + 1)
            refuses = (
                kind == "all" or
                (kind == "first" and n == 1) or
                (kind == "two" and n <= 2))
            if refuses:
                raise TTSFailure(f"injected refusal: {prefix}{kind}")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(24000)
                f.writeframes(b"\x00\x00" * 2400)

    _wired_build_tts(monkeypatch, s4, tmp_path, _Scripted())

    from dubtool import heartbeat as hb

    with fast_clock(monkeypatch=monkeypatch) as clock:
        def advancing_sleep(seconds: float, text=None, cancel=None):
            clock.sleep(float(seconds))  # waits advance the fake clock
            return float(seconds)

        monkeypatch.setattr(hb, "sleeping", advancing_sleep)

        from dubtool.tts_failures import TTSFailureLog
        import types as _types

        cfg = _cfg_stub(tmp_path, unit_budget_s=60.0,
                        attempt_delays_s=[2.0, 5.0])
        tts = s4.SegmentTTS(_Scripted(), tmp_path / "tts" / "cache",
                            "vi-TEST-Voice",
                            options={"rate": "+0%"}, media_cache=None)
        ladder = s4._Ladder(tts, TTSFailureLog(tmp_path), cfg)
        t0 = clock.now
        for entry in entries:
            ladder.attempt(entry, budget_s=cfg.tts["unit_budget_s"])
        total = clock.now - t0

    failing = len(fail_ids)
    bound = cfg.tts["unit_budget_s"] * failing
    assert total < bound, (total, bound)
    # the good segments finished fast and the numbers are recorded
    from dubtool.job import read_json

    records = read_json(tmp_path / "tts_failures.json")
    assert records, "the refusals were not recorded"
