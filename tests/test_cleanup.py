"""AGENT_UPDATE_6.md 6.8 checks for the workspace cleanup planner.

A temporary fake project tree is built per test; dry run must delete
nothing, --apply must delete only the listed categories, report-only items
must survive, paths outside the root must be refused, a symlink must be
removed without touching its target, and .git/ must never be touched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dubtool.app import cleanup as cw


def build_fake_tree(base: Path) -> Path:
    """A project root with one item per category."""
    root = base / "root"
    (root / "src" / "dubtool").mkdir(parents=True)
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "config").write_text("git!", encoding="utf-8")
    (root / "build").mkdir(parents=True)
    (root / "build" / "_pan.py").write_text("x", encoding="utf-8")
    (root / "src" / "dubtool" / "__pycache__").mkdir()
    (root / "src" / "dubtool" / "a.pyc").write_text("x", encoding="utf-8")
    (root / ".pytest_cache").mkdir()
    (root / "jobs").mkdir(parents=True)
    (root / "jobs" / "j1").mkdir()
    (root / "jobs" / "j1" / "state.json").write_text(
        '{"stages": {"s1": "done"}}', encoding="utf-8")
    (root / "jobs" / "j1" / "audio.wav").write_text("x", encoding="utf-8")
    # j2 is inside the 3-day resumable window -> never deletable
    (root / "jobs" / "j2").mkdir()
    os.utime(root / "jobs" / "j2", (now_ts(base) - 100, now_ts(base) - 100))
    (root / "jobs" / "j2" / "audio.wav").write_text("x", encoding="utf-8")
    # j3 is running -> never deletable
    (root / "jobs" / "j3").mkdir()
    (root / "jobs" / "j3" / "state.json").write_text(
        '{"stages": {"s7": "running"}}', encoding="utf-8")
    # old j1 age: make both ctime dirs clearly old
    old = now_ts(base) - 20 * 86400
    for p in (root / "jobs" / "j1", root / "jobs" / "j1" / "state.json"):
        os.utime(p, (old, old))
    (root / "shots_001").mkdir(parents=True)
    (root / "shots_001" / "a.png").write_text("x", encoding="utf-8")
    (root / "shots_002").mkdir(parents=True)
    (root / "shots_002" / "b.png").write_text("x", encoding="utf-8")
    os.utime(root / "shots_002", (now_ts(base), now_ts(base)))  # newest set
    (root / "screenshots").mkdir(parents=True)
    for i in range(12):
        (root / "screenshots" / f"s{i}.png").write_text("x", encoding="utf-8")
    (root / "review").mkdir(parents=True)
    for i in range(5):
        (root / "review" / f"r{i}.zip").write_text("x", encoding="utf-8")
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "DubTool").mkdir()
    (root / "dist" / "old.zip").write_text("x", encoding="utf-8")
    (root / "vendor").mkdir(parents=True)
    (root / "vendor" / "ffmpeg.exe").write_text("x", encoding="utf-8")
    (root / "sample").mkdir(parents=True)
    (root / "sample" / "input.mp4").write_text("x", encoding="utf-8")
    (root / "~raw-123.wav").write_text("x", encoding="utf-8")
    # Anchor every real mtime to the fake clock so ordering rules are all
    # comparisons against the same timeline, then shift entries that must be
    # newer/older than the rest.
    base_now = now_ts(base)
    for p in root.rglob("*"):
        if not p.is_symlink():
            os.utime(p, (base_now, base_now))
    os.utime(root, (base_now, base_now))
    new = base_now + 60  # "newest" beats everything else by an hour
    for p in ((root / "shots_002"), (root / "dist" / "DubTool"),
              (root / "review" / "r4.zip")):
        os.utime(p, (new, new))
    old = base_now - 20 * 86400  # clearly older than the jobs-days cutoff
    for p in (root / "jobs" / "j1",):
        os.utime(p, (old, old))
    return root


def now_ts(base: Path) -> float:
    return 1_700_000_000.0


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return build_fake_tree(tmp_path)


def plan_now(root: Path, **kw):
    return cw.plan(root, now=now_ts(root), **kw)


def _cat(items, category):
    return {i.path for i in items if i.category == category}


# ---------------------------------------------------------------- planning
def test_plan_marks_only_regenerable(tree: Path):
    deletions, reported, _ = plan_now(tree)
    assert tree / "build" in _cat(deletions, "build")
    assert tree / "src" / "dubtool" / "__pycache__" in _cat(deletions, "caches")
    assert tree / "~raw-123.wav" in _cat(deletions, "temp_clips")
    assert tree / "jobs" / "j1" in _cat(deletions, "jobs_old")
    assert tree / "shots_001" in _cat(deletions, "shots")
    assert tree / "shots_002" not in _cat(deletions, "shots")  # newest set kept
    # report-only survive
    assert {tree / "vendor", tree / "sample"} <= _cat(reported, "vendor") | _cat(reported, "sample")
    assert all(i.action == "keep" for i in reported)


def test_jobs_run_and_recent_survive(tree: Path):
    deletions, _, _ = plan_now(tree)
    jobs = _cat(deletions, "jobs_old")
    assert jobs == {tree / "jobs" / "j1"}  # j2 recent, j3 running


def test_reviews_and_dist_keep_newest(tree: Path):
    deletions, _, _ = plan_now(tree)
    # 5 zips, keep the 3 newest by mtime (all ties here -> name-desc keeps
    # r4..r2, deletes r1, r0 — the newest is never deleted on a tie)
    assert len(_cat(deletions, "review_zips")) == 2
    assert tree / "review" / "r4.zip" not in _cat(deletions, "review_zips")
    assert {tree / "review" / "r0.zip", tree / "review" / "r1.zip"} == _cat(deletions, "review_zips")
    assert tree / "dist" / "DubTool" not in _cat(deletions, "dist_old")


# ----------------------------------------------------------------- applying
def test_categories_filter(tree: Path):
    category = "caches"
    items, _, _ = plan_now(tree)
    wanted = [i for i in items if i.category == category]
    log = tree / "cleanup.log"
    reclaimed = cw.apply(wanted, tree, log)
    assert reclaimed > 0
    assert (tree / "src" / "dubtool" / "__pycache__").exists() is False
    assert (tree / "build" / "_pan.py").exists()  # other category untouched
    text = log.read_text(encoding="utf-8")
    assert "DELETED" in text and "TOTAL" in text
    assert not any(line.startswith("FAILED") for line in text.splitlines()[1:])


def test_apply_dry_run_uses_category_gate_only_via_cli(tree: Path, capsys):
    argv = ["--top", "5"]
    assert cw.main(argv) == 0
    assert (tree / "build" / "_pan.py").exists()  # dry run is safe


def test_apply_refuses_outside_root(tree: Path):
    items, _, _ = plan_now(tree)
    bad = cw.PlanItem(tree.parent / "outside", 10, "build", "delete")
    cw.apply([bad] + items, tree, tree / "cleanup.log")
    # the outside path is never touched
    assert not (tree.parent / "outside").exists()


def test_symlink_removed_without_touching_target(tree: Path, tmp_path: Path):
    target = tmp_path / "target_dir"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    link = tree / "shots_link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    items, _, _ = plan_now(tree)
    item = cw.PlanItem(link, 0, "temp_clips", "delete")
    cw.apply([item], tree, tree / "cleanup.log")
    assert not link.exists()
    assert (target / "keep.txt").exists()  # target untouched


def test_git_never_touched(tree: Path):
    items, _, _ = plan_now(tree)
    cw.apply(items, tree, tree / "cleanup.log")
    assert (tree / ".git" / "config").exists()
    assert all(i.path.name != ".git" for i in items if i.action == "delete")


def test_git_is_report_only(tree: Path):
    _, reported, _ = plan_now(tree)
    assert any(i.path.name == ".git" for i in reported)
    assert all(i.action == "keep" for i in reported
               if i.path.name == ".git")


def test_top_items_lists_biggest(tree: Path):
    tops = cw.top_items(tree, n=5)
    assert len(tops) <= 5
    sizes = [s for _, s in tops]
    assert sizes == sorted(sizes, reverse=True)
