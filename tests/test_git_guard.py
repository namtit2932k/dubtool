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
