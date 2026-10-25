"""Skip/force logic tests (AGENT.md section 9) with fake stage modules."""

from __future__ import annotations

import sys
import types

import pytest

import dubtool.cli as cli
from dubtool.job import Job


class FakeStage:
    def __init__(self, calls):
        self.calls = calls

    def run(self, job, config, force=False):
        self.calls.append(force)


@pytest.fixture
def fake_all_stages(monkeypatch):
    calls: dict[str, list] = {}
    modules = {}
    for stage in cli.STAGE_ORDER:
        mod = types.ModuleType(f"fake_stage_{stage}")
        mod.run = lambda job, config, force=False, s=stage: calls.setdefault(s, []).append(force)
        modules[stage] = (mod, calls)
    monkeypatch.setattr(cli, "_stage_module", lambda name: modules[name][0])
    # Fake stages do not need a provider key
    monkeypatch.setattr(cli, "_require_provider", lambda conf, provider_id=None: None)
    return calls


def test_run_pipeline_skips_done_stages_without_force(fake_all_stages):
    calls = fake_all_stages
    job = Job(create_job_dir())
    job.start_stage("s1")
    job.finish_stage("s1")
    job.start_stage("s3")
    job.finish_stage("s3")

    conf = object()
    for stage in ["s1", "s2", "s3", "s4", "s5", "s6", "s7"]:
        cli._run_stage(job, conf, stage, force=False)

    assert "s1" not in calls  # done -> skipped
    assert "s3" not in calls  # done -> skipped
    assert set(calls) == {"s2", "s4", "s5", "s6", "s7"}


def test_run_pipeline_force_reruns_done_stages(fake_all_stages):
    calls = fake_all_stages
    job = Job(create_job_dir())
    job.finish_stage("s1")

    conf = object()
    for stage in cli.STAGE_ORDER:
        cli._run_stage(job, conf, stage, force=True)

    assert calls["s1"] == [True]
    assert set(calls) == set(cli.STAGE_ORDER)


def test_failed_stage_records_error_and_reraises(fake_all_stages, monkeypatch):
    calls = fake_all_stages
    job = Job(create_job_dir())

    def boom(job, config, force=False):
        raise RuntimeError("whisper exploded")

    mod = types.ModuleType("fake_s2")
    mod.run = boom
    monkeypatch.setattr(cli, "_stage_module", lambda name: mod if name == "s2" else fake_all_stages and None)

    conf = object()
    with pytest.raises(RuntimeError):
        cli._run_stage(job, conf, "s2")
    assert job.stage_status("s2") == "failed"
    assert job.state["last_error"] == "s2: whisper exploded"


def create_job_dir(tmp=None):
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp()) / "jobX"
    d.mkdir(parents=True, exist_ok=True)
    return d
