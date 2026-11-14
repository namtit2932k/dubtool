"""AGU10 task 6.6 - the pre-commit guard, exercised in a temporary repo.

Each test stages files in a fresh `git init` repository and runs
scripts/git_guard.py as a subprocess, exactly like the installed hook does.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dubtool import proc

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "git_guard.py"
HOOKS = ROOT / "scripts" / "install_git_hooks.py"


def _base_env(extra: dict[str, str] | None = None) -> dict:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.pop("ALLOW_BIG_FILE", None)
    env.update(extra or {})
    return env


def _run_guard(repo: Path, extra_env: dict[str, str] | None = None):
    done = proc.run(
        [sys.executable, str(GUARD)],
        cwd=str(repo),
        env=_base_env(extra_env),
    )
    text = (done.stdout or "") + (done.stderr or "")
    return done.returncode, text


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        assert proc.run(cmd, cwd=str(repo)).returncode == 0, cmd
    (repo / "src").mkdir()
    (repo / "src" / "keep.txt").write_text("normal source\n", encoding="utf-8")
    proc.run(["git", "add", "src/keep.txt"], cwd=str(repo))
    return repo


def _stage(repo: Path, rel: str, data: bytes) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    assert proc.run(["git", "add", "--", rel], cwd=str(repo)).returncode == 0


def test_normal_source_file_passes(tmp_path):
    repo = _make_repo(tmp_path)
    rc, text = _run_guard(repo)
    assert rc == 0, text
    assert "1 staged file(s) checked" in text


def test_big_file_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "blob.bin", b"x" * (6 * 1024 * 1024))
    rc, text = _run_guard(repo)
    assert rc != 0
    assert "blob.bin" in text and "6.00 MB" in text
    # message names the bypass variable and the fix
    assert "ALLOW_BIG_FILE" in text and "git rm --cached" in text


def test_forbidden_prefix_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, ".freebuff/cache/state.bin", b"junk")
    rc, text = _run_guard(repo)
    assert rc != 0
    assert "forbidden path prefix" in text


def test_forbidden_extension_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "sample/build/out.dll", b"junk")
    rc, text = _run_guard(repo)
    assert rc != 0
    assert ".dll" in text


def test_bypass_variable_allows_and_logs(tmp_path):
    repo = _make_repo(tmp_path)
    _stage(repo, "blob.bin", b"x" * (6 * 1024 * 1024))
    rc, text = _run_guard(repo, {"ALLOW_BIG_FILE": "1"})
    assert rc == 0, text
    assert "bypassed (ALLOW_BIG_FILE=1)" in text


def test_fake_key_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "src" / "keep.txt").write_text(
        # key-shaped FAKE value below — fixture-fake-key, not a real key
        "key = sk-ant-FAKE-13f4bea2c9d7e851  # fixture-fake-key\n",
        encoding="utf-8",
    )
    proc.run(["git", "add", "src/keep.txt"], cwd=str(repo))
    rc, text = _run_guard(repo)
    assert rc != 0
    assert "anthropic-key" in text and "never commit keys" in text


def test_install_hook_and_shim_invokes_guard(tmp_path):
    from dubtool.app import settings as _  # noqa: F401  (dubtool import check)

    import importlib.util

    spec = importlib.util.spec_from_file_location("igh", HOOKS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.install(tmp_path) == "installed"
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    assert "git_guard.py" in hook.read_text(encoding="utf-8")
    # idempotent refresh, and a foreign hook is left alone
    assert mod.install(tmp_path) == "refreshed"
    hook.write_text("#!/bin/sh\necho other\n", encoding="utf-8")
    assert mod.install(tmp_path) == "exists-foreign-hook-left-alone"


def _make_repo_with_bare_remote(tmp_path: Path) -> tuple[Path, Path]:
    import hashlib
    import importlib.util
    import shutil

    bare = tmp_path / "remote.git"
    bare.mkdir()
    assert proc.run(["git", "init", "--bare", "-q"], cwd=str(bare)).returncode == 0

    repo = tmp_path / "work"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "t"],
        ["git", "remote", "add", "origin", str(bare)],
    ):
        assert proc.run(cmd, cwd=str(repo)).returncode == 0, cmd

    # Copy tracked guard scripts and dubtool.proc into the temp repo so the hook runs self-contained
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    for sname in ("git_guard.py", "git_push_guard.py", "preflight_gates.py", "install_git_hooks.py"):
        shutil.copyfile(ROOT / "scripts" / sname, repo / "scripts" / sname)
    (repo / "src" / "dubtool").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "src" / "dubtool" / "__init__.py", repo / "src" / "dubtool" / "__init__.py")
    shutil.copyfile(ROOT / "src" / "dubtool" / "proc.py", repo / "src" / "dubtool" / "proc.py")
    (repo / "packaging").mkdir(parents=True, exist_ok=True)
    (repo / "packaging" / "update_number.txt").write_text("15\n", encoding="utf-8")
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "note.md").write_text("initial docs\n", encoding="utf-8")

    assert proc.run(["git", "add", "."], cwd=str(repo)).returncode == 0
    assert proc.run(["git", "commit", "-q", "-m", "initial"], cwd=str(repo)).returncode == 0
    # Push initial commit before installing pre-push hook so origin/main exists
    assert proc.run(["git", "push", "-q", "-u", "origin", "main"], cwd=str(repo)).returncode == 0

    spec = importlib.util.spec_from_file_location("igh", repo / "scripts" / "install_git_hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.install_pre_push(repo) == "installed"
    return repo, bare


def _push_env() -> dict[str, str]:
    return _base_env({"DUBTOOL_HOOK_PYTHON": sys.executable.replace("\\", "/")})


def _write_stamp(repo: Path, update_num: int, head_sha: str, review_zip: Path, zip_sha: str | None = None) -> None:
    import hashlib
    import json

    actual_sha = hashlib.sha256(review_zip.read_bytes()).hexdigest().lower()
    stamp = {
        "update": update_num,
        "version": f"1.0.{update_num}",
        "head": head_sha,
        "zip_path": "dist/DubTool.zip",
        "zip_sha256": "a" * 64,
        "review_zip_path": str(review_zip),
        "review_zip_sha256": zip_sha if zip_sha is not None else actual_sha,
        "timestamp": "2026-11-14T00:00:00Z",
    }
    (repo / ".git" / f"dubtool-release-ok-{update_num}").write_text(
        json.dumps(stamp), encoding="utf-8"
    )


def test_pre_push_ordinary_docs_push_passes(tmp_path):
    repo, _bare = _make_repo_with_bare_remote(tmp_path)
    (repo / "docs" / "note.md").write_text("updated docs\n", encoding="utf-8")
    assert proc.run(["git", "add", "docs/note.md"], cwd=str(repo)).returncode == 0
    assert proc.run(["git", "commit", "-q", "-m", "docs update"], cwd=str(repo)).returncode == 0
    res = proc.run(["git", "push", "origin", "main"], cwd=str(repo), env=_push_env())
    assert res.returncode == 0, (res.stdout or "") + (res.stderr or "")


def test_pre_push_tag_without_stamp_blocked_and_with_valid_stamp_passes(tmp_path):
    repo, _bare = _make_repo_with_bare_remote(tmp_path)
    assert proc.run(["git", "tag", "-a", "v1.0.15", "-m", "v1.0.15"], cwd=str(repo)).returncode == 0

    # 1. Without stamp -> blocked
    res = proc.run(["git", "push", "origin", "v1.0.15"], cwd=str(repo), env=_push_env())
    out = (res.stdout or "") + (res.stderr or "")
    assert res.returncode != 0
    assert "missing release stamp" in out

    # 2. With stale stamp (wrong HEAD) -> blocked
    review_zip = tmp_path / "review.zip"
    review_zip.write_bytes(b"PK\x03\x04dummy-review-zip")
    _write_stamp(repo, 15, "0" * 40, review_zip)
    res2 = proc.run(["git", "push", "origin", "v1.0.15"], cwd=str(repo), env=_push_env())
    out2 = (res2.stdout or "") + (res2.stderr or "")
    assert res2.returncode != 0
    assert "stale release stamp" in out2

    # 3. With valid HEAD but tampered review zip -> blocked
    head_sha = proc.run(["git", "rev-parse", "HEAD"], cwd=str(repo)).stdout.strip()
    _write_stamp(repo, 15, head_sha, review_zip, zip_sha="f" * 64)
    res3 = proc.run(["git", "push", "origin", "v1.0.15"], cwd=str(repo), env=_push_env())
    out3 = (res3.stdout or "") + (res3.stderr or "")
    assert res3.returncode != 0
    assert "SHA-256 mismatch" in out3

    # 4. With valid stamp -> passes
    _write_stamp(repo, 15, head_sha, review_zip)
    res4 = proc.run(["git", "push", "origin", "v1.0.15"], cwd=str(repo), env=_push_env())
    assert res4.returncode == 0, (res4.stdout or "") + (res4.stderr or "")


def test_pre_push_main_version_bump_blocked_without_stamp(tmp_path):
    repo, _bare = _make_repo_with_bare_remote(tmp_path)
    (repo / "packaging" / "update_number.txt").write_text("16\n", encoding="utf-8")
    assert proc.run(["git", "add", "packaging/update_number.txt"], cwd=str(repo)).returncode == 0
    assert proc.run(["git", "commit", "-q", "-m", "bump to 16"], cwd=str(repo)).returncode == 0

    res = proc.run(["git", "push", "origin", "main"], cwd=str(repo), env=_push_env())
    out = (res.stdout or "") + (res.stderr or "")
    assert res.returncode != 0
    assert "packaging/update_number.txt" in out


def test_pre_push_fake_key_blocked_and_never_echoed(tmp_path):
    repo, _bare = _make_repo_with_bare_remote(tmp_path)
    fake_secret = "AIzaSyFakePushTestKey1234567890abcdef"  # fixture-fake-key
    (repo / "docs" / "note.md").write_text(f"token={fake_secret}\n", encoding="utf-8")
    assert proc.run(["git", "add", "--no-verify", "docs/note.md"], cwd=str(repo)).returncode != 0 or True
    # Stage and commit bypassing pre-commit to test pre-push specifically
    assert proc.run(["git", "add", "docs/note.md"], cwd=str(repo)).returncode == 0
    assert proc.run(["git", "commit", "--no-verify", "-q", "-m", "accidental secret"], cwd=str(repo)).returncode == 0

    res = proc.run(["git", "push", "origin", "main"], cwd=str(repo), env=_push_env())
    out = (res.stdout or "") + (res.stderr or "")
    assert res.returncode != 0
    assert "google-key" in out
    assert fake_secret not in out

