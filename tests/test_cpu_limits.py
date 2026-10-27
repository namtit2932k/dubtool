"""AGENT_UPDATE_6.md 4.x checks: CPU-usage mapping (4.2), the settings value
reaching Whisper and the FFmpeg encode command, the encode preset, resume
hash invariance, progress coalescing (4.3), and the profile sampler (4.1).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dubtool.app import cpu, coalesce, settings as settings_mod


# ------------------------------------------------------- 4.2 thread mapping
@pytest.mark.parametrize(
    ("cores", "level", "expected"),
    [
        (16, "low", 4), (16, "balanced", 8), (16, "maximum", 16),
        (8, "low", 2), (8, "balanced", 4), (8, "maximum", 8),
        (4, "low", 1), (4, "balanced", 2), (2, "low", 1), (2, "balanced", 2),
        (1, "low", 1), (1, "balanced", 2), (1, "maximum", 1),
        (0, "balanced", 2),  # cpu_count() fallback
    ],
)
def test_cpu_thread_budget_mapping(cores, level, expected):
    assert cpu.thread_budget(level, cores=cores) == expected


def test_cpu_thread_budget_default_balanced(tmp_path, monkeypatch):
    tmp = tmp_path / "settings.json"
    s = settings_mod.DEFAULTS
    monkeypatch.setattr(settings_mod, "path", lambda: tmp)
    monkeypatch.setattr(cpu, "current_level", lambda: "low")
    assert cpu.thread_budget(cores=8) == 2
    monkeypatch.setattr(cpu, "current_level", lambda: "maximum")
    assert cpu.thread_budget(cores=8) == 8


def test_settings_validation_of_new_values():
    good = settings_mod.DEFAULTS
    assert settings_mod.validate(good) == []
    bad = dict(good, cpu_usage="turbo", video_encode_preset="ultra")
    problems = settings_mod.validate(bad)
    assert any("cpu_usage" in p for p in problems)
    assert any("video_encode_preset" in p for p in problems)


# -------------------------------------- 4.2 settings reaching Whisper + ffmpeg
class _Job2:
    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        (self.dir / "s1").mkdir(exist_ok=True)
        self.audio_path = self.dir / "s1" / "audio.wav"
        self.audio_path.write_bytes(b"RIFFdata")
        self.transcript_path = self.dir / "transcript.json"


class _Cfg2:
    whisper_model = "tiny"
    device = "cpu"
    compute_type = "int8"
    asr = {"dedupe_min_words": 3, "condition_on_previous_text": False}


def test_whisper_gets_thread_budget(monkeypatch, tmp_path):
    import dubtool.stages.s2_transcribe as s2

    captured = {}

    class FakeModel:
        def __init__(self, model, device=None, compute_type=None, cpu_threads=None):
            captured["kwargs"] = dict(cpu_threads=cpu_threads, device=device)

        def transcribe(self, path, **kw):
            class _Seg:
                start = 0.0
                end = 1.0
                text = "hi"

            return iter([_Seg()]), SimpleNamespace(duration=1.0, language="en")

    monkeypatch.setattr(s2, "audio_utils", SimpleNamespace(require_ffmpeg=lambda: None))
    monkeypatch.setattr(cpu, "thread_budget", lambda **k: 3)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(cpu, "lower_app_priority", lambda: None)
    monkeypatch.setattr(cpu, "restore_app_priority", lambda: None)

    s2.run(_Job2(tmp_path), _Cfg2(), force=True)
    assert captured["kwargs"]["cpu_threads"] == 3
    assert not cpu._saved_priority  # priority state restored after the stage


def test_encode_preset_and_threads_reach_ffmpeg(monkeypatch, tmp_path):
    """4.2: the saved preset and the thread budget appear on the encode
    command line, and the encode runs below normal priority."""
    import dubtool.stages.s7_subtitles as s7

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = list(cmd)
        captured["low_priority"] = kw.get("low_priority")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(s7.proc, "run", fake_run)
    monkeypatch.setattr(s7.audio_utils, "ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(s7.audio_utils, "probe_video_info", lambda p: {"height": 720, "width": 1280})
    monkeypatch.setattr(s7.subs, "find_system_font", lambda font: (Path("fakefont.ttf"), "Arial"))
    monkeypatch.setattr(
        "dubtool.subtitles.build_srt", lambda *a, **k: "1\n0:00:00,000 --> 0:00:01,000\nx\n"
    )
    monkeypatch.setattr(s7, "build_burn_filter", lambda **kw: "fakefilter")
    monkeypatch.setattr(s7.shutil, "copyfile", lambda a, b: None)
    monkeypatch.setattr(s7.os, "replace", lambda a, b: None)  # fake encode makes no tmp file

    (tmp_path / "input.mp4").write_bytes(b"x")
    entries = [{"id": 0, "vi": "x", "start": 0.0, "end": 1.0}]
    monkeypatch.setattr(s7, "read_json", lambda p: entries)

    job = SimpleNamespace(
        dir=tmp_path,
        srt_path=tmp_path / "subs.srt",
        fonts_dir=tmp_path / "fonts",
        translated_path=tmp_path / "translated.json",
        output_nosubs_path=tmp_path / "input.mp4",
        output_path=tmp_path / "out.mp4",
        fitted_dir=tmp_path / "fitted",
    )
    cfg = {"burn": True, "preset": "veryfast", "crf": 18}
    config = SimpleNamespace(subtitles=cfg, encode_threads=4)
    s7.run(job, config, force=True)

    cmd = captured.get("cmd") or []
    assert "-preset" in cmd and "veryfast" in cmd
    assert "-threads" in cmd and "4" in cmd
    assert captured.get("low_priority") is True


# -------------------------------------------------- 4.2 resume hash invariance
def test_resume_hash_ignores_cpu_settings(tmp_path):
    """Only the CPU setting changes -> the s7 stage-input hash is unchanged;
    changing the encode preset DOES change it (it changes the file)."""
    from dubtool.config import Config
    from dubtool.job import stage_inputs_hash

    job_dir = tmp_path / "j"
    (job_dir / "s7").mkdir(parents=True)
    (job_dir / "state.json").write_text("{}", encoding="utf-8")
    job = __import__("dubtool.job", fromlist=["Job"]).Job(job_dir)

    cfg = Config()
    base = stage_inputs_hash(job, cfg, "s7")
    cfg2 = Config()
    cfg2.encode_threads = 7  # the new speed-only attribute
    assert stage_inputs_hash(job, cfg2, "s7") == base
    cfg3 = Config()
    cfg3.subtitles["preset"] = "veryfast"  # changes the produced file
    assert stage_inputs_hash(job, cfg3, "s7") != base


# --------------------------------------------------------- 4.3 coalescing
class TestProgressGate:
    def test_coalesces_to_ten_per_second(self):
        gate = coalesce.ProgressGate(limit_per_second=10)
        rendered = []
        # Timestamps inside the *first* flush slot (gate starts last_flush=0,
        # interval 0.1 s) so events actually queue instead of flushing.
        for i in range(100):
            rendered += gate.feed("s4", "progress", i, 82, "m", 0.0, 0.05 + i * 0.01)
        rendered += gate.drain(1.30)  # events still pending after the last slot
        # 100 events over 1.0 s: flush slots at least once per 0.1 s => >=10,
        # <= ~12 flush moments; never more than 100.
        assert 10 <= len(rendered) <= 12

    def test_latest_per_stage_slot_wins(self):
        gate = coalesce.ProgressGate(limit_per_second=10)
        t = 0.05  # inside the first flush slot: nothing flushes on first feed
        gate.feed("s3", "progress", 1, 5, "old", 0.0, t)
        gate.feed("s4", "progress", 2, 8, "old", 0.0, t)
        gate.feed("s4", "progress", 3, 8, "new", 0.0, t)
        events = gate.drain(t + 1.0)
        s4 = [e for e in events if e[0] == "s4"]
        assert s4 == [("s4", "progress", 3, 8, "new", 0.0)]  # latest only

    def test_terminal_events_never_dropped(self):
        gate = coalesce.ProgressGate(limit_per_second=10)
        t = 0.05
        gate.feed("s7", "progress", 1, 2, "", 0.0, t)     # queued
        out = gate.feed("s7", "done", 0, 0, "", -1.0, t)  # terminal passes now
        states = [e[1] for e in out]
        assert states == ["progress", "done"]             # buffered then done

    def test_drain_empty_returns_nothing(self):
        gate = coalesce.ProgressGate()
        assert gate.drain(time.monotonic()) == []


def test_rules_keep_done_error_skipped_in_terminal_states():
    for state in ("done", "error", "skipped"):
        assert state in coalesce.TERMINAL_STATES


# ------------------------------------------------------------ 4.1 profiler
def test_profile_sampler_writes_csv_rows(tmp_path):
    from dubtool.app.profile import ProfileSampler

    stage = {"name": "s1"}
    sampler = ProfileSampler(tmp_path / "profile.csv", interval=0.05,
                             stage_getter=lambda: stage["name"])
    sampler.start()
    time.sleep(0.3)
    stage["name"] = "s2"
    time.sleep(0.3)
    rows = sampler.stop()
    assert rows and len(rows) >= 3
    with (tmp_path / "profile.csv").open(encoding="utf-8") as fh:
        header = fh.readline().strip()
    assert header.split(",")[:2] == ["elapsed_s", "stage"]
    assert any(r["stage"] == "s2" for r in rows)


def test_sampler_stops_on_event(tmp_path):
    from dubtool.app.profile import ProfileSampler

    stop_event = threading.Event()
    sampler = ProfileSampler(tmp_path / "p.csv", interval=0.05)
    sampler._stop = stop_event
    sampler.start()
    stop_event.set()
    assert sampler._thread.join(timeout=2) is None  # exited without hanging
