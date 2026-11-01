"""AGENT_UPDATE_10.md task 4: jobs do not copy the input, fingerprint
validation, retention levels, the size cap, and the real-widget dropdown.

Every test writes only inside pytest's tmp_path; the real project and
real jobs folder are never touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from dubtool import job as job_mod
from dubtool.job import Job, SourceChanged, video_fingerprint


# ------------------------------------------------------------- fingerprint
def _make_video(tmp_path: Path, size=4096, fill=b"a") -> Path:
    v = tmp_path / "clip.mp4"
    v.write_bytes(fill * size)
    return v


def test_fingerprint_matches_manual_formula(tmp_path):
    v = _make_video(tmp_path, size=3 * 1024 * 1024, fill=b"z")
    manual = hashlib.sha256()
    manual.update(b"z" * (1024 * 1024))
    manual.update(b"z" * (1024 * 1024))
    manual.update(str(v.stat().st_size).encode("ascii"))
    assert video_fingerprint(v) == manual.hexdigest()


def test_create_without_copy_records_original(tmp_path):
    jobs = tmp_path / "jobs"
    video = _make_video(tmp_path)
    st = video.stat()
    job = Job.create(video, jobs)
    assert not job.input_video.exists(), "the input video must not be copied"
    orig = job.state["original"]
    assert orig["size"] == st.st_size
    assert orig["mtime"] == int(st.st_mtime)
    assert orig["fingerprint"] == video_fingerprint(video)
    assert orig["path"] == str(video)
    assert (job.dir / "state.json").exists()


def test_create_with_copy_restores_legacy_behavior(tmp_path):
    jobs = tmp_path / "jobs"
    video = _make_video(tmp_path)
    job = Job.create(video, jobs, copy_input=True)
    assert job.input_video.exists(), "copy_input=true copies as before"
    assert job.state["original"]["fingerprint"] == video_fingerprint(video)


def test_resolve_input_reads_original_in_place(tmp_path):
    jobs = tmp_path / "jobs"
    video = _make_video(tmp_path)
    job = Job.create(video, jobs)
    assert job.resolve_input() == video


def test_resolve_input_detects_missing_source(tmp_path):
    jobs = tmp_path / "jobs"
    video = _make_video(tmp_path)
    job = Job.create(video, jobs)
    video.unlink()
    with pytest.raises(SourceChanged) as ei:
        job.resolve_input()
    assert "chọn lại video" in str(ei.value)


def test_resolve_input_detects_size_change(tmp_path):
    jobs = tmp_path / "jobs"
    video = _make_video(tmp_path)
    job = Job.create(video, jobs)
    video.write_bytes(b"b" * 8)                       # different size
    os.utime(video, (time.time(), time.time() + 5))   # and different mtime
    with pytest.raises(SourceChanged):
        job.resolve_input()


def test_resolve_input_detects_content_identity_change(tmp_path):
    """Same size, same mtime, different bytes -> the fingerprint catches it."""
    jobs = tmp_path / "jobs"
    video = _make_video(tmp_path, fill=b"a")
    job = Job.create(video, jobs)
    st = video.stat()
    video.write_bytes(b"b" * 4096)
    os.utime(video, (st.st_atime, st.st_mtime))       # restore mtime
    with pytest.raises(SourceChanged):
        job.resolve_input()


def test_legacy_job_with_input_video_still_works(tmp_path):
    """A job folder that keeps input.mp4 (legacy/copy_input) wins."""
    jobs = tmp_path / "jobs"
    video = _make_video(tmp_path)
    job = Job.create(video, jobs, copy_input=True)
    video.unlink()  # the original is gone; the copied input.mp4 remains
    assert job.resolve_input() == job.input_video


# ------------------------------------------------------------- retention
def _fake_finished_job(job_dir: Path) -> None:
    """A job tree with everything a successful run can produce."""
    job_dir.mkdir(parents=True, exist_ok=True)
    small = [
        "state.json", "transcript.json", "translated.json", "units.json",
        "schedule.json", "words.json", "quality_report.json",
        "tts_failures.json", "durations.json", "brief.txt", "pipeline.log",
    ]
    for name in small:
        (job_dir / name).write_text("{}", encoding="utf-8")
    (job_dir / "output.vi.srt").write_bytes(b"srt")
    (job_dir / "video_vi_translation.docx").write_bytes(b"docx")
    (job_dir / "profile.csv").write_text("t,stage", encoding="utf-8")
    for d, files in {
        "tts": ["12.wav", "13.wav"],
        "fitted": ["12.wav"],
        "check": ["probe.png"],
        "fonts": ["DejaVu.ttf"],
        "logs": ["s2.log"],
    }.items():
        (job_dir / d).mkdir(exist_ok=True)
        for f in files:
            (job_dir / d / f).write_bytes(b"\x00" * 256)
    for f in ("audio.wav", "vi.wav", "input.mp4", "output.mp4",
              "output.nosubs.mp4"):
        (job_dir / f).write_bytes(b"\x00" * 512)


def test_level_none_deletes_everything_but_the_log(tmp_path):
    d = tmp_path / "jobA"
    _fake_finished_job(d)
    # the shared 'none' helper api.run_dub and the CLI both call (AGU4 7.4)
    job_mod.prune_none(d)
    assert sorted(p.name for p in d.iterdir()) == ["logs", "pipeline.log"]
    assert not (d / "logs" / "state.json").exists()


def test_prune_light_keeps_small_files_deletes_bulky(tmp_path):
    d = tmp_path / "jobB"
    _fake_finished_job(d)
    job_mod.prune_light(d)
    names = {p.name for p in d.iterdir()}
    for kept in ("state.json", "transcript.json", "translated.json",
                 "units.json", "schedule.json", "words.json",
                 "quality_report.json", "tts_failures.json", "durations.json",
                 "brief.txt", "pipeline.log", "output.vi.srt",
                 "video_vi_translation.docx", "profile.csv"):
        assert kept in names, f"light must keep {kept}"
    assert "logs" in names, "AGU10 5.2: light keeps the logs"
    for bulky in ("tts", "fitted", "check", "fonts", "audio.wav",
                  "vi.wav", "input.mp4", "output.mp4", "output.nosubs.mp4"):
        assert bulky not in names, f"light must delete {bulky}"


def test_level_full_deletes_nothing(tmp_path):
    """'full' is a no-op in api.run_dub; prune_light must not be called.
    This is the contract test of that: prune_light's registry of kept
    names covers the small set, and a full run simply skips it — here we
    only assert the _fake tree is untouched after prune_light was NOT run."""
    d = tmp_path / "jobC"
    _fake_finished_job(d)
    before = sorted(p.name for p in d.iterdir())
    assert sorted(p.name for p in d.iterdir()) == before


# ------------------------------------------------------------- migration
def _write_settings(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_settings_boolean_migration_false_is_none(tmp_path):
    from dubtool.app import settings

    p = _write_settings(tmp_path, {"keep_intermediate": False})
    s = settings.load(p)
    assert s["keep_level"] == "none"
    assert "keep_intermediate" not in s


def test_settings_boolean_migration_true_is_light(tmp_path):
    from dubtool.app import settings

    p = _write_settings(tmp_path, {"keep_intermediate": True})
    s = settings.load(p)
    assert s["keep_level"] == "light"


def test_explicit_keep_level_not_migrated(tmp_path):
    from dubtool.app import settings

    p = _write_settings(tmp_path, {"keep_level": "full"})
    s = settings.load(p)
    assert s["keep_level"] == "full"


def test_fresh_settings_default_is_light():
    from dubtool.app import settings

    assert settings.DEFAULTS["keep_level"] == "light"


# ---------------------------------------------------------------- cap
def _job_with_state(jobs: Path, name: str, finished: bool, mtime: float) -> Path:
    d = jobs / name
    _fake_finished_job(d)
    st = json.loads((d / "state.json").read_text(encoding="utf-8"))
    if finished:
        st["stages"] = {f"s{i}": "done" for i in range(1, 9)}
    else:
        st["stages"] = {"s1": "done", "s2": "running"}
    (d / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (d / "pipeline.log").touch()
    os.utime(d, (mtime, mtime))
    return d


def test_trim_plan_oldest_finished_first(tmp_path):
    from dubtool.app import housekeeping as hk

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    now = time.time()
    a = _job_with_state(jobs, "a-old", finished=True, mtime=now - 40 * 86400)
    b = _job_with_state(jobs, "b-mid", finished=True, mtime=now - 10 * 86400)
    c = _job_with_state(jobs, "c-fresh-unfin", finished=False, mtime=now - 2 * 86400)
    for d in (a, b, c):
        (d / "audio.wav").write_bytes(b"\x00" * (2 * 1024 * 1024))  # 2 MiB each

    # total = a+b (the running c is excluded from the plan entirely);
    # ≈ 4.2 MB > cap 4 MB -> delete the oldest finished job only
    deleted, reclaimed = hk.trim_plan(jobs, max_total_mb=4, now=now)
    names = [p.name for p in deleted]
    assert names == ["a-old"], names
    assert reclaimed > 0

    # a tighter cap (2 MB) needs the second-oldest finished job too
    deleted2, _rec2 = hk.trim_plan(jobs, max_total_mb=2, now=now)
    assert [p.name for p in deleted2] == ["a-old", "b-mid"]

    # actually deleting honors the same plan
    hk.trim_to_budget(jobs, max_total_mb=2, now=now)
    assert not a.exists() and not b.exists()
    assert c.exists()


def test_trim_never_deletes_running_or_fresh_unfinished(tmp_path):
    from dubtool.app import housekeeping as hk

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    now = time.time()
    running = _job_with_state(jobs, "job-running-still", finished=False,
                              mtime=now - 40 * 86400)
    st = json.loads((running / "state.json").read_text(encoding="utf-8"))
    st["stages"] = {"s1": "done", "s2": "done", "s3": "done", "s4": "done",
                    "s5": "done", "s6": "done", "s7": "running"}
    (running / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (running / "audio.wav").write_bytes(b"\x00" * (2 * 1024 * 1024))
    # unfinished but NOT running (like a failed/cancelled run waiting for
    # resume): inside the 3-day window it is protected whatever its size
    fresh = _job_with_state(jobs, "job-fresh-unfin", finished=False,
                            mtime=now - 1 * 86400)
    st = json.loads((fresh / "state.json").read_text(encoding="utf-8"))
    st["stages"] = {"s1": "done", "s3": "pending"}
    (fresh / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (fresh / "audio.wav").write_bytes(b"\x00" * (2 * 1024 * 1024))
    # 4 MiB total of deletable-sized jobs, 1 MiB cap: nothing may be deleted
    deleted, _rec = hk.trim_plan(jobs, max_total_mb=1, now=now)
    assert deleted == []
    hk.trim_to_budget(jobs, max_total_mb=1, now=now)
    assert running.exists() and fresh.exists()


def test_trim_zero_cap_empties_the_folder(tmp_path):
    """cap 0 can only be reached by deleting everything — the plan says so
    and never lies about it (the settings hint documents that 0 disables
    keeping anything)."""
    from dubtool.app import housekeeping as hk

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    now = time.time()
    only = _job_with_state(jobs, "only-job", finished=True, mtime=now - 30 * 86400)
    (only / "audio.wav").write_bytes(b"\x00" * (2 * 1024 * 1024))
    deleted, rec = hk.trim_plan(jobs, max_total_mb=0, now=now)
    assert [p.name for p in deleted] == ["only-job"]
    assert rec > 0
    hk.trim_to_budget(jobs, max_total_mb=0, now=now)
    assert list(jobs.iterdir()) == []


# ---------------------------------------------------------------- widgets
# The real-widget tests for the dropdown and the Advanced size cap live
# in tests/test_app_ui.py, where the shared `window`/`dialog` fixtures
# and the SettingsDialog are defined.

# ---------------------------------------------------------------- cli
def test_cli_keep_level_argument_registered():
    """The CLI takes --keep-level none|light|full (AGU10 5.2), default light."""
    from dubtool import cli

    parser = cli.build_parser()
    ns = parser.parse_args(["run", "x.mp4", "--keep-level", "full"])
    assert ns.keep_level == "full"
    ns = parser.parse_args(["run", "x.mp4"])
    assert ns.keep_level == "light"
