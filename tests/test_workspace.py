"""AGENT_UPDATE_10.md task 1: the build root + the migration helper.

Every test uses temporary directories OUTSIDE the real project; the real
DUBTOOL_BUILD_ROOT value is never written to disk by these tests.
"""

from __future__ import annotations

import os
import shutil
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from dubtool import workspace


@pytest.fixture()
def build_env(tmp_path, monkeypatch):
    """A temp fake project + its build root, wired through the env var."""
    fake_project = tmp_path / "Project_K"
    fake_project.mkdir()
    fake_project_src = fake_project / "src" / "dubtool"
    fake_project_src.mkdir(parents=True)
    (fake_project_src / "workspace.py").write_text(
        "# fake module layout so imports resolve\n", encoding="utf-8"
    )
    build_root = tmp_path / "Project_K_build"
    monkeypatch.setenv(workspace.ENV_VAR, str(build_root))
    monkeypatch.setattr(workspace, "project_root", lambda: fake_project)
    return fake_project, build_root


# ------------------------------------------------------------------ paths
def test_default_build_root_is_a_sibling_of_the_project(tmp_path, monkeypatch):
    fake = tmp_path / "MyProj"
    (fake / "src" / "dubtool").mkdir(parents=True)
    (fake / "src" / "dubtool" / "workspace.py").write_text("", encoding="utf-8")
    monkeypatch.delenv(workspace.ENV_VAR, raising=False)
    monkeypatch.setattr(workspace, "project_root", lambda: fake)
    br = workspace.build_root(create=False)
    assert br == tmp_path / "MyProj_build"
    assert br.parent != fake


def test_build_root_env_override(tmp_path, monkeypatch):
    override = tmp_path / "elsewhere" / "build"
    monkeypatch.setenv(workspace.ENV_VAR, str(override))
    monkeypatch.setattr(workspace, "project_root", lambda: tmp_path / "p")
    assert workspace.build_root() == override
    assert override.is_dir()


def test_build_root_inside_the_project_is_refused(tmp_path, monkeypatch):
    fake = tmp_path / "proj"
    fake.mkdir()
    monkeypatch.setattr(workspace, "project_root", lambda: fake)
    monkeypatch.setenv(workspace.ENV_VAR, str(fake / "build_here"))
    with pytest.raises(RuntimeError, match="OUTSIDE the project"):
        workspace.build_root()
    # equal to the project itself is also refused
    monkeypatch.setenv(workspace.ENV_VAR, str(fake))
    with pytest.raises(RuntimeError, match="OUTSIDE the project"):
        workspace.build_root()


def test_subdirs_are_created_on_demand(build_env):
    _proj, br = build_env
    assert workspace.logs_dir() == br / "logs" and (br / "logs").is_dir()
    assert workspace.tmp_dir() == br / "tmp"
    assert workspace.jobs_dir() == br / "jobs"
    assert workspace.hf_cache_dir() == br / "hf-cache"
    assert workspace.build_dir().name == "build"
    assert workspace.dist_dir().name == "dist"
    assert workspace.venv_dir().name == "venv-build"


def test_sanitized_tmp_subdir(build_env):
    _proj, br = build_env
    d = workspace.sanitized_tmp_dir("run-42")
    assert d == br / "tmp" / "sanitized" / "run-42" and d.is_dir()


# -------------------------------------------------------------- migration
def _fake_project_with_copy_of_helper(tmp_path: Path) -> Path:
    """A fake project tree with scripts/migrate_workspace.py copied into
    it, so running its CLI by path resolves ROOT (its parents[1]) to the
    FAKE project — nothing in the real project is ever touched."""
    fake = tmp_path / "FakeK"
    (fake / "scripts").mkdir(parents=True)
    (fake / "src" / "dubtool").mkdir(parents=True)  # sys.path filler as in the real script
    shutil.copy2(ROOT / "scripts" / "migrate_workspace.py",
                 fake / "scripts" / "migrate_workspace.py")
    shutil.copy2(ROOT / "src" / "dubtool" / "workspace.py",
                 fake / "src" / "dubtool" / "workspace.py")
    (fake / "src" / "dubtool" / "__init__.py").write_text("", encoding="utf-8")
    return fake


