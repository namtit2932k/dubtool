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
    assert "git archive" in wf
    assert "build_exe.ps1" in wf
    assert "DUBTOOL_GIT_HEAD" in wf
    assert "python -m PyInstaller" not in wf, (
        "CI package job must invoke packaging/build_exe.ps1 rather than running PyInstaller manually"
    )
    assert not any(
        "release.py" in ln for ln in wf.splitlines() if not ln.lstrip().startswith("#")
    ), "CI workflow must never invoke scripts/release.py"
    assert "SHA256SUMS.txt" in wf and "BUILD_INFO.txt" in wf
    assert "testzip()" in wf and "extractall" in wf
    assert "--gui-smoke" in wf
    assert "upload-artifact" in wf
    assert "schedule:" in wf
    assert "--junitxml" in wf
    assert "pip freeze" in wf and "continue-on-error: true" in wf

    build_ps1 = (ROOT / "packaging" / "build_exe.ps1").read_text(encoding="utf-8")
    m_pip = re.search(r'\$PipVersion\s*=\s*"([^"]+)"', build_ps1)
    assert m_pip, "packaging/build_exe.ps1 must pin $PipVersion"
    assert f'"pip=={m_pip.group(1)}"' in wf, (
        f"CI Python 3.13 locked job must pin the same pip=={m_pip.group(1)} as build_exe.ps1"
    )


def test_build_script_validates_python_313_x64_and_writes_provenance():
    build_ps1 = (ROOT / "packaging" / "build_exe.ps1").read_text(encoding="utf-8")
    assert "Resolve-BuildPython313x64" in build_ps1
    assert "sys.version_info[:2] == (3, 13)" in build_ps1
    assert "struct.calcsize('P') * 8" in build_ps1
    for field in (
        "python:",
        "pip:",
        "lockfile_sha256:",
        "vendor_manifest_sha256:",
        "ffmpeg_version:",
        "ffmpeg_sha256:",
    ):
        assert field in build_ps1, f"BUILD_INFO.txt provenance field '{field}' missing from build_exe.ps1"


def test_vendor_manifest_schema_and_invariants_offline():
    """Pure offline schema validation for packaging/vendor_manifest.json (no network)."""
    import json
    from pathlib import PurePosixPath

    hex64 = re.compile(r"^[0-9a-f]{64}$")
    manifest_path = ROOT / "packaging" / "vendor_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("schema") == 1
    assets = manifest.get("assets")
    assert isinstance(assets, dict) and assets

    seen_dests: set[str] = set()
    for name, spec in assets.items():
        assert isinstance(name, str) and name.strip()
        assert "ffprobe" not in name.lower(), f"ffprobe must not be in bundled assets: {name}"
        for req_key in ("version", "license", "url", "dest", "sha256"):
            val = spec.get(req_key)
            assert isinstance(val, str) and val.strip(), f"asset {name} missing non-empty '{req_key}'"
        assert spec["url"].startswith("https://"), f"asset {name} URL must use HTTPS"
        assert hex64.match(spec["sha256"]), f"asset {name} sha256 must be 64 lowercase hex chars"
        assert isinstance(spec.get("size"), int) and spec["size"] > 0

        dest = spec["dest"]
        assert not dest.startswith(("/", "\\")) and ":" not in dest, f"asset {name} dest must be relative: {dest}"
        parts = PurePosixPath(dest.replace("\\", "/")).parts
        assert parts and ".." not in parts, f"asset {name} dest escapes vendor root: {dest}"
        norm_dest = "/".join(parts).lower()
        assert "ffprobe" not in norm_dest, f"ffprobe must not be a bundled asset dest: {dest}"
        assert norm_dest not in seen_dests, f"duplicate asset dest: {dest}"
        seen_dests.add(norm_dest)

        if "archive_member" in spec or "zip_sha256" in spec:
            assert isinstance(spec.get("archive_member"), str) and spec["archive_member"].strip()
            assert isinstance(spec.get("zip_sha256"), str) and hex64.match(spec["zip_sha256"])

    sidecars = manifest.get("test_sidecars", {})
    assert isinstance(sidecars, dict)
    for sc_name, sc in sidecars.items():
        assert sc_name not in assets, f"sidecar {sc_name} must stay outside bundled assets"
        parent = sc.get("from_asset")
        assert parent in assets, f"sidecar {sc_name} references unknown asset {parent!r}"
        assert "archive_member" in assets[parent], f"sidecar {sc_name} parent {parent!r} must be an archive"
        assert isinstance(sc.get("archive_member"), str) and sc["archive_member"].strip()
        assert isinstance(sc.get("sha256"), str) and hex64.match(sc["sha256"])
        assert isinstance(sc.get("size"), int) and sc["size"] > 0
        sc_dest = sc.get("dest", "")
        assert isinstance(sc_dest, str) and sc_dest.strip()
        assert not sc_dest.startswith(("/", "\\")) and ":" not in sc_dest
        assert ".." not in PurePosixPath(sc_dest.replace("\\", "/")).parts


def test_selftest_provider_offline_smoke_checks_pass():
    from dubtool.app import selftest

    for key in ("gemini_client", "openai_client", "anthropic_client", "groq_auto_rotate"):
        assert key in selftest.CHECKS, f"missing provider check {key} in selftest.CHECKS"
        res = selftest.CHECKS[key]()
        assert res["ok"], f"selftest {key} failed: {res}"


def test_packaged_vendor_assets_provenance_pinned_in_ci_and_build_script():
    import json

    manifest_path = ROOT / "packaging" / "vendor_manifest.json"
    assert manifest_path.is_file(), "packaging/vendor_manifest.json missing"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = manifest["assets"]
    assert {"ffmpeg", "noto_sans_ttf", "noto_sans_ofl"} <= set(assets)

    third_party = (ROOT / "packaging" / "THIRD_PARTY.md").read_text(encoding="utf-8")
    notices = (ROOT / "packaging" / "licenses" / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    build_ps1 = (ROOT / "packaging" / "build_exe.ps1").read_text(encoding="utf-8")
    wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for key in ("ffmpeg", "noto_sans_ttf"):
        sha = assets[key]["sha256"].lower()
        assert len(sha) == 64
        assert sha in third_party, f"{key} sha256 missing from packaging/THIRD_PARTY.md"
        assert sha in notices, f"{key} sha256 missing from packaging/licenses/THIRD_PARTY_NOTICES.txt"

    assert "vendor_manifest.json" in build_ps1
    assert "scripts/fetch_vendor.py" in wf
    assert "choco install ffmpeg" not in wf, (
        "CI must fetch the pinned SHA-256 verified FFmpeg via scripts/fetch_vendor.py, not floating choco install"
    )
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



