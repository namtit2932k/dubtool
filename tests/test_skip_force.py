"""Skip/force/resume tests (AGENT.md 9 + AGENT_UPDATE_5.md task 5.1)
with fake stage modules and no media."""

from __future__ import annotations

import os
import time
import types
from pathlib import Path

import pytest

import dubtool.cli as cli
from dubtool.config import Config
from dubtool.job import Job, stage_inputs_hash


@pytest.fixture
def conf() -> Config:
    return Config()


@pytest.fixture
def fake_all_stages(monkeypatch):
    calls: dict[str, list] = {}
    modules = {}
    for stage in cli.STAGE_ORDER:
        mod = types.ModuleType(f"fake_stage_{stage}")
        mod.run = lambda job, config, force=False, api_keys=None, cancel=None, s=stage: calls.setdefault(s, []).append(force)
        modules[stage] = (mod, calls)
    monkeypatch.setattr(cli, "_stage_module", lambda name: modules[name][0])
    # Fake stages do not need a provider key
    monkeypatch.setattr(cli, "_require_provider", lambda conf, provider_id=None: None)
    return calls


def _mark_done(job: Job, conf, stages) -> None:
    """Mark stages done and record their current inputs hash."""
    for stage in stages:
        job.start_stage(stage)
        job.finish_stage(stage, inputs_hash=stage_inputs_hash(job, conf, stage))


def test_run_pipeline_skips_done_stages_without_force(fake_all_stages, conf):
    calls = fake_all_stages
    job = Job(create_job_dir())
    # Linear prefix done (as a real run would leave it); the rest pending.
    _mark_done(job, conf, ["s1", "s2", "s3"])

    for stage in cli.STAGE_ORDER:
        cli._run_stage(job, conf, stage, force=False)

    assert not calls.get("s1") and not calls.get("s2") and not calls.get("s3")
    assert set(calls) == {"s4", "s5", "s6", "s7"}


def test_run_pipeline_force_reruns_done_stages(fake_all_stages, conf):
    calls = fake_all_stages
    job = Job(create_job_dir())
    _mark_done(job, conf, ["s1"])

    for stage in cli.STAGE_ORDER:
        cli._run_stage(job, conf, stage, force=True)

    assert calls["s1"] == [True]
    assert set(calls) == set(cli.STAGE_ORDER)


def test_failed_stage_records_error_and_reraises(fake_all_stages, monkeypatch, conf):
    calls = fake_all_stages
    job = Job(create_job_dir())

    def boom(job, config, force=False):
        raise RuntimeError("whisper exploded")

    mod = types.ModuleType("fake_s2")
    mod.run = boom
    monkeypatch.setattr(cli, "_stage_module", lambda name: mod if name == "s2" else fake_all_stages and None)

    with pytest.raises(RuntimeError):
        cli._run_stage(job, conf, "s2")
    assert job.stage_status("s2") == "failed"
    assert job.state["last_error"] == "s2: whisper exploded"


def test_resume_no_changes_skips_everything(fake_all_stages, conf):
    """An unchanged completed job skips every stage on resume."""
    calls = fake_all_stages
    job = Job(create_job_dir())
    _make_upstream_files(job, conf)
    _mark_done(job, conf, cli.STAGE_ORDER)

    for stage in cli.STAGE_ORDER:
        cli._run_stage(job, conf, stage, force=False)

    assert calls == {}  # nothing to do


def test_resume_after_rerun_of_s3_reruns_later_stages(fake_all_stages, conf, monkeypatch):
    """After stage 3 reruns, stages 4-7 execute without --force (task 5.1)."""
    calls = fake_all_stages
    job = Job(create_job_dir())
    _make_upstream_files(job, conf)
    translated = job.dir / "translated.json"

    _mark_done(job, conf, cli.STAGE_ORDER)

    # Rerun s3 forcibly; the "stage" bumps its output (translated.json).
    stamped = time.time() + 100
    mod = types.ModuleType("fake_s3")

    def fake_s3_run(job_, config_, force=False, **kw):
        calls.setdefault("s3", []).append(force)
        os.utime(translated, (stamped, stamped))

    mod.run = fake_s3_run
    monkeypatch.setattr(cli, "_stage_module", lambda name: mod)

    cli._run_stage(job, conf, "s3", force=True)

    # Reinstall the fake all-stages modules (the single-stage fake is
    # replaced; monkeypatch keeps both alive until the test ends).
    modules: dict = {}
    for stage in cli.STAGE_ORDER:
        m2 = types.ModuleType(f"fake_stage_{stage}")
        m2.run = lambda job_, config_, force=False, api_keys=None, cancel=None, s=stage: calls.setdefault(s, []).append(force)
        modules[stage] = m2
    monkeypatch.setattr(cli, "_stage_module", lambda name: modules[name])

    for stage in cli.STAGE_ORDER:
        cli._run_stage(job, conf, stage, force=False)

    assert "s3" not in [extra for extra in calls.get("s3", []) if extra is False]
    assert calls.get("s3") == [True]  # only the forced rerun
    # s4-s7 saw a changed upstream and reran WITHOUT force:
    for stage in ("s4", "s5", "s6", "s7"):
        assert stage in calls, stage
    # s1 and s2 upstreams are untouched, so they stayed skipped:
    assert set(calls) == {"s3", "s4", "s5", "s6", "s7"}


def test_resume_never_reuses_artifacts_from_different_model(fake_all_stages, conf, monkeypatch):
    """A config/model change reruns s3 and s5, others stay skipped."""
    calls = fake_all_stages
    job = Job(create_job_dir())
    _make_upstream_files(job, conf)
    _mark_done(job, conf, cli.STAGE_ORDER)

    conf2 = Config()
    conf2.llm = dict(conf.llm)
    conf2.llm["gemini"] = {"model": "gemini-other-model"}
    for stage in cli.STAGE_ORDER:
        cli._run_stage(job, conf2, stage, force=False)

    # Model change reruns s3/s5 and cascades into every later stage;
    # s1 and s2 do not use the model and stay skipped.
    assert set(calls) == {"s3", "s4", "s5", "s6", "s7"}


def _make_upstream_files(job_dir_job: Job, conf) -> None:
    d = job_dir_job.dir
    (d / "tts").mkdir(exist_ok=True)
    (d / "fitted").mkdir(exist_ok=True)
    for name in ("input.mp4", "audio.wav", "transcript.json", "translated.json",
                 "tts/a.mp3", "fitted/b.wav", "output.nosubs.mp4", "output.vi.srt"):
        p = d / name
        p.write_bytes(b"x")


def create_job_dir(tmp=None):
    import tempfile

    d = Path(tempfile.mkdtemp()) / "jobX"
    d.mkdir(parents=True, exist_ok=True)
    return d