def _migrate(args, cwd, project=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    script = Path(cwd) / "scripts" / "migrate_workspace.py"
    if project is not None:
        script = Path(project) / "scripts" / "migrate_workspace.py"
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, cwd=str(cwd), env=env,
    )


def test_migration_dry_run_lists_every_item_and_changes_nothing(tmp_path, build_env):
    """AGU10 2.4: the dry run lists every generated item of the fake
    project, states the destination, and changes nothing."""
    _proj, br = build_env  # sets DUBTOOL_BUILD_ROOT through the env var
    fake = _fake_project_with_copy_of_helper(tmp_path)
    (fake / "dist").mkdir()
    (fake / "dist" / "stub.txt").write_text("x", encoding="utf-8")
    (fake / "jobs").mkdir()
    (fake / "jobs" / "stubjob").mkdir()
    (fake / ".venv-build").mkdir()
    (fake / "build_log_10test.txt").write_text("log line", encoding="utf-8")
    r = _migrate([], cwd=fake)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for needle in ("dist", "jobs", "build_log_10test.txt", ".venv-build",
                   "recreate", "DRY RUN"):
        assert needle in out, needle
    # nothing moved, nothing deleted
    assert (fake / "dist" / "stub.txt").exists()
    assert (fake / "jobs" / "stubjob").exists()
    assert not (br / "dist").exists()
    assert not (br / "jobs").exists()



