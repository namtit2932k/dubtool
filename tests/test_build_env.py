"""AGENT_UPDATE_10.md task 2: the lean build environment.

The build venv installs PySide6-Essentials (not the full wheel including
WebEngine), which is valid because src/ imports only Essentials modules.
The import scan in this test re-checks that assumption so a future
import of an Addons module fails the build set loudly.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _pinned_text() -> str:
    """The pinned dependency block of packaging/build_exe.ps1."""
    text = (ROOT / "packaging" / "build_exe.ps1").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'\$Pinned = @\(@"\n(.*?)\n"@\.TrimEnd\(\)', text, re.DOTALL)
    assert m, "the pinned block was not found in packaging/build_exe.ps1"
    return m.group(1)


def test_pinned_requirements_use_the_essentials_wheel():
    pinned = _pinned_text()
    reqs = [ln.strip() for ln in pinned.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    pkg_names = [r.split(">=")[0].split("<")[0].split("=")[0].strip() for r in reqs]
    assert "PySide6-Essentials" in pkg_names, pkg_names
    assert "PySide6" not in pkg_names, (
        "the full PySide6 wheel adds 370+ MB of WebEngine the app never imports"
    )


def _imported_pyside_modules() -> set[str]:
    mods: set[str] = set()
    for folder in ("src", "tests", "scripts", "packaging"):
        for p in (ROOT / folder).rglob("*.py"):
            text = p.read_text(encoding="utf-8", errors="ignore")
            mods.update(re.findall(r"PySide6\.Qt[A-Za-z]+", text))
    return mods


def test_app_imports_only_pyside_essentials_modules():
    # Every Qt* submodule of these four belongs to PySide6-Essentials;
    # Addons-only modules (WebEngine, Charts, Quick3D, ...) must not appear.
    allowed_prefixes = ("QtCore", "QtGui", "QtWidgets", "QtTest")
    imported = _imported_pyside_modules()
    assert imported, "the import scan found no PySide6 modules — broken scan"
    bad = [m for m in imported
           if not m[len("PySide6."):].startswith(allowed_prefixes)]
    assert not bad, f"Addons-only PySide6 modules in use: {sorted(bad)}"


def test_selftest_prints_imported_qt_modules():
    """The self-test lists the Qt modules actually imported (task 2.4)."""
    from dubtool.app import selftest

    line = selftest.qt_modules_line()
    assert line.startswith("qt modules:")
    for mod in ("QtWidgets", "QtCore", "QtGui"):
        assert mod in line
