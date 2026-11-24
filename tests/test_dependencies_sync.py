"""Verify dependency metadata consistency between pyproject.toml and packaging/build_exe.ps1."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pkg_name(spec: str) -> str:
    base = spec.split(";", 1)[0].strip()
    m = re.match(r"([A-Za-z0-9_.-]+)", base)
    return m.group(1).lower().replace("_", "-") if m else ""


def _build_ps1_no_deps() -> list[str]:
    text = (ROOT / "packaging" / "build_exe.ps1").read_text(encoding="utf-8", errors="ignore")
    assert "$Pinned =" not in text, (
        "packaging/build_exe.ps1 must install from packaging/requirements-lock.txt "
        "instead of maintaining a separate $Pinned version-range list"
    )
    assert 'packaging\\requirements-lock.txt' in text
    assert "-r $LockFile" in text
    m_nd = re.search(r"\$NoDepsPinned = @\((.*?)\)", text, re.DOTALL)
    assert m_nd, "could not find $NoDepsPinned in packaging/build_exe.ps1"
    return re.findall(r'"([^"]+)"', m_nd.group(1))


def test_pyproject_and_build_script_dependencies_stay_in_sync():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    proj = data["project"]
    opt = proj.get("optional-dependencies", {})

    core = {_pkg_name(s) for s in proj.get("dependencies", [])}
    gui = {_pkg_name(s) for s in opt.get("gui", [])}
    dev = {_pkg_name(s) for s in opt.get("dev", [])}
    tts = {_pkg_name(s) for s in opt.get("tts", [])}

    # GUI and dev extras must declare the packages required by README source install.
    assert {"pyside6-essentials", "keyring"} <= gui
    assert {"pytest", "pytest-qt", "pytest-timeout"} <= dev
    assert "vieneu" in tts
    assert "vieneu" not in core, "vieneu must stay out of core deps so pip install -e . does not pull gradio/librosa"

    no_deps_specs = _build_ps1_no_deps()
    assert no_deps_specs, "$NoDepsPinned must not be empty"
    for spec in no_deps_specs:
        assert "==" in spec, f"build_exe.ps1 --no-deps entry must pin an exact version: {spec}"
    no_deps = {_pkg_name(s) for s in no_deps_specs}
    # Every --no-deps package in build_exe.ps1 must appear in pyproject.toml [tts].
    assert no_deps <= tts, f"Missing from [tts] extra: {sorted(no_deps - tts)}"

    # CI and release build script must install the exact same pinned vieneu version.
    wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for spec in no_deps_specs:
        assert f"--no-deps {spec}" in wf, (
            f"CI workflow does not match build_exe.ps1 --no-deps pin '{spec}'"
        )


def test_requirements_lockfile_pins_all_direct_dependencies():
    lock_path = ROOT / "packaging" / "requirements-lock.txt"
    assert lock_path.is_file(), "packaging/requirements-lock.txt missing"
    lines = [
        ln.strip()
        for ln in lock_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert lines, "requirements-lock.txt must not be empty"
    locked: set[str] = set()
    for ln in lines:
        spec = ln.split(";", 1)[0].strip()
        assert "==" in spec, f"Unpinned entry in requirements-lock.txt: {ln}"
        locked.add(_pkg_name(spec))

    # vieneu must stay out of requirements-lock.txt (installed with --no-deps).
    assert "vieneu" not in locked

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    proj = data["project"]
    opt = proj.get("optional-dependencies", {})
    expected = (
        {_pkg_name(s) for s in proj.get("dependencies", [])}
        | {_pkg_name(s) for s in opt.get("gui", [])}
        | {_pkg_name(s) for s in opt.get("dev", [])}
        | {"pyinstaller"}
    )
    missing = expected - locked
    assert not missing, f"Direct build/runtime packages missing from requirements-lock.txt: {sorted(missing)}"


def test_i18n_package_vi_en_key_parity():
    from dubtool.app import i18n
    from dubtool.app.i18n.en import EN_STRINGS
    from dubtool.app.i18n.vi import VI_STRINGS

    assert set(VI_STRINGS) == set(EN_STRINGS)
    assert i18n.STRINGS["vi"] is VI_STRINGS
    assert i18n.STRINGS["en"] is EN_STRINGS
    assert len(VI_STRINGS) >= 250
    assert i18n.tr("app.title")


def test_github_ci_workflow_exists_and_uses_source_install():
    wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "windows-latest" in wf
    assert '"3.11"' in wf and '"3.13"' in wf
    assert "requirements-lock.txt" in wf
    assert '.[gui,dev]' in wf
    assert "--no-deps" in wf and "vieneu" in wf
    assert "preflight_gates.py" in wf
    assert "check_readme_links.py" in wf
    assert "PyInstaller" in wf
    assert "--gui-smoke" in wf
    assert "--screenshots" in wf
    assert "-m packaged" in wf
    assert "upload-artifact" in wf


def test_packaged_font_provenance_pinned_in_ci_and_build_script():
    expected_sha = "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d"
    third_party = (ROOT / "packaging" / "THIRD_PARTY.md").read_text(encoding="utf-8")
    build_ps1 = (ROOT / "packaging" / "build_exe.ps1").read_text(encoding="utf-8")
    wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert expected_sha in third_party
    assert expected_sha in build_ps1
    assert expected_sha in wf
    assert "segoeui.ttf" not in wf and "arial.ttf" not in wf, (
        "CI package job must bundle the authentic pinned NotoSans.ttf, not a system font substitute"
    )


def test_declared_pytest_markers_are_wired():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        m.split(":", 1)[0].strip()
        for m in data["tool"]["pytest"]["ini_options"]["markers"]
    }
    assert declared == {"unit", "gui", "integration", "packaged"}
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    shots = (ROOT / "tests" / "test_screenshots.py").read_text(encoding="utf-8")
    assert "pytest_collection_modifyitems" in conftest
    for marker in ("unit", "gui", "integration", "packaged"):
        assert f'"{marker}"' in conftest or f".{marker}" in conftest
    assert "pytestmark = pytest.mark.packaged" in shots



