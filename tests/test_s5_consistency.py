"""Tests for AGENT_UPDATE_3 tasks 4, 5, 6: text/audio consistency on TTS
failure, --force regeneration of fitted clips, and failure logging."""

from __future__ import annotations

import json
from pathlib import Path

from dubtool.stages import s5_fit_duration as s5
from dubtool.tts.base import TTSFailure
from dubtool.tts_failures import TTSFailureLog


class FakeJob:
    """A minimal job standing on plain JSON files (no real audio needed)."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        (self.dir / "tts").mkdir(exist_ok=True)
        (self.dir / "tts" / "0.mp3").touch()
        (self.dir / "fitted").mkdir(exist_ok=True)
        self.translated_path = self.dir / "translated.json"

    tts_dir = property(lambda self: self.dir / "tts")
    fitted_dir = property(lambda self: self.dir / "fitted")


class FakeConfig:
    chars_per_sec = 14
    max_stretch = 1.15
    max_fit_attempts = 3
    tts_voice = "v"
    tts_rate = "+0%"
    tts_trim_silence = False
    tts_silence_threshold_db = -35
    tts_silence_margin_s = 0.05


def run_with_probes(job, config, monkeypatch, fake_tts, fake_llm):
    """Drive s5.run with a fake TTS + LLM and no-op audio materialization."""
    monkeypatch.setattr(s5.audio_utils, "probe_duration", lambda p: 3.0)
    monkeypatch.setattr("dubtool.stages.s4_tts.build_tts", lambda config, cache_dir, **kw: fake_tts)
    monkeypatch.setattr(
        s5, "materialize_clip", lambda *a, **k: (job.fitted_dir / "0.wav").touch()
    )
    monkeypatch.setattr("dubtool.llm.LLMClient", fake_llm, raising=False)
    monkeypatch.setattr("dubtool.llm.get_provider", lambda *a, **k: object())
    s5.run(job, config, force=False)


class _AlwaysFailTTS:
    def synthesize(self, seg: dict, cancel=None):
        raise TTSFailure("edge-tts failed after 5 attempts: throttle")


class _OkTTS:
    def __init__(self):
        self.calls = 0

    def synthesize(self, seg: dict, cancel=None):
        self.calls += 1
        return 3.0, Path(f"cache-{seg['id']}")


class FakeLLMOK:
    total_input_tokens = 0
    total_output_tokens = 0

    def __init__(self, provider=None):
        pass

    def shorten(self, english, current_vi, max_chars):
        return "ngắn gọn"

    def shorten_batch(self, items):
        return [{"id": item["id"], "vi": "ngắn gọn"} for item in items]


class FakeLLMBoom:
    total_input_tokens = 0
    total_output_tokens = 0

    def __init__(self, provider=None):
        pass

    def shorten(self, english, current_vi, max_chars):
        raise RuntimeError("LLM exploded")

    def shorten_batch(self, items):
        raise RuntimeError("LLM exploded")


def _too_long_job(tmp_path) -> FakeJob:
    job = FakeJob(tmp_path)
    entries = [
        {
            "id": 0, "start": 0.0, "end": 1.0, "text": "hello",
            "vi": "câu này quá dài để vừa", "max_chars": 10,
            "reviewed": False, "tts_duration": 3.0, "fit_status": "pending",
        }
    ]
    job.translated_path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return job


def test_tts_failure_restores_text_and_duration(monkeypatch, tmp_path):
    """If TTS fails after a shorten, vi and tts_duration revert to the old clip."""
    job = _too_long_job(tmp_path)
    entries_before = json.loads(job.translated_path.read_text(encoding="utf-8"))
    original_vi = entries_before[0]["vi"]
    original_tts = entries_before[0]["tts_duration"]

    run_with_probes(job, FakeConfig(), monkeypatch, _AlwaysFailTTS(), FakeLLMOK)
    entries_after = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert entries_after[0]["vi"] == original_vi
    assert entries_after[0]["tts_duration"] == original_tts
    assert entries_after[0]["fit_status"] == "overflow"  # falls through to overflow
    failures = json.loads((job.dir / "tts_failures.json").read_text(encoding="utf-8"))
    assert len(failures) == 1
    assert failures[0]["id"] == 0
    assert failures[0]["attempt"] == 1
    assert "edge-tts failed" in failures[0]["error"]
    assert len(failures[0]["text"]) <= 120


def test_llm_failure_leaves_entry_unchanged(monkeypatch, tmp_path):
    job = _too_long_job(tmp_path)
    original_vi = json.loads(job.translated_path.read_text(encoding="utf-8"))[0]["vi"]

    run_with_probes(job, FakeConfig(), monkeypatch, _OkTTS(), FakeLLMBoom)
    entries_after = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert entries_after[0]["vi"] == original_vi
    assert entries_after[0]["tts_duration"] == 3.0  # no re-TTS happened


def test_stage5_runs_without_llm_key_when_nothing_needs_shortening(monkeypatch, tmp_path):
    job = FakeJob(tmp_path)
    entries = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "hi", "vi": "chào",
         "max_chars": 14, "reviewed": False, "tts_duration": 0.5, "fit_status": "pending"},
    ]
    job.translated_path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    def must_not_build(*a, **k):
        raise RuntimeError("LLM provider must not be constructed here")

    monkeypatch.setattr("dubtool.llm.get_provider", must_not_build, raising=False)
    run_with_probes(job, FakeConfig(), monkeypatch, _OkTTS(), FakeLLMOK)
    entries_after = json.loads(job.translated_path.read_text(encoding="utf-8"))
    assert entries_after[0]["vi"] == "chào"
    assert entries_after[0]["fit_status"] == "ok"  # 0.5 s fits the 2.0 s slot


def test_force_regenerates_fitted_clip(monkeypatch, tmp_path):
    job = FakeJob(tmp_path)
    entries = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "hi", "vi": "chào",
         "max_chars": 14, "reviewed": False, "tts_duration": 0.5, "fit_status": "ok"},
    ]
    job.translated_path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    fitted = job.fitted_dir / "0.wav"
    fitted.write_bytes(b"stale")  # resume condition met

    writes = {"n": 0}

    def fake_materialize(source, out, status, duration, slot, max_stretch, cancel=None):
        writes["n"] += 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"{status}-{duration}", encoding="utf-8")

    monkeypatch.setattr(s5.audio_utils, "probe_duration", lambda p: 3.0)
    monkeypatch.setattr("dubtool.stages.s4_tts.build_tts", lambda config, cache_dir, **kw: _OkTTS())
    monkeypatch.setattr(s5, "materialize_clip", fake_materialize)
    monkeypatch.setattr("dubtool.llm.LLMClient", FakeLLMOK, raising=False)
    monkeypatch.setattr("dubtool.llm.get_provider", lambda *a, **k: object())

    # Without --force and a final status: skipped entirely.
    s5.run(job, FakeConfig(), force=False)
    assert writes["n"] == 0

    # With --force: the clip is regenerated even though the status is final.
    s5.run(job, FakeConfig(), force=True)
    assert writes["n"] == 1
    assert fitted.read_text(encoding="utf-8") == "ok-0.5"

    # Change vi and rerun with --force: the fitted clip reflects the change.
    job.translated_path.write_text(
        json.dumps([dict(entries[0], vi="khác hẳn")], ensure_ascii=False), encoding="utf-8"
    )
    s5.run(job, FakeConfig(), force=True)
    assert writes["n"] == 2


def test_failure_records_capped_text_and_summary(monkeypatch, tmp_path):
    job = FakeJob(tmp_path)
    log = TTSFailureLog(job.dir)
    long_text = "x" * 500
    got = {"warned": []}

    from dubtool import tts_failures as tf

    monkeypatch.setattr(
        tf.log, "warning",
        lambda msg, *a: got["warned"].append(msg % a if a else msg),
    )
    log.record(3, 2, long_text, TTSFailure("boom throttle"), 1.5)
    data = json.loads((job.dir / "tts_failures.json").read_text(encoding="utf-8"))
    assert len(data[0]["text"]) == 120
    assert data[0]["chars"] == 500
    assert data[0]["elapsed_s"] == 1.5
    summary = log.summary(["abcd", "w1" * 9])
    assert "total_failures=1" in summary
    assert "digit_or_symbol_rate" in summary
    assert got["warned"]  # context was logged with the truncated text
