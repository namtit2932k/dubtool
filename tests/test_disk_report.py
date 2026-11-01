"""AGU10 task 7 — the disk report, the documented-entries check, and the
cleanup script on the new layout.

Run against a fake tree, never the real project (the report walks files).
"""

from __future__ import annotations

from pathlib import Path

from dubtool import disk_report


def _fake_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src" / "dubtool").mkdir(parents=True)
    (root / "src" / "dubtool" / "app.py").write_text("#" * 40, encoding="utf-8")
    (root / "README.md").write_text("x\n", encoding="utf-8")
    (root / "config.yaml").write_text("chars_per_sec: 14\n", encoding="utf-8")
    # secret file whose CONTENT must never appear in any report line
    (root / ".env").write_text("GEMINI_API_KEY=supersecretvalue12345\n",
                               encoding="utf-8")
    # duplicate pair above 1 MB + one unique larger file
    blob = b"z" * (1024 * 1024 + 512)
    (root / "sample").mkdir()
    (root / "sample" / "a.bin").write_bytes(blob)
    (root / "sample" / "unique.bin").write_bytes(b"q" * (1024 * 1024 + 100))
    (root / "models").mkdir()
    (root / "models" / "a.bin").write_bytes(blob)  # duplicate of sample/a.bin
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return root


def _build_root(tmp_path: Path) -> Path:
    br = tmp_path / "proj_build"
    (br / "logs").mkdir(parents=True)
    (br / "logs" / "rerun.txt").write_text("hello\n", encoding="utf-8")
    return br


def test_report_counts_duplicates_and_budget(tmp_path):
    root = _fake_project(tmp_path)
    br = _build_root(tmp_path)
    text = disk_report.build_report(root, budget_mb=1500, build_root=br)
    assert "project top level" in text
    assert "build root top level" in text
    assert "largest 30 files" in text
    # the duplicate pair is detected together, the unique file listed as big
    assert "== duplicates >= 1 MB" in text
    dup_block = text.split("== duplicates >= 1 MB")[1].split("== outside")[0]
    assert dup_block.count("a.bin") == 2  # both copies, one buffering entry
    assert "unique.bin" in text.split("== largest")[1].split("== duplicates")[0]
    # budget line names the four sizes
    assert "Disk budget:" in text
    assert "within budget" in text  # the fake tree is tiny
    # .env is listed but its CONTENT is never printed
    assert "supersecretvalue12345" not in text
    assert ".env" in text


def test_over_budget_verdict(tmp_path):
    root = tmp_path / "proj"
    (root / "x").mkdir(parents=True)
    (root / "x" / "blob.bin").write_bytes(b"k" * (2 * 1024 * 1024))  # 2 MB
    text = disk_report.build_report(root, budget_mb=1, build_root=None)
    assert "OVER budget" in text
    assert "migrate_workspace.py --apply" in text


def test_undocumented_entry_flagged(tmp_path):
    root = _fake_project(tmp_path)
    (root / "mystery").mkdir()
    flags = disk_report.undocumented(disk_report.top_entries(root))
    assert "mystery" in flags
    for known in ("sample", "models", "README.md"):
        assert known not in flags


def test_documented_list_matches_workspace_doc():
    """Every name the report whitelists must be described in docs/WORKSPACE.md."""
    doc = Path(__file__).resolve().parents[1] / "docs" / "WORKSPACE.md"
    text = doc.read_text(encoding="utf-8")
    missing = []
    for entry in disk_report.DOCUMENTED_ENTRIES:
        bare = entry[:-1] if entry.endswith("*") else entry
        if bare not in text:
            missing.append(entry)
    assert missing == []


def test_largest_files(tmp_path):
    root = _fake_project(tmp_path)
    files = disk_report.largest_files([root], limit=5)
    assert Path(files[0][0]).name == "unique.bin" or Path(files[0][0]).name == "a.bin"
    sizes = [s for _, s in files]
    assert sizes == sorted(sizes, reverse=True)


def test_duplicates_skip_env_and_venv(tmp_path):
    root = tmp_path / "proj"
    (root / ".venv" / "lib").mkdir(parents=True)
    blob = bytes(1024 * 1024 + 7)
    (root / ".venv" / "lib" / "dup.bin").write_bytes(blob)
    (root / ".venv" / "lib" / "dup2.bin").write_bytes(blob)
    # same content as above (would be a duplicate without the guard)
    (root / ".env").write_bytes(blob)
    (root / ".env.bak").write_bytes(blob)
    dups = disk_report.find_duplicates([root])
    names = {Path(p).name for _, _, paths in dups for p in paths}
    assert not names  # duplicates under .venv* / .env* are skipped entirely


def test_write_report_uses_build_root_logs(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    br = _build_root(tmp_path)
    monkeypatch.setenv("DUBTOOL_BUILD_ROOT", str(br))
    path = disk_report.write_report(root, budget_mb=1500)
    assert path.is_file() and path.parent == br / "logs"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("DubTool disk report")
    # git facts of task 6.4 appear in the written report too
    assert "git count-objects" in text


def test_cleanup_plans_and_stays_dry_run(tmp_path):
    from dubtool.app.cleanup import plan

    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "sources.txt").write_text("keep\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "a.pyc").write_bytes(b"x")
    (root / "jobs" / "abc123").mkdir(parents=True)
    (root / "jobs" / "abc123" / "state.json").write_text("{}", encoding="utf-8")
    deletions, _reported, _tops = plan(root, jobs_days=14, now=2_000_000_000)
    assert all(i.action == "delete" for i in deletions)
    assert not any(".git" in str(i.path) for i in deletions)
    # dry run: nothing was deleted
    assert (root / "__pycache__" / "a.pyc").exists()
    assert (root / "sources.txt").exists()


def test_cleanup_default_log_in_build_root(monkeypatch, tmp_path):
    from dubtool.app.cleanup import default_log

    br = tmp_path / "proj_build"
    (br / "logs").mkdir(parents=True)
    monkeypatch.setenv("DUBTOOL_BUILD_ROOT", str(br))
    log = default_log()
    assert log == br / "logs" / "cleanup.log"
    project = Path(__file__).resolve().parents[1]
    assert not log.is_relative_to(project)


def test_never_flag_real_generated_env(tmp_path):
    """Entries the docs mark as report-only never become delete rows."""
    from dubtool.app.cleanup import plan

    root = tmp_path / "proj"
    root.mkdir()
    (root / ".venv").mkdir()
    (root / ".venv" / "x.pyd").write_bytes(b"p")
    root.joinpath(".env").write_text("A=1\n", encoding="utf-8")
    deletions, reported, _ = plan(root, now=2_000_000_000)
    assert not deletions
    reported_names = {i.path.name for i in reported}
    assert ".venv" in reported_names and ".env" in reported_names
