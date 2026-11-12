"""The build root (AGENT_UPDATE_10.md task 1) — every generated artifact.

One folder OUTSIDE the project holds everything the tools create:
venv-build, build/, dist/, vendor/, jobs/, models/, hf-cache/,
sample_output/, screenshots/, inventory/, logs/, tmp/, pip-cache.

Resolution order: the DUBTOOL_BUILD_ROOT environment variable, then the
default ``<project parent>/<project folder name>_build``. Every getter
creates its folder on demand. A build root inside the project folder is
refused with a clear message (that is the exact situation that made the
project folder grow to 4.4 GiB before Update 10).

The installed exe keeps using %LOCALAPPDATA%/DubTool and
%APPDATA%/DubTool (app/paths.py); this module is for source checkouts,
the build script and the developer/agent tooling.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ENV_VAR = "DUBTOOL_BUILD_ROOT"


def project_root() -> Path:
    """The source-checkout project folder (src/dubtool/workspace.py -> root)."""
    return Path(__file__).resolve().parents[2]


def default_build_root() -> Path:
    """<project parent>/<project folder name>_build (AGU10 task 2.1)."""
    root = project_root()
    return root.parent / (root.name + "_build")


def build_root(create: bool = True) -> Path:
    """The build root, refusing one inside the project folder.

    Frozen: the exe has no build root; %LOCALAPPDATA%/DubTool answers for
    generated data, so this returns the app data root instead of raising
    (a caller that needs the real thing checks is_frozen() itself).
    """
    if getattr(sys, "frozen", False):
        from dubtool.app.paths import app_data_root

        p = app_data_root()
    else:
        override = os.environ.get(ENV_VAR, "").strip()
        if override:
            p = Path(override)
        else:
            p = default_build_root()
        p = Path(os.path.abspath(str(p)))
        project = project_root()
        if p == project or p in project.parents or project in p.parents:
            raise RuntimeError(
                f"DUBTOOL_BUILD_ROOT must be OUTSIDE the project folder "
                f"({project}); got {p}. Generated data may never live in "
                f"the project (AGENT_UPDATE_10.md task 1)."
            )
    if create:
        _ensure(p)
    return p


def _ensure(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"cannot create the build root {p}: {exc}") from exc


def _sub(name: str) -> Path:
    root = build_root()
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def venv_dir() -> Path:
    """The build virtual environment (was .venv-build in the project)."""
    return _sub("venv-build")


def build_dir() -> Path:
    """The PyInstaller work directory."""
    return _sub("build")


def dist_dir() -> Path:
    """The promoted build output (DubTool/ + the newest zip only)."""
    return _sub("dist")


def vendor_dir() -> Path:
    """Third-party binaries (ffmpeg.exe, fonts)."""
    return _sub("vendor")


def jobs_dir() -> Path:
    """Job folders when running from source."""
    return _sub("jobs")


def models_dir() -> Path:
    """Voice models (Piper and friends) outside the project."""
    return _sub("models")


def hf_cache_dir() -> Path:
    """Shared Whisper-model cache for all sanitized verification runs."""
    return _sub("hf-cache")


def sample_output_dir() -> Path:
    """End-to-end outputs of the sample videos."""
    return _sub("sample_output")


def screenshots_dir() -> Path:
    return _sub("screenshots")


def inventory_dir() -> Path:
    """Disk reports (scripts/disk_report.py output)."""
    return _sub("inventory")


def logs_dir() -> Path:
    """Every log and redirected command output (never the project root)."""
    return _sub("logs")


def tmp_dir() -> Path:
    """Sanitized homes and temporary files (cleaned after each run)."""
    return _sub("tmp")


def sanitized_tmp_dir(run_id: str) -> Path:
    """<build root>/tmp/sanitized/<run id>/ (AGU10 task 3.2), created."""
    d = tmp_dir() / "sanitized" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


_PROTECTED_ENV_VARS = (
    "USERPROFILE",
    "HOME",
    "SystemRoot",
    "WINDIR",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramData",
    "PUBLIC",
    "ALLUSERSPROFILE",
    "TEMP",
    "TMP",
)


def _norm_resolved(p: Path) -> Path:
    return Path(os.path.normcase(os.path.realpath(os.path.abspath(str(p)))))


def protected_system_and_user_paths() -> list[Path]:
    """Canonical protected user and OS directories (never derived from mutable APPDATA/LOCALAPPDATA)."""
    home = Path.home()
    candidates: list[Path] = [
        home,
        home.parent,
        home / "AppData",
        home / "AppData" / "Local",
        home / "AppData" / "Roaming",
        home / "AppData" / "LocalLow",
        home / "AppData" / "Local" / "Temp",
        home / "AppData" / "Local" / "DubTool",
        home / "AppData" / "Local" / "DubTool" / "tmp",
        home / "AppData" / "Local" / "DubTool" / "tmp" / "sanitized",
        home / "AppData" / "Roaming" / "DubTool",
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
    ]
    for var in _PROTECTED_ENV_VARS:
        val = os.environ.get(var, "").strip()
        if val:
            candidates.append(Path(val))
    if not getattr(sys, "frozen", False):
        try:
            proj = project_root()
            candidates.append(proj)
            br = build_root(create=False)
            candidates.extend([br, br / "tmp", br / "tmp" / "sanitized"])
        except Exception:
            pass
    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            norm = _norm_resolved(c)
        except OSError:
            continue
        key = str(norm)
        if key not in seen:
            seen.add(key)
            out.append(norm)
    return out


def assert_not_protected_dir(target: Path, *, action: str = "modify") -> Path:
    """Refuse operations whose target is a filesystem root, protected user/OS dir, or an ancestor of one."""
    norm = _norm_resolved(target)
    if norm == norm.parent or len(norm.parts) <= 2:
        raise RuntimeError(f"Refusing to {action} filesystem root or top-level path: {target}")
    for protected in protected_system_and_user_paths():
        if norm == protected or norm in protected.parents:
            raise RuntimeError(
                f"Refusing to {action} protected directory ({protected}): got {target}"
            )
    return norm


def is_sanitized_subpath(target: Path, *, allow_media_child: bool = False) -> bool:
    """True only when target is a dedicated child dir inside `<base>/tmp/sanitized/<run_id>`."""
    try:
        assert_not_protected_dir(target, action="clean")
        norm = _norm_resolved(target)
    except (RuntimeError, OSError):
        return False
    is_home = norm.parent.name.lower() == "sanitized" and norm.parent.parent.name.lower() == "tmp" and bool(norm.name)
    is_media = (
        allow_media_child
        and norm.name.lower() == "media"
        and norm.parent.parent.name.lower() == "sanitized"
        and norm.parent.parent.parent.name.lower() == "tmp"
    )
    return is_home or is_media


def safe_rmtree_sanitized(
    target: Path,
    *,
    allow_media_child: bool = False,
    ignore_errors: bool = False,
) -> None:
    """Delete only a verified sanitized temp directory; raises RuntimeError on any protected/unexpected path."""
    raw = Path(target)
    if raw.is_symlink():
        raise RuntimeError(f"Refusing to rmtree a symlink/junction: {target}")
    if not is_sanitized_subpath(raw, allow_media_child=allow_media_child):
        raise RuntimeError(
            f"Refusing to rmtree non-sanitized directory: {target} "
            "(must live strictly under .../tmp/sanitized/<run_id>)"
        )
    if raw.exists():
        shutil.rmtree(raw, ignore_errors=ignore_errors)


def pip_cache_dir() -> Path:
    """pip download cache for the build environment."""
    return _sub("pip-cache")


def _build_vendor_state() -> tuple[Path, Path]:
    """(build-root vendor, legacy project vendor) without creating folders."""
    if getattr(sys, "frozen", False):
        from dubtool.app.paths import bundle_root

        return bundle_root() / "vendor", project_root() / "vendor"
    return build_root(False) / "vendor", project_root() / "vendor"


def uses_project_vendor() -> bool:
    """True when the build root has no vendor/ but the project still does
    (the build script reads it with a one-line notice it should be moved)."""
    b, legacy = _build_vendor_state()
    return not b.exists() and legacy.exists()


def vendor_dir() -> Path:
    """The active vendor directory: build root first, then the legacy
    project vendor/ (compatibility, AGU10 task 2.2)."""
    b, legacy = _build_vendor_state()
    if b.exists():
        return b
    if legacy.exists():
        print(
            f"NOTICE: vendor binaries still in the project ({legacy}) — "
            f"move them to {b} (migrate_workspace.py --apply)"
        )
        return legacy
    return b


if __name__ == "__main__":  # pragma: no cover - manual check helper
    print("project root : ", project_root())
    print("build root   : ", build_root())
