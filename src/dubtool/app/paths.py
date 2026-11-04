"""Resource and data paths that work both from source and frozen (AGU4 6.2).

From source, data lives next to the project root; frozen, PyInstaller's
_MEIPASS holds bundled resources and %LOCALAPPDATA% / %APPDATA% hold user
data.

AGENT_UPDATE_11 item 1: jobs and models ALWAYS live in the build root
(created on demand). The legacy in-project `jobs/` and `models/` are only
READ (to list or resume an old job) and are never written again.
AGENT_UPDATE_11 item 2: verification outputs live in
`<build root>/sample_output`; the sample input videos stay in `sample/input`.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("dubtool")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Read-only bundled resources (vendor/, packaging/modes.yaml)."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        vendor = base / "vendor"
        if vendor.exists():
            return base
        return base
    # src/dubtool/app/paths.py -> project root
    return Path(__file__).resolve().parents[3]


def legacy_jobs_dir() -> Path | None:
    """AGENT_UPDATE_11 item 1: the in-project jobs folder, for read-only
    listing/resume of old jobs. None when it does not exist."""
    legacy = bundle_root() / "jobs"
    if legacy.exists():
        return legacy
    return None


def legacy_models_dir() -> Path | None:
    """AGENT_UPDATE_11 item 1: the in-project models folder, read-only."""
    legacy = bundle_root() / "models"
    if legacy.exists():
        return legacy
    return None


def vendor_dir() -> Path:
    """Third-party binaries: the build root's vendor/ (Update 10 task 1)
    with the legacy in-project vendor/ as fallback during migration."""
    from dubtool import workspace

    return workspace.vendor_dir()


def ffmpeg_dir() -> Path:
    """Directory that may contain ffmpeg.exe/ffprobe.exe (bundled)."""
    return vendor_dir()


def font_file() -> Path | None:
    """Bundled Vietnamese-capable font (open license)."""
    fonts = vendor_dir() / "fonts"
    if fonts.exists():
        for cand in sorted(fonts.glob("*.ttf")):
            return cand
    return None


def app_data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "DubTool"


def settings_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "DubTool"


def jobs_dir() -> Path:
    override = os.environ.get("DUBTOOL_JOBS_DIR")
    if override:
        return Path(override)
    if not is_frozen():
        # AGENT_UPDATE_10 task 1; AGENT_UPDATE_11 item 1: jobs ALWAYS live
        # in the build root (created on demand); the legacy in-project
        # jobs/ stays read-only (see legacy_jobs_dir()).
        from dubtool import workspace

        b = workspace.build_root(False) / "jobs"
        b.mkdir(parents=True, exist_ok=True)
        if (bundle_root() / "jobs").exists():
            log.info(
                "NOTICE: an old in-project jobs/ folder exists (%s) — it is "
                "read-only now; new jobs go to %s",
                bundle_root() / "jobs", b,
            )
        return b
    return app_data_root() / "jobs"


def models_dir() -> Path:
    override = os.environ.get("DUBTOOL_MODELS_DIR")
    if override:
        return Path(override)
    if not is_frozen():
        # AGENT_UPDATE_10 task 1/3.1; AGENT_UPDATE_11 item 1: source runs
        # share one model cache in the build root, created on demand; an
        # in-project legacy models/ is read for compatibility only.
        from dubtool import workspace

        b = workspace.build_root(False) / "models"
        b.mkdir(parents=True, exist_ok=True)
        if (bundle_root() / "models").exists() and not any(b.iterdir()):
            log.info(
                "NOTICE: an old in-project models/ folder exists (%s) — it is "
                "read-only now; the shared cache lives in %s",
                bundle_root() / "models", b,
            )
        return b
    return app_data_root() / "models"


def logs_dir() -> Path:
    return app_data_root() / "logs"


def output_default() -> Path | None:
    p = app_data_root() / "outputs"
    if not is_frozen():
        # AGENT_UPDATE_11 item 2: verification outputs live in
        # <build root>/sample_output; the inputs stay in sample/input.
        from dubtool import workspace

        return workspace.sample_output_dir()
    return p


def ensure_user_dirs() -> None:
    for d in (app_data_root(), logs_dir(), models_dir()):
        d.mkdir(parents=True, exist_ok=True)
