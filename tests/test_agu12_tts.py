"""Update 12 task 5: the voice-request limiter and the honest experiment.

- the global limiter halves to one request per 5 s when more than 30% of
  the last 10 requests were refused, and recovers (slowly) after 10
  consecutive successes — with the rate change.visible in the panel;
- a handshake 503 counts as a refusal like any other failure;
- `--tts-probe` (the same code from source and from the exe) interleaves
  the three rate conditions, fires a control every 10 requests, and
  prints a results table.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dubtool.tts.base import TTSFailure
from dubtool.tts_probe import _classify, run_experiment


# ---------------------------------------------------------------- limiter


def test_limiter_halves_on_refusals_with_a_floor():
    from dubtool.stages.s4_tts import VoiceRequestLimiter

    lim = VoiceRequestLimiter(max_rps=1.0)
    assert lim.current_interval_s == pytest.approx(1.0)
    emoted: list[str] = []
    for _ in range(3):
        lim.record(False)
    # Three refusals in a row: the share of the last 10 exceeds 30% every
    # time -> 1 s -> 2 s -> 4 s -> the 5 s floor.
    assert lim.current_interval_s == pytest.approx(5.0)
    assert lim.refusals == 3


def test_limiter_recovers_slowly_after_successes():
    from dubtool.stages.s4_tts import VoiceRequestLimiter

    lim = VoiceRequestLimiter(max_rps=1.0)
    lim.current_interval_s = 5.0
    for _ in range(10):
        lim.record(True)
    # 10 consecutive successes recover HALF of the excess (never below the
    # configured interval) — a slow recovery, not a full reset.
    assert lim.current_interval_s == pytest.approx(3.0)
    assert lim.success_streak == 0


def test_limiter_gate_paces_requests(tmp_path, monkeypatch):
    from dubtool import audio_utils
    from dubtool.stages import s4_tts
    from dubtool.stages.s4_tts import SegmentTTS, VoiceRequestLimiter

    monkeypatch.setattr(audio_utils, "require_ffmpeg", lambda: None)

    sleeps: list[float] = []

    lim = VoiceRequestLimiter(max_rps=2.0)  # 0.5 s between requests

    class _Ok:
        def synthesize(self, text, voice, out_path):
            import wave

            out_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(24000)
                f.writeframes(b"\x00\x00" * 2400)

    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    tts = SegmentTTS(_Ok(), tmp_path / "cache", "vi-VN-Test")
    tts.limiter = lim
    monkeypatch.setattr("dubtool.heartbeat.sleeping",
                        lambda s, text=None, cancel=None: sleeps.append(s))
    tts.synthesize_text("câu một", "vi-VN-Test")
    tts.synthesize_text("câu hai", "vi-VN-Test")
    assert lim.requests == 2 and lim.refusals == 0
    # The second request was paced (a non-negative wait within the interval;
    # execution time between the requests may consume part of it).
    assert sleeps and 0 < sleeps[0] <= 0.5


def test_handshake_503_counts_as_refusal(tmp_path):
    from dubtool.stages.s4_tts import SegmentTTS, VoiceRequestLimiter

    class _Handshake503:
        def synthesize(self, text, voice, out_path):
            raise TTSFailure(
                "WSServerHandshakeError: HTTP 503 Invalid response status "
                "from speech.platform.bing.com"
            )

    lim = VoiceRequestLimiter(max_rps=1.0)
    tts = SegmentTTS(_Handshake503(), tmp_path / "cache", "vi-VN-Test")
    tts.limiter = lim
    with pytest.raises(TTSFailure):
        tts.synthesize_text("câu thử", "vi-VN-Test")
    assert lim.requests == 1 and lim.refusals == 1


def test_handshake_503_is_classified():
    class _E(Exception):
        pass

    assert _classify(TTSFailure(
        "WSServerHandshakeError: HTTP 503 Invalid response status")) == "handshake_503"
    assert _classify(TTSFailure("NoAudioReceived for unknown reason")) == "no_audio"
    assert _classify(TTSFailure("connection timeout after 45 s")) == "timeout"
    assert _classify(TTSFailure("something else entirely")) == "other"


# ----------------------------------------------------------------- probe


def test_probe_interleaves_with_control_and_table(tmp_path, monkeypatch, capsys):
    import dubtool.tts_probe as probe

    monkeypatch.setattr(probe, "_sleep", lambda s: None)  # no real waiting
    calls = {"n": 0}

    class _Fake:
        def synthesize(self, text, voice, out_path):
            calls["n"] += 1
            if calls["n"] % 4 == 0:
                raise TTSFailure(
                    "WSServerHandshakeError: HTTP 503 Invalid response status"
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"mp3")

    cfg = SimpleNamespace(tts_voice="vi-VN-Test", tts={"request_timeout_s": 5.0})
    data = run_experiment(
        cfg, requests=12, provider=_Fake(),
        texts=[f"câu {i}" for i in range(12)],
    )
    captured = capsys.readouterr().out
    # Every condition saw all 12 texts (interleaved, same window).
    assert all(c["requests"] == 12 for c in data["conditions"].values())
    # A control request fired at the 10th request.
    assert data["controls"]["requests"] == 1
    # The failures were classified, not merged (the fake refuses every
    # 4th of the 36 condition requests).
    sums = {
        k: sum(c[k] for c in data["conditions"].values())
        for k in ("ok", "handshake_503", "other")
    }
    assert sums["handshake_503"] == 9
    assert sums["ok"] + sums["handshake_503"] + sums["other"] == 36
    # The printed table names every condition and the control row.
    assert "handshake_503" in captured
    assert "control" in captured
    assert "1/3s" in captured and "1/10s" in captured


def test_probe_writes_results_file(tmp_path, monkeypatch):
    import dubtool.tts_probe as probe

    monkeypatch.setattr(probe, "_sleep", lambda s: None)

    class _Ok:
        def synthesize(self, text, voice, out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"mp3")

    cfg = SimpleNamespace(tts_voice="vi-VN-Test", tts={"request_timeout_s": 5.0})
    out = tmp_path / "probe.json"
    run_experiment(cfg, out_path=str(out), requests=3,
                   provider=_Ok(), texts=["câu một", "câu hai", "câu ba"])
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert '"controls"' in body and '"conditions"' in body


# --------------------------------------------------- edge-tts speed optimizations


def test_edge_provider_reuses_loop_connector_and_honors_cancel(tmp_path, monkeypatch):
    import asyncio
    import concurrent.futures
    import threading
    import time

    import edge_tts

    from dubtool.tts.edge import EdgeTTSProvider

    seen_loops: list[int] = []
    seen_connectors: list[object] = []
    seen_lock = threading.Lock()
    entered = threading.Event()

    class _FakeCommunicate:
        def __init__(self, text, voice, rate="+0%", connector=None):
            self.text = text
            self.voice = voice
            with seen_lock:
                seen_connectors.append(connector)

        async def save(self, out_path):
            with seen_lock:
                seen_loops.append(id(asyncio.get_running_loop()))
            if self.text == "hang-cancel":
                entered.set()
                await asyncio.sleep(30.0)
            from pathlib import Path

            Path(out_path).write_bytes(b"\xff" * 300)

    monkeypatch.setattr(edge_tts, "Communicate", _FakeCommunicate)
    cancel = threading.Event()
    provider = EdgeTTSProvider(request_timeout_s=5.0, rate_limit_s=0.0, cancel=cancel)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futs = [
                pool.submit(provider.synthesize, f"cau {i}", "vi-VN-Test", tmp_path / f"{i}.mp3")
                for i in range(4)
            ]
            for fut in futs:
                fut.result()

        # All 4 calls across worker threads shared the single persistent loop & connector.
        assert len(set(seen_loops)) == 1
        assert all(c is not None and type(c).__name__ == "_ReusableTCPConnector" for c in seen_connectors)
        assert len(set(id(c) for c in seen_connectors)) == 1

        # A hanging request exits promptly once cancel is set.
        threading.Timer(0.1, lambda: (entered.wait(timeout=2.0), cancel.set())).start()
        t0 = time.monotonic()
        with pytest.raises(TTSFailure) as excinfo:
            provider.synthesize("hang-cancel", "vi-VN-Test", tmp_path / "hang.mp3")
        assert time.monotonic() - t0 < 2.5
        assert "cancel" in str(excinfo.value).lower()
    finally:
        provider.close()
    assert provider._loop is None and provider._connector is None


def test_edge_provider_handles_communicate_without_connector_param(tmp_path, monkeypatch):
    from pathlib import Path

    import edge_tts

    from dubtool.tts.edge import EdgeTTSProvider

    class _LegacyFakeCommunicate:
        def __init__(self, text, voice, rate="+0%"):
            pass

        async def save(self, out_path):
            Path(out_path).write_bytes(b"\xff" * 256)

    monkeypatch.setattr(edge_tts, "Communicate", _LegacyFakeCommunicate)
    provider = EdgeTTSProvider(request_timeout_s=5.0, rate_limit_s=0.0)
    try:
        out = tmp_path / "legacy.mp3"
        provider.synthesize("xin chao", "vi-VN-Test", out)
        assert out.exists() and out.stat().st_size == 256
    finally:
        provider.close()


def test_limiter_reserves_non_overlapping_slots_under_concurrency(monkeypatch):
    import concurrent.futures
    import threading

    from dubtool.stages.s4_tts import VoiceRequestLimiter

    sleeps: list[float] = []
    sleep_lock = threading.Lock()
    monkeypatch.setattr(
        "dubtool.heartbeat.sleeping",
        lambda s, text=None, cancel=None: (sleep_lock.acquire(), sleeps.append(s), sleep_lock.release()),
    )

    lim = VoiceRequestLimiter(max_rps=5.0)  # 0.2 s nominal slot spacing
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(lim.gate) for _ in range(4)]
        for fut in futs:
            fut.result()
    assert len(sleeps) == 3
    assert max(sleeps) >= 0.4


def test_s4_concurrent_execution_dedups_duplicates_and_closes_provider(tmp_path, monkeypatch):
    import threading
    import time

    from dubtool import audio_utils
    from dubtool.job import Job, write_json
    from dubtool.stages import s4_tts
    from dubtool.tts.base import TTSUnavailableError

    monkeypatch.setattr(audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(audio_utils, "probe_duration", lambda path: 0.5)

    active = 0
    max_active = 0
    calls: list[str] = []
    closed = False
    state_lock = threading.Lock()

    class _ConcurrentFakeProvider:
        supports_concurrent = True

        def __init__(self):
            self.cancel = None
            self.max_cooldown_s = 30.0
            self.rate_limit_s = 0.0

        def synthesize(self, text, voice, out_path):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                calls.append(text)
            try:
                time.sleep(0.15)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"\xff" * 1200)
            finally:
                with state_lock:
                    active -= 1

        def close(self):
            nonlocal closed
            closed = True

    provider = _ConcurrentFakeProvider()

    class _FakeTTS(s4_tts.SegmentTTS):
        pass

    def _fake_build_tts(cfg, cache_dir, media_cache=None):
        return _FakeTTS(provider, cache_dir, cfg.tts_voice, media_cache=media_cache)

    monkeypatch.setattr(s4_tts, "build_tts", _fake_build_tts)

    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    # Two entries share the exact same Vietnamese text ("cau trung") to verify
    # per-(voice, text) dedup avoids duplicate network calls.
    write_json(
        job_dir / "translated.json",
        [
            {"id": 0, "start": 0.0, "end": 1.0, "vi": "cau khac 0"},
            {"id": 1, "start": 1.0, "end": 2.0, "vi": "cau khac 1"},
            {"id": 2, "start": 2.0, "end": 3.0, "vi": "cau khac 2"},
            {"id": 3, "start": 3.0, "end": 4.0, "vi": "cau trung"},
            {"id": 4, "start": 4.0, "end": 5.0, "vi": "cau khac 4"},
            {"id": 5, "start": 5.0, "end": 6.0, "vi": "cau trung"},
        ],
    )
    job = Job(job_dir)
    cfg = SimpleNamespace(
        tts_voice="vi-VN-HoaiMyNeural",
        tts={
            "concurrency": 3,
            "max_rps": 0.0,
            "pacing_delay_s": 0.0,
            "retry_rounds": 0,
            "breaker_consecutive": 3,
            "breaker_pause_s": 0.0,
            "unit_budget_s": 10.0,
            "stage_budget_s": 60.0,
            "budget_policy": "stop",
        },
        dub_unit_mode="segment",
    )

    t0 = time.monotonic()
    s4_tts.run(job, cfg)
    elapsed = time.monotonic() - t0

    assert closed is True
    assert max_active >= 2
    assert elapsed < 0.75
    assert calls.count("cau trung") == 1
    assert len(calls) == 5
    for idx in range(6):
        assert (job.tts_dir / f"{idx}.mp3").exists()


def test_s4_concurrent_breaker_drains_inflight_before_pause(tmp_path, monkeypatch):
    import threading

    from dubtool import audio_utils
    from dubtool.job import Job, write_json
    from dubtool.stages import s4_tts
    from dubtool.tts.base import TTSUnavailableError

    monkeypatch.setattr(audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(audio_utils, "probe_duration", lambda path: 0.5)

    inflight = 0
    max_inflight_during_sleep: list[int] = []
    state_lock = threading.Lock()

    class _AlwaysFailConcurrent:
        supports_concurrent = True

        def __init__(self):
            self.cancel = None

        def synthesize(self, text, voice, out_path):
            nonlocal inflight
            with state_lock:
                inflight += 1
            try:
                import time
                time.sleep(0.05)
                raise TTSFailure("service unavailable")
            finally:
                with state_lock:
                    inflight -= 1

        def close(self):
            pass

    provider = _AlwaysFailConcurrent()
    monkeypatch.setattr(
        s4_tts,
        "build_tts",
        lambda cfg, cache_dir, media_cache=None: s4_tts.SegmentTTS(
            provider, cache_dir, cfg.tts_voice, media_cache=media_cache
        ),
    )

    breaker_pauses: list[float] = []

    def _record_sleep(seconds, text=None, cancel=None):
        with state_lock:
            max_inflight_during_sleep.append(inflight)
        breaker_pauses.append(seconds)

    monkeypatch.setattr("dubtool.heartbeat.sleeping", _record_sleep)

    job_dir = tmp_path / "job_breaker"
    job_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        job_dir / "translated.json",
        [{"id": i, "start": float(i), "end": i + 1.0, "vi": f"fail {i}"} for i in range(4)],
    )
    job = Job(job_dir)
    cfg = SimpleNamespace(
        tts_voice="vi-VN-HoaiMyNeural",
        tts={
            "concurrency": 3,
            "max_rps": 0.0,
            "pacing_delay_s": 0.0,
            "retry_rounds": 0,
            "breaker_consecutive": 2,
            "breaker_pause_s": 1.25,
            "per_attempt_backoff_s": [0.0],
            "unit_budget_s": 10.0,
            "stage_budget_s": 60.0,
            "budget_policy": "stop",
        },
        dub_unit_mode="segment",
    )

    with pytest.raises(TTSUnavailableError):
        s4_tts.run(job, cfg)

    assert breaker_pauses and 1.25 in breaker_pauses
    # Every in-flight worker had drained cleanly before the breaker sleep ran.
    assert all(n == 0 for n in max_inflight_during_sleep)


def test_ladder_direct_retries_recovers_transient_no_audio(tmp_path, monkeypatch):
    from dubtool import audio_utils
    from dubtool.stages.s4_tts import SegmentTTS, _Ladder
    from dubtool.tts_failures import TTSFailureLog

    monkeypatch.setattr(audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(audio_utils, "probe_duration", lambda path: 0.4)
    sleeps: list[float] = []
    monkeypatch.setattr("dubtool.heartbeat.sleeping", lambda s, text=None, cancel=None: sleeps.append(s) or 0.0)

    attempts = {"n": 0}

    class _FlakyOnce:
        def synthesize(self, text, voice, out_path):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise TTSFailure("NoAudioReceived")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\xff" * 1200)

    cfg = SimpleNamespace(
        tts_voice="vi-VN-NamMinhNeural",
        tts={
            "direct_retries": 1,
            "attempt_delays_s": [1.5, 4.0],
            "max_chars_per_request": 200,
            "unit_budget_s": 30.0,
        },
    )
    tts = SegmentTTS(_FlakyOnce(), tmp_path / "cache", cfg.tts_voice)
    ladder = _Ladder(tts, TTSFailureLog(tmp_path), cfg)
    state, dur, path = ladder.attempt({"id": 7, "vi": "Câu ngắn bị rớt một nhịp."})
    assert state == "done" and dur == pytest.approx(0.4)
    assert path is not None and path.exists()
    assert attempts["n"] == 2
    assert sleeps == [1.5]


def test_graduated_breaker_schedule_and_limiter_floor(tmp_path, monkeypatch):
    from dubtool import audio_utils
    from dubtool.job import Job, write_json
    from dubtool.stages import s4_tts
    from dubtool.stages.s4_tts import VoiceRequestLimiter
    from dubtool.tts.base import TTSUnavailableError

    lim = VoiceRequestLimiter(max_rps=1.5, floor_interval_s=2.5)
    for _ in range(5):
        lim.record(False)
    assert lim.current_interval_s == pytest.approx(2.5)

    monkeypatch.setattr(audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(audio_utils, "probe_duration", lambda path: 0.4)

    pauses: list[float] = []
    monkeypatch.setattr("dubtool.heartbeat.sleeping", lambda s, text=None, cancel=None: pauses.append(s) or 0.0)

    class _AlwaysFail:
        def synthesize(self, text, voice, out_path):
            raise TTSFailure("NoAudioReceived")

    provider = _AlwaysFail()
    monkeypatch.setattr(
        s4_tts,
        "build_tts",
        lambda cfg, cache_dir, media_cache=None: s4_tts.SegmentTTS(provider, cache_dir, cfg.tts_voice),
    )

    job_dir = tmp_path / "job_schedule"
    job_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        job_dir / "translated.json",
        [{"id": i, "start": float(i), "end": i + 1.0, "vi": f"cau {i}"} for i in range(4)],
    )
    job = Job(job_dir)
    cfg = SimpleNamespace(
        tts_voice="vi-VN-NamMinhNeural",
        tts={
            "concurrency": 1,
            "max_rps": 0.0,
            "pacing_delay_s": 0.0,
            "direct_retries": 0,
            "retry_rounds": 0,
            "breaker_consecutive": 2,
            "breaker_pause_schedule_s": [10.0, 20.0, 30.0],
            "unit_budget_s": 10.0,
            "stage_budget_s": 60.0,
            "budget_policy": "stop",
        },
        dub_unit_mode="segment",
    )

    with pytest.raises(TTSUnavailableError):
        s4_tts.run(job, cfg)
    assert pauses == [10.0, 20.0]


