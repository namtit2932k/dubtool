"""AGENT_UPDATE_8.md 2.6 — TTS resilience tests.

Fake-provider (and one real-ffmpeg duration probe) scenarios:
- one failing segment does not abort the stage (deferred rounds recover);
- the circuit breaker stops calling the service after N consecutive failures;
- Cancel works during a cooldown (fake clock);
- if everything fails, the finished clips are preserved and a resumed run
  reuses them;
- the backup voice is used and counted;
- the log fields are present and contain no key;
- `tts_unavailable` (TTSUnavailableError) is raised for the exhausted case.

No real sleeping: all waits are config-driven (pacing and cooldowns set to 0
in the test config).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from dubtool.job import write_json


pytestmark = pytest.mark.usefixtures("require_ffprobe_or_skip")


@pytest.fixture(name="require_ffprobe_or_skip")
def _ffprobe_or_skip():
    from dubtool import audio_utils

    src = None
    try:
        audio_utils.ffmpeg_bin()
    except audio_utils.FFmpegMissing:
        pytest.skip("ffmpeg/ffprobe not available")


def _write_wav(path: Path, seconds: float = 0.3) -> None:
    """A short valid WAV so ffprobe sees a real duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(22050)
        f.writeframes(b"\x00\x00" * int(22050 * seconds))


class FakeJob:
    """The parts of Job that stages/s4_tts.run touches."""

    def __init__(self, tmp_path: Path, n_segments: int = 4):
        self.dir = tmp_path
        self.tts_dir = tmp_path / "tts"
        self.tts_dir.mkdir(parents=True, exist_ok=True)
        self.translated_path = tmp_path / "translated.json"
        write_json(
            self.translated_path,
            [{"id": i, "vi": f"câu {i}"} for i in range(n_segments)],
        )


class FakeConfig:
    def __init__(self, tmp_path: Path, **tts_extra):
        self.tts_voice = "vi-TEST-Voice"
        self.tts_rate = "+0%"
        self.tts_trim_silence = False
        self.tts = {
            "pacing_delay_s": 0.0,          # no real sleeping in tests
            "max_delay_s": 0.0,
            "max_cooldown_s": 0.0,
            "retry_rounds": 3,
            "round_cooldown_s": [0.0, 0.0, 0.0],
            "breaker_consecutive": 3,
            "fallback": "none",
            "piper_voice": "",
            **tts_extra,
        }
        self._tmp = tmp_path


class FlakyProvider:
    """Fails the first `fail_times` synthesize calls (per run), succeeds after."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TTSFailureCtor("injected failure")
        _write_wav(out_path)


class TTSFailureCtor(Exception):
    """A TTSFailure stand-in (the real class lives in tts.base)."""


class AlwaysFailProvider:
    def __init__(self):
        self.calls = 0

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        self.calls += 1
        raise TTSFailureCtor("service down")


class FakeClockMonotonic:
    """Replace time.monotonic inside s4_tts with a controllable clock."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def patch(self, monkeypatch):
        import time as time_mod

        monkeypatch.setattr(time_mod, "monotonic", self)


def _import_stage():
    from dubtool.stages import s4_tts

    return s4_tts


@pytest.fixture(autouse=True)
def _failure_class_alias():
    from dubtool.tts.base import TTSFailure, TTSUnavailableError

    globals()["TTSFailureCtor"] = TTSFailure
    globals()["TTSUnavailableError"] = TTSUnavailableError


# ---------------------------------------------------------------- tests


