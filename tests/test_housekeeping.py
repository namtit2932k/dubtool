"""AGENT_UPDATE_6.md 6.8 checks for job-folder pruning (task 5, 6.7).

Rules under test: delete only folders inside the jobs directory that are
older than the setting AND older than the 3-day resumability window; never
the running job; the model cache is never touched by pruning.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dubtool.app import housekeeping as hk

NOW = 1_800_000_000.0


def make_job(jobs: Path, name: str, *, age_days: float, running: bool = False) -> Path:
    p = jobs / name
    p.mkdir(parents=True)
    st = {"stages": {"s7": "running"} if running else {"s7": "done"}}
    (p / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (p / "output.mp4").write_bytes(b"x" * 100)
    t = NOW - age_days * 86400
    os.utime(p, (t, t))
    os.utime(p / "state.json", (t, t))
    return p


def build(tmp_path: Path) -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    return jobs


def test_old_finished_job_deleted(tmp_path):
    j = make_job(build(tmp_path), "aaa111", age_days=20)
    expected = hk.tree_size(j)  # measured before pruning deletes it
    deleted, rec = hk.prune_jobs(tmp_path / "jobs", days=14, now=NOW)
    assert deleted == [j] and rec == expected


def test_recent_within_setting_kept(tmp_path):
    make_job(build(tmp_path), "bbb222", age_days=5)
    deleted, rec = hk.prune_jobs(tmp_path / "jobs", days=14, now=NOW)
    assert deleted == [] and rec == 0


def test_recent_resumable_window_beats_small_setting(tmp_path):
    """A resumable job from the last 3 days survives even days=1."""
    make_job(build(tmp_path), "ccc333", age_days=2)
    deleted, _ = hk.prune_jobs(tmp_path / "jobs", days=1, now=NOW)
    assert deleted == []


def test_running_job_never_deleted(tmp_path):
    make_job(build(tmp_path), "ddd444", age_days=30, running=True)
    deleted, _ = hk.prune_jobs(tmp_path / "jobs", days=14, now=NOW)
    assert deleted == []


def test_model_cache_never_touched(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "small.bin").write_bytes(b"f" * 10)
    make_job(build(tmp_path), "eee555", age_days=30)
    hk.prune_jobs(tmp_path / "jobs", days=14, now=NOW)
    assert (models / "small.bin").exists()


def test_symlink_removed_not_target(tmp_path):
    jobs = build(tmp_path)
    target = tmp_path / "real_target"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    link = jobs / "lnk"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        import pytest

        pytest.skip(f"symlink unavailable: {exc}")
    hk.prune_jobs(jobs, days=1, now=NOW)
    assert not link.exists()
    assert (target / "keep.txt").exists()


def test_tree_size_and_human(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "f.bin").write_bytes(b"0" * 1024)
    assert hk.tree_size(d) == 1024
    assert hk.human(2048) == "2.0 KB"


def test_validation_allows_jobs_days_setting():
    from dubtool.app import settings as s

    good = dict(s.DEFAULTS, jobs_max_days=14)
    assert s.validate(good) == []
    bad = dict(s.DEFAULTS, jobs_max_days=0)
    assert any("jobs_max_days" in p for p in s.validate(bad))
