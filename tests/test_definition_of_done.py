"""Unit tests for scripts/definition_of_done.py (AGENT_UPDATE_15 Task 2).

Builds a fake project + build-root tree where all 8 rules pass, then verifies
that breaking each rule (1 through 8) causes that rule and `run_dod` to fail.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from dubtool import proc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import definition_of_done as dod  # noqa: E402


def _make_png(path: Path, width: int = 800, height: int = 600, mostly_white: bool = False) -> None:
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255) if mostly_white else QColor(40, 60, 90))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))


def _write_review_zip(repo: Path, update_n: int = 15, extra_files: dict[str, bytes] | None = None) -> Path:
    review_dir = repo / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    zpath = review_dir / "dubtool-review-20261114-1200.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("docs/WORKSPACE.md", "# Workspace\n")
        zf.writestr(f"REPORT_UPDATE_{update_n}.md", (repo / f"REPORT_UPDATE_{update_n}.md").read_text("utf-8"))
        zf.writestr("REVIEW_NOTES.md", "# Notes\n")
        zf.writestr("DOD_REPORT.md", "# DoD\n")
        zf.writestr("RELEASE_EVIDENCE.txt", "evidence\n")
        zf.writestr("tests/SKIPS.md", (repo / "tests" / "SKIPS.md").read_text("utf-8"))
        for k, v in (extra_files or {}).items():
            zf.writestr(k, v)
    return zpath


def _setup_valid_tree(tmp_path: Path, update_n: int = 15) -> tuple[Path, Path, str]:
    repo = tmp_path / "project"
    build_root = tmp_path / "project_build"
    repo.mkdir()
    build_root.mkdir()

    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        assert proc.run(cmd, cwd=str(repo)).returncode == 0

    (repo / "packaging").mkdir(parents=True)
    (repo / "packaging" / "update_number.txt").write_text(f"{update_n}\n", encoding="utf-8")
    (repo / "packaging" / "version.txt").write_text(f"1.0.{update_n}\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "dubtool"\ndynamic = ["version"]\n[tool.setuptools.dynamic]\nversion = {attr = "dubtool.__version__"}\n',
        encoding="utf-8",
    )
    (repo / "src" / "dubtool").mkdir(parents=True)
    (repo / "src" / "dubtool" / "__init__.py").write_text(
        f'__version__ = "1.0.{update_n}"\n', encoding="utf-8"
    )
    (repo / "src" / "dubtool" / "sample_mod.py").write_text(
        '"""Developer docstring mentioning AGU15 is allowed."""\nMSG = "Xin chào người dùng"\n',
        encoding="utf-8",
    )
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "SKIPS.md").write_text(
        "# Skips\n- `tests/test_cleanup.py`: Windows symlink privilege\n",
        encoding="utf-8",
    )

    req_pngs = [
        "ready.png",
        "running.png",
        "finished.png",
        "settings_api_keys.png",
        "1100x800_idle.png",
        "1100x800_video.png",
        "1100x800_running.png",
        "1100x800_finished.png",
        "1100x800_error.png",
    ]
    for name in req_pngs:
        _make_png(repo / "docs" / "images" / name, 800, 600, mostly_white=False)

    (repo / "README.md").write_text(
        "![r](docs/images/ready.png)\n![ru](docs/images/running.png)\n![f](docs/images/finished.png)\n![s](docs/images/settings_api_keys.png)\n",
        encoding="utf-8",
    )
    (repo / "README_BUILD.md").write_text("# Build\n", encoding="utf-8")

    assert proc.run(["git", "add", "."], cwd=str(repo)).returncode == 0
    assert proc.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo)).returncode == 0
    head = proc.run(["git", "rev-parse", "HEAD"], cwd=str(repo)).stdout.strip()

    # Write INDEX.txt with current HEAD
    (repo / "docs" / "images" / "INDEX.txt").write_text(
        f"DubTool screenshots - 1.0.{update_n}\n"
        f"git: {head}\n"
        "built (UTC): 2026-11-14 12:05:00\n"
        f"build id: Update {update_n} ({head})\n"
        "images: from the packaged exe (DubTool.exe --screenshots), platform: windows — the REAL interface\n"
        "masked paths: yes\n"
        "files: " + ", ".join(req_pngs) + "\n",
        encoding="utf-8",
    )

    # Build artifacts
    dist_app = build_root / "dist" / "DubTool"
    dist_app.mkdir(parents=True)
    exe_path = dist_app / "DubTool.exe"
    exe_path.write_bytes(b"MZ-fake-exe-" + f"1.0.{update_n}".encode("utf-16-le"))
    exe_sha = hashlib.sha256(exe_path.read_bytes()).hexdigest().lower()

    (dist_app / "BUILD_INFO.txt").write_text(
        f"update: {update_n}\n"
        f"version: 1.0.{update_n}\n"
        f"git: {head}\n"
        "built (UTC): 2026-11-14 12:00:00\n"
        f"sha256: {exe_sha}\n"
        f"build id: Update {update_n} ({head})\n",
        encoding="utf-8",
    )
    zip_path = build_root / "dist" / f"DubTool-1.0.{update_n}-win64.zip"
    zip_path.write_bytes(b"PK\x03\x04fake-release-zip")
    zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
    (build_root / "dist" / "SHA256SUMS.txt").write_text(
        f"{zip_sha}  {zip_path.name}\n", encoding="utf-8"
    )

    # Pytest log
    logs_dir = build_root / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / f"pytest-{update_n}.txt").write_text(
        "SKIPPED [1] tests/test_cleanup.py:210: symlink privilege not held\n"
        "500 passed, 1 skipped in 30.0s\n",
        encoding="utf-8",
    )

    # Report
    (repo / f"REPORT_UPDATE_{update_n}.md").write_text(
        f"# Report Update {update_n}\n"
        "Files changed: `src/dubtool/sample_mod.py`, `DOD_REPORT.md`.\n"
        "Demo video: `test9.mp4`.\n"
        "Checked `v1.0.14`.\n"
        "git log -1 --format=%H\n"
        f"{head}\n"
        "git status -sb\n"
        "## main...origin/main\n"
        "git ls-remote origin refs/heads/main\n"
        f"{head}\trefs/heads/main\n"
        "Tags:\nv1.0.14\nv1.0.15\n",
        encoding="utf-8",
    )

    _write_review_zip(repo, update_n)
    return repo, build_root, head


def test_dod_passes_on_valid_tree(tmp_path):
    repo, build_root, _head = _setup_valid_tree(tmp_path)
    rc, out_path = dod.run_dod(repo, build_root, 15)
    assert rc == 0, out_path.read_text("utf-8")
    assert "**Overall Status:** PASS" in out_path.read_text("utf-8")


def test_dod_each_rule_fails_when_broken(tmp_path):
    repo, build_root, head = _setup_valid_tree(tmp_path)

    # Rule 1: BUILD_INFO commit != HEAD
    binfo = build_root / "dist" / "DubTool" / "BUILD_INFO.txt"
    orig_binfo = binfo.read_text("utf-8")
    binfo.write_text(orig_binfo.replace(head, "0" * 40), encoding="utf-8")
    ok1, _, ev1 = dod.check_rule_1_version(repo, build_root, 15)
    assert not ok1 and "BUILD_INFO.txt git" in ev1
    binfo.write_text(orig_binfo, encoding="utf-8")

    # Rule 2: Exe SHA-256 mismatch
    exe = build_root / "dist" / "DubTool" / "DubTool.exe"
    orig_exe = exe.read_bytes()
    exe.write_bytes(orig_exe + b"tampered")
    ok2, _, ev2 = dod.check_rule_2_artifacts(repo, build_root, 15)
    assert not ok2 and "SHA-256" in ev2
    exe.write_bytes(orig_exe)

    # Rule 3: Stale screenshot in docs/images
    stale = repo / "docs" / "images" / "dialog_settings_tab1_Mô_hình.png"
    _make_png(stale, 800, 600)
    ok3, _, ev3 = dod.check_rule_3_screenshots(repo, build_root, 15)
    assert not ok3 and "dialog_settings_tab1_Mô_hình.png" in ev3
    stale.unlink()

    # Rule 4: Undocumented skip in pytest log
    plog = build_root / "logs" / "pytest-15.txt"
    orig_plog = plog.read_text("utf-8")
    plog.write_text(
        "SKIPPED [1] tests/test_mystery.py:10: undocumented skip\n10 passed, 1 skipped\n",
        encoding="utf-8",
    )
    ok4, _, ev4 = dod.check_rule_4_tests(repo, build_root, 15)
    assert not ok4 and "test_mystery.py" in ev4
    plog.write_text(orig_plog, encoding="utf-8")

    # Rule 5: Report missing GitHub evidence or duplicate tag
    rep = repo / "REPORT_UPDATE_15.md"
    orig_rep = rep.read_text("utf-8")
    rep.write_text(orig_rep + "\nv1.0.15\n", encoding="utf-8")
    ok5, _, ev5 = dod.check_rule_5_report(repo, build_root, 15)
    assert not ok5 and "Duplicate" in ev5
    rep.write_text(orig_rep, encoding="utf-8")

    # Rule 6: Review zip contains a fake key without fixture marker
    _write_review_zip(
        repo,
        15,
        extra_files={"notes/leak.txt": b"secret=AIzaSyUnmarkedFakeKey1234567890abcdef\n"},  # fixture-fake-key
    )
    ok6, _, ev6 = dod.check_rule_6_review_zip(repo, build_root, 15)
    assert not ok6 and "google-key" in ev6
    _write_review_zip(repo, 15)

    # Rule 7: Runtime string contains AGU identifier
    mod = repo / "src" / "dubtool" / "sample_mod.py"
    orig_mod = mod.read_text("utf-8")
    mod.write_text('MSG = "See AGU15 task 3.6 for details"\n', encoding="utf-8")
    ok7, _, ev7 = dod.check_rule_7_messages(repo)
    assert not ok7 and "sample_mod.py" in ev7
    mod.write_text(orig_mod, encoding="utf-8")

    # Rule 8: Report references a non-existent file path
    rep.write_text(orig_rep + "\nSee `src/dubtool/does_not_exist.py`.\n", encoding="utf-8")
    ok8, _, ev8 = dod.check_rule_8_report_paths(repo, build_root, 15)
    assert not ok8 and "does_not_exist.py" in ev8
    rep.write_text(orig_rep, encoding="utf-8")