def test_one_failing_segment_does_not_abort(tmp_path, monkeypatch):
    """AGU8 2.6: round 1 recovers a segment that failed on the first pass."""
    job = FakeJob(tmp_path, n_segments=4)
    cfg = FakeConfig(tmp_path)
    s4 = _import_stage()
    provider = FlakyProvider(fail_times=1)

    monkeypatch.setattr(s4.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(
        s4, "build_tts", lambda config, cache_dir, media_cache=None: _fake_tts(cfg, provider, tmp_path)
    )
    s4.run(job, cfg)

    entries = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert all(e["tts_duration"] is not None for e in entries)
    assert provider.calls == 5  # 3 successes + 1 failure + 1 retry


def _fake_tts(cfg, provider, tmp_path):
    from dubtool.job import read_json

    s4 = _import_stage()
    tts = s4.SegmentTTS(
        provider, tmp_path / "tts" / "cache", cfg.tts_voice, options={"rate": "+0%"},
        media_cache=None,
    )
    return tts


def test_exhausted_failure_preserves_clips_and_raises_unavailable(tmp_path, monkeypatch):
    """AGU8 2.1/2.4: all rounds exhausted -> TTSUnavailableError, finished
    clips kept; a resumed run reuses them (never resynthesized)."""
    job = FakeJob(tmp_path, n_segments=3)
    cfg = FakeConfig(tmp_path)
    s4 = _import_stage()
    provider = AlwaysFailProvider()
    monkeypatch.setattr(s4.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(s4, "build_tts", lambda config, cache_dir, media_cache=None: _fake_tts(cfg, provider, tmp_path))

    with pytest.raises(globals()["TTSUnavailableError"]):
        s4.run(job, cfg)

    # Pre-finish one segment manually (as a previous successful pass would):
    # segment 1 has its clip and tts_duration -> a resumed run must not call
    # the provider for it.
    _write_wav(job.tts_dir / "1.mp3")
    entries = json.loads(job.translated_path.read_text(encoding="utf-8"))
    entries[1]["tts_duration"] = 0.3
    write_json(job.translated_path, entries)

    provider2 = FlakyProvider(fail_times=0)
    monkeypatch.setattr(
        s4, "build_tts", lambda config, cache_dir, media_cache=None: _fake_tts(cfg, provider2, tmp_path)
    )
    s4.run(job, cfg)
    # Only the two still-missing segments were attempted.
    assert provider2.calls == 2
    resumed = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert all(e["tts_duration"] is not None for e in resumed)


def test_circuit_breaker_pauses_after_consecutive_failures(tmp_path, monkeypatch):
    """AGU8 2.1: after `breaker_consecutive` failures in a row the pass stops
    calling the service (bounded calls) and goes to the round cooldown."""
    job = FakeJob(tmp_path, n_segments=6)
    cfg = FakeConfig(tmp_path, breaker_consecutive=3, retry_rounds=0)
    s4 = _import_stage()
    provider = AlwaysFailProvider()
    monkeypatch.setattr(s4.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(s4, "build_tts", lambda config, cache_dir, media_cache=None: _fake_tts(cfg, provider, tmp_path))

    with pytest.raises(globals()["TTSUnavailableError"]):
        s4.run(job, cfg)
    # Breaker: 3 attempts in the first pass, then the pass stops (6 segments
    # would otherwise each be attempted in 3 rounds = up to 18 calls).
    assert provider.calls == 3


def test_cancel_during_cooldown(tmp_path, monkeypatch):
    """AGU8 2.1: Cancel during the round cooldown aborts at once (fake clock
    steps sleep(1) -- nothing sleeps because cooldowns are 0 and cancel is set
    before the wait)."""
    import threading

    from dubtool.job import StageCancelled

    job = FakeJob(tmp_path, n_segments=2)
    cfg = FakeConfig(tmp_path, round_cooldown_s=[5.0, 5.0, 5.0])
    s4 = _import_stage()
    provider = AlwaysFailProvider()
    monkeypatch.setattr(s4.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(s4, "build_tts", lambda config, cache_dir, media_cache=None: _fake_tts(cfg, provider, tmp_path))
    cancel = threading.Event()
    cancel.set()  # the cooldown wait sees this immediately

    with pytest.raises(StageCancelled):
        s4.run(job, cfg, cancel=cancel)


def test_fallback_voice_used_and_counted(tmp_path, monkeypatch):
    """AGU8 2.3/2.6: the backup voice covers the still-missing segments and
    the use is counted."""
    job = FakeJob(tmp_path, n_segments=4)
    cfg = FakeConfig(tmp_path, fallback="piper")
    s4 = _import_stage()

    class CountingBackup:
        name = "piper"
        uses = 0

        def synthesize(self, text, voice, out_path):
            CountingBackup.uses += 1
            _write_wav(out_path)

    primary = AlwaysFailProvider()
    fallback = CountingBackup()

    monkeypatch.setattr(s4.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(
        s4, "build_tts", lambda config, cache_dir, media_cache=None: _fake_tts(cfg, primary, tmp_path)
    )
    import dubtool.tts.fallback as fallback_mod

    monkeypatch.setattr(fallback_mod, "build_backup", lambda config: fallback)
    s4.run(job, cfg)

    entries = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert all(e["tts_duration"] is not None for e in entries)
    assert CountingBackup.uses == 4
    # 3 rounds x 3 attempts through the breaker (segments 0-2) + 4 primary
    # tries inside the backup pass (the wrapper always retries the primary
    # once before falling back).
    assert primary.calls == 12 + 4


def test_failure_log_fields_present_no_key(tmp_path, monkeypatch):
    """AGU8 2.2: every failed attempt records the diagnostic fields
    (exception type, voice, rate, text) and never any key material."""
    job = FakeJob(tmp_path, n_segments=2)
    cfg = FakeConfig(tmp_path, retry_rounds=0)
    s4 = _import_stage()
    provider = AlwaysFailProvider()
    monkeypatch.setattr(s4.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(s4, "build_tts", lambda config, cache_dir, media_cache=None: _fake_tts(cfg, provider, tmp_path))
    monkeypatch.setattr(
        s4, "_fail_rate", lambda config: "+0%"
    )

    with pytest.raises(globals()["TTSUnavailableError"]):
        s4.run(job, cfg)

    log_path = tmp_path / "tts_failures.json"
    assert log_path.exists()
    records = json.loads(log_path.read_text(encoding="utf-8"))
    assert records
    text_blob = json.dumps(records)
    assert "TTSFailure" in text_blob
    assert cfg.tts_voice in text_blob
    for field in ("error", "text", "chars", "attempt", "elapsed_s"):
        assert field in records[0]
    for name in ("GEMINI_API_KEY", "AIza", "api_key"):
        assert name not in text_blob
    # voice/rate fields added by AGU8 2.2
    assert records[0].get("voice") and records[0].get("rate")