def test_migration_apply_on_a_fake_tree_moves_and_refuses_rewrites(
    tmp_path, monkeypatch, build_env
):
    """Run the helper's apply function directly against a fake project."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "migrate", ROOT / "scripts" / "migrate_workspace.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # re-point the script constants at the fake tree
    fake_project, br = build_env
    monkeypatch.setattr(mod, "ROOT", fake_project)
    monkeypatch.setenv(workspace.ENV_VAR, str(br))
    # create the generated items in the fake project
    (fake_project / "dist").mkdir()
    (fake_project / "dist" / "a.bin").write_bytes(b"x" * 10)
    (fake_project / "jobs").mkdir()
    (fake_project / "jobs" / "j1").mkdir()
    (fake_project / "build_log_test.txt").write_text("x", encoding="utf-8")

    items = mod.plan(verbose=False)
    kinds = {p.name: kind for p, kind, _d, _s in items}
    assert kinds["dist"] == "move"
    assert kinds["build_log_test.txt"] == "log move"
    assert mod.apply_moves(mod.plan(verbose=False)) >= 3
    assert (br / "dist" / "a.bin").exists()
    assert (br / "jobs" / "j1").exists()
    assert (br / "logs" / "build_log_test.txt").exists()
    assert not (fake_project / "dist").exists()

    # skips a colliding item, never overwrites, and keeps processing
    (fake_project / "dist").mkdir()
    (fake_project / "dist" / "b.bin").write_bytes(b"y")
    (br / "dist" / "newer.bin").write_bytes(b"z")  # build root owns a newer copy
    items2 = mod.plan(verbose=False)
    assert mod.apply_moves(items2) == 0
    assert (fake_project / "dist" / "b.bin").exists()      # skipped, not moved
    assert (br / "dist" / "newer.bin").read_bytes() == b"z"  # nothing overwritten


def test_migration_apply_refuses_outside_paths(tmp_path, monkeypatch, build_env):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "migrate2", ROOT / "scripts" / "migrate_workspace.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake_project, br = build_env
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    item = (outside, "move", f"<build root>/outside.txt", 1)
    with pytest.raises(RuntimeError, match="outside the project and the build root"):
        mod.apply_moves([item])


# ----------------------------------------------------------------- static
def test_no_script_hardcodes_generated_folders_relative_to_cwd():
    """Static check (AGU10 2.4): scripts/ and app code must build
    generated-folder paths through workspace, not relative to the cwd."""
    import re

    patterns = [
        re.compile(r"Path\(\s*['\"](?:dist|jobs|models|vendor|\.venv-build|screenshots)['\"]"),
        re.compile(r"(?:root|cwd|PROJECT_ROOT|ROOT)\s*/\s*['\"](?:dist|\.venv-build|screenshots)['\"]"),
        re.compile(r"Join-Path\s+\$root\s+['\"](?:dist|\.venv-build['\"])"),
    ]
    offenders = []
    scan_files = list((ROOT / "scripts").glob("*.py"))
    scan_files += [ROOT / "packaging" / "build_exe.ps1"]
    for f in scan_files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), start=1):
            if any(p.search(line) for p in patterns):
                offenders.append(f"{f.name}:{i}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


# ----------------------------------------------------------------- AGU10 3
def test_prepare_sanitized_home_build_root_and_shared_caches(build_env, monkeypatch, tmp_path):
    """AGU10 task 3.1/3.2: the sanitized home lives under tmp/sanitized,
    the shared caches are set BEFORE APPDATA moves, and %TEMP% gets no
    new dubtool-* folder (the old smoke-media leftovers)."""
    from dubtool.app import main as app_main

    fake_project, br = build_env
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    before = set(p.name for p in temp_dir.glob("dubtool-*"))
    monkeypatch.setenv("TEMP", str(temp_dir))
    monkeypatch.setattr("tempfile.tempdir", str(temp_dir))
    for var in ("HF_HOME", "HF_HUB_CACHE", "DUBTOOL_MODELS_DIR"):
        monkeypatch.delenv(var, raising=False)

    home = app_main._prepare_sanitized_home("t3test")
    try:
        home_rel = home.relative_to(br)
        assert str(home_rel).startswith("tmp\\sanitized\\t3test-"), home_rel
        assert os.environ["APPDATA"] == str(home)
        assert os.environ["LOCALAPPDATA"] == str(home)
        # shared caches (task 3.1): set before the home override, under
        # the build root — not inside the sanitized home
        assert Path(os.environ["HF_HOME"]) == br / "hf-cache"
        assert Path(os.environ["HF_HUB_CACHE"]) == br / "hf-cache" / "hub"
        assert Path(os.environ["DUBTOOL_MODELS_DIR"]) == br / "models"
        after = set(p.name for p in temp_dir.glob("dubtool-*"))
        assert after == before, "a dubtool-* temp entry appeared"
    finally:
        for var in ("APPDATA", "LOCALAPPDATA", "HF_HOME", "HF_HUB_CACHE", "DUBTOOL_MODELS_DIR"):
            monkeypatch.delenv(var, raising=False)


def test_gui_smoke_media_inside_sanitized_home():
    """After --gui-smoke, %TEMP% has no new dubtool-* entries (AGU10 3.3):
    the smoke media folder is not named with a tempfile prefix but lives
    inside the sanitized home."""
    text = (ROOT / "src" / "dubtool" / "app" / "main.py").read_text(encoding="utf-8")
    assert 'mkdtemp(prefix="dubtool-smoke-media-")' not in text
    assert 'mkdtemp(prefix="dubtool-gui-smoke-appdata-")' not in text
    assert 'smoke_dir = Path(os.environ["LOCALAPPDATA"]) / "media"' in text


def test_safe_rmtree_refuses_protected_and_non_sanitized_paths(build_env, tmp_path, monkeypatch):
    """Critical regression guard: safe_rmtree_sanitized and job pruning must
    never delete real APPDATA/LOCALAPPDATA, USERPROFILE, %TEMP%, drive roots,
    project_root, build_root, or unisolated media paths."""
    from dubtool import job as job_mod

    proj, br = build_env
    fake_profile = tmp_path / "Users" / "Tester"
    fake_local = fake_profile / "AppData" / "Local"
    fake_roaming = fake_profile / "AppData" / "Roaming"
    fake_chrome_sentinel = fake_local / "Google" / "Chrome" / "User Data" / "Default" / "Preferences"
    fake_chrome_sentinel.parent.mkdir(parents=True)
    fake_chrome_sentinel.write_text("keep", encoding="utf-8")
    fake_roaming.mkdir(parents=True)
    fake_temp = fake_local / "Temp"
    fake_temp.mkdir(parents=True)
    (fake_local / "media").mkdir(parents=True)

    monkeypatch.setenv("USERPROFILE", str(fake_profile))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    monkeypatch.setenv("APPDATA", str(fake_roaming))
    monkeypatch.setenv("TEMP", str(fake_temp))

    for dangerous in (
        Path(fake_profile.anchor),
        fake_profile.parent,
        fake_profile,
        fake_profile / "AppData",
        fake_local,
        fake_roaming,
        fake_temp,
        fake_local / "media",
        proj,
        br,
        br / "tmp",
        br / "tmp" / "sanitized",
        Path.home() / "AppData" / "Local",
    ):
        with pytest.raises(RuntimeError):
            workspace.safe_rmtree_sanitized(dangerous, allow_media_child=True, ignore_errors=True)

    # Prune helpers must also refuse protected roots
    for dangerous_job in (fake_local, fake_roaming, fake_temp, fake_profile):
        with pytest.raises(RuntimeError):
            job_mod.prune_none(dangerous_job)
        with pytest.raises(RuntimeError):
            job_mod.prune_light(dangerous_job)

    assert fake_chrome_sentinel.read_text(encoding="utf-8") == "keep"

    # Legitimate sanitized home and its media subdir are allowed
    san = workspace.sanitized_tmp_dir("safe-case-01")
    media = san / "media"
    media.mkdir(parents=True)
    (media / "clip.mp4").write_bytes(b"ok")
    workspace.safe_rmtree_sanitized(media, allow_media_child=True)
    assert not media.exists() and san.is_dir()
    workspace.safe_rmtree_sanitized(san)
    assert not san.exists()


def test_direct_run_gui_smoke_auto_isolates_before_touching_appdata(build_env, tmp_path, monkeypatch):
    """Calling run_gui_smoke() directly without --gui-smoke must automatically
    isolate APPDATA/LOCALAPPDATA into build_root/tmp/sanitized before executing
    the smoke body, leaving real AppData untouched and restoring env on exit."""
    from dubtool.app import main as app_main

    _proj, br = build_env
    fake_local = tmp_path / "RealUsers" / "Admin" / "AppData" / "Local"
    fake_roaming = tmp_path / "RealUsers" / "Admin" / "AppData" / "Roaming"
    fake_local.mkdir(parents=True)
    fake_roaming.mkdir(parents=True)
    sentinel = fake_local / "important_app.exe"
    sentinel.write_bytes(b"exe")

    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    monkeypatch.setenv("APPDATA", str(fake_roaming))

    observed: dict[str, str] = {}

    def fake_body(ws):
        observed["local"] = os.environ["LOCALAPPDATA"]
        observed["appdata"] = os.environ["APPDATA"]
        assert ws.is_sanitized_subpath(Path(os.environ["LOCALAPPDATA"]))
        return 0

    monkeypatch.setattr(app_main, "_run_gui_smoke_body", fake_body)
    rc = app_main.run_gui_smoke()
    assert rc == 0
    assert sentinel.read_bytes() == b"exe"
    assert os.environ["LOCALAPPDATA"] == str(fake_local)
    assert os.environ["APPDATA"] == str(fake_roaming)
    assert Path(observed["local"]).parent == br / "tmp" / "sanitized"
    assert not Path(observed["local"]).exists()


def test_no_raw_env_rmtree_in_src():
    """Static check: source modules must never call shutil.rmtree directly on an
    os.environ[...] path."""
    import re

    src_dir = ROOT / "src"
    bad_pat = re.compile(r"shutil\.rmtree\(\s*Path\(\s*os\.environ")
    hits = []
    for p in src_dir.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if bad_pat.search(text):
            hits.append(str(p.relative_to(ROOT)))
    assert not hits, f"Raw env rmtree found in: {hits}"
