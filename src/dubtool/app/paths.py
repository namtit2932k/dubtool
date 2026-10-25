"""Resource and data paths that work both from source and frozen (AGU4 6.2).

From source, data lives next to the project root; frozen, PyInstaller's
_MEIPASS holds bundled resources and %LOCALAPPDATA% / %APPDATA% hold user
data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


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


def vendor_dir() -> Path:
    return bundle_root() / "vendor"


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
        # source checkout keeps jobs next to the project
        return bundle_root() / "jobs"
    return app_data_root() / "jobs"


def models_dir() -> Path:
    override = os.environ.get("DUBTOOL_MODELS_DIR")
    if override:
        return Path(override)
    return app_data_root() / "models"


def logs_dir() -> Path:
    return app_data_root() / "logs"


def output_default() -> Path | None:
    p = app_data_root() / "outputs"
    if not is_frozen() and (bundle_root() / "sample" / "output").exists():
        return bundle_root() / "sample" / "output"
    return p


def ensure_user_dirs() -> None:
    for d in (app_data_root(), logs_dir(), models_dir()):
        d.mkdir(parents=True, exist_ok=True)
