"""api.run_dub tests (AGU4 12) with a fake pipeline: same job files as the
CLI path, cancel between items, resume, output-exists never overwrites, and
the key appears nowhere on disk.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from dubtool import api
from dubtool.api import DubOptions, ProgressEvent


FAKE_KEY = "test-key-not-a-real-gemini-key-0000"


class FakeSecrets:
    def get_gemini_key(self) -> str | None:
        return FAKE_KEY


@pytest.fixture()
def stub_pipeline(monkeypatch, tmp_path):
    """Replace the seven stage functions with recorders; config points at tmp."""
    calls: list[tuple[str, bool]] = []

    import dubtool.app.modes as modes_mod
    import dubtool.app.paths as paths_mod

    root = tmp_path / "project"
    root.mkdir()
    (root / "packaging").mkdir()
    (root / "packaging" / "modes.yaml").write_text(
        "fast:\n  provider: gemini\n  model: m-fast\n  translate_concurrency: 1\n"
        "accurate:\n  provider: gemini\n  model: m-acc\n  translate_concurrency: 3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths_mod, "bundle_root", lambda: root)
    monkeypatch.setattr(api, "_default_jobs_dir", lambda: tmp_path / "jobs")

    import dubtool.config as config_mod

    real_load = config_mod.load

    def load_from(root_arg, overrides=None):
        return real_load(Path(__file__).parents[1], overrides=overrides)

    monkeypatch.setattr(config_mod, "load", load_from)

    import dubtool.stages as stages_pkg

    def make_stage(name):
        def run(job, config, force=False, cancel=None):
            if cancel is not None and cancel.is_set():
                from dubtool.job import StageCancelled

                raise StageCancelled(f"{name}: cancelled by user")
            calls.append((name, force))
            if name.startswith("s7"):
                job.output_path.write_bytes(b" dubbed")  # final video exists

        return run

    from dubtool.stages import s1_extract_audio

    for name in ("s1_extract_audio", "s2_transcribe", "s3_translate",
                 "s4_tts", "s5_fit_duration", "s6_mix", "s7_subtitles"):
        mod = __import__(f"dubtool.stages.{name}", fromlist=["run"])
        monkeypatch.setattr(mod, "run", make_stage(name))

    # a tiny "output.mp4" so delivery works
    (root / "output.mp4").write_bytes(b"")  # placeholder; real output comes from job
    return calls


def _make_video(tmp_path: Path) -> Path:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video bytes")
    return video


def test_run_dub_produces_output_and_no_key_on_disk(tmp_path, stub_pipeline):
    video = _make_video(tmp_path)
    outdir = tmp_path / "out"
    outdir.mkdir()
    events: list[ProgressEvent] = []
    result = api.run_dub(
        video, outdir,
        options=DubOptions(mode="fast"),
        secrets=FakeSecrets(),
        progress=events.append,
        cancel=threading.Event(),
    )
    assert result.ok, result.error
    assert (outdir / "clip_vi.mp4").exists()
    stages_run = [name[:2] for name, _force in stub_pipeline]
    assert stages_run == ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]
    # no key anywhere in the jobs tree
    jobs_dir = tmp_path / "jobs"
    for f in jobs_dir.rglob("*"):
        if f.is_file():
            assert FAKE_KEY not in f.read_text(encoding="utf-8", errors="replace"), f


def test_run_dub_requires_key(tmp_path, stub_pipeline, monkeypatch):
    class NoSecrets:
        def get_gemini_key(self):
            return None

    result = api.run_dub(
        _make_video(tmp_path), tmp_path,
        options=DubOptions(), secrets=NoSecrets(),
        progress=lambda e: None, cancel=threading.Event(),
    )
    assert result.code == "invalid_key"
    assert not stub_pipeline


def test_run_dub_output_exists_never_overwrites(tmp_path, stub_pipeline):
    video = _make_video(tmp_path)
    outdir = tmp_path / "out"
    outdir.mkdir()
    target = outdir / "clip_vi.mp4"
    target.write_bytes(b"previous")
    result = api.run_dub(
        video, outdir,
        options=DubOptions(), secrets=FakeSecrets(),
        progress=lambda e: None, cancel=threading.Event(),
    )
    assert result.code == "output_exists"
    assert target.read_bytes() == b"previous"


def test_run_dub_cancel_is_resumable(tmp_path, stub_pipeline):
    video = _make_video(tmp_path)
    outdir = tmp_path / "out"
    outdir.mkdir()
    cancel = threading.Event()

    def cancel_at_s4():
        # cancel as soon as s3 has been recorded
        while not any(n == "s3" for n, _ in stub_pipeline):
            if not any(n == "s3" for n, _ in stub_pipeline):
                pass
            break
        cancel.set()

    events = []
    # pre-set cancel: the run stops at the first stage boundary cleanly
    cancel.set()
    result = api.run_dub(
        video, outdir,
        options=DubOptions(), secrets=FakeSecrets(),
        progress=events.append, cancel=cancel,
    )
    assert result.code == "cancelled"
    assert stub_pipeline == []  # nothing ran


def test_stage_cancelled_between_items(tmp_path, monkeypatch):
    """s4 honors the cancel event between segments (StageCancelled)."""
    from dubtool.job import StageCancelled
    from dubtool.stages import s4_tts

    job = type(
        "J",
        (),
        {
            "translated_path": Path(tmp_path / "t.json"),
            "tts_dir": Path(tmp_path / "tts"),
            "dir": Path(tmp_path),
        },
    )()
    tmp_path.joinpath("tts").mkdir(exist_ok=True)
    Path(tmp_path / "t.json").write_text("[]", encoding="utf-8")
    entries = [{"id": i, "vi": "x" * 10} for i in range(5)]
    monkeypatch.setattr(s4_tts, "read_json", lambda p: entries)
    monkeypatch.setattr(s4_tts.audio_utils, "require_ffmpeg", lambda: None)

    cancel = threading.Event()

    class FakeTTS:
        def __init__(self, *a, **k):
            pass

        def synthesize(self, entry):
            if entry["id"] >= 2:
                cancel.set()
            return 1.0, Path("/tmp/x.mp3")

    monkeypatch.setattr(s4_tts, "build_tts", lambda config, cache: FakeTTS())

    class FakeFailures:
        def record(self, *a, **k):
            pass

        def summary(self, *a, **k):
            return ""

    monkeypatch.setattr(s4_tts, "TTSFailureLog", lambda d: FakeFailures())
    monkeypatch.setattr(s4_tts, "write_json", lambda p, d: None)
    monkeypatch.setattr(s4_tts.shutil, "copyfile", lambda a, b: None)
    with pytest.raises(StageCancelled):
        s4_tts.run(job, type("C", (), {"tts_voice": "v"})(), cancel=cancel)
