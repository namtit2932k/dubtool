"""AGENT_UPDATE_8 task 7.1: the startup PyAV guard.

faster-whisper's decoding path uses PyAV APIs that changed in v19, so from
source the app/CLI checks the installed version up front and shows the
exact repair command before stage 2 fails. Pure function; tests pass a
fake version.
"""

from __future__ import annotations

import re

MIN_DESCRIBED = "11"
NEW_LINE = 19

REPAIR_COMMAND = 'pip install "av>=11,<19"'


def _major(version: str) -> int | None:
    match = re.match(r"(\d+)\.", version or "")
    return int(match.group(1)) if match else None


def problem(version: str | None = None) -> str | None:
    """The user-facing warning line, or None when PyAV is compatible.

    `version` overrides the installed version (test seam). An unreadable
    version never blocks the app: stage 2 will fail with its own error.
    """
    try:
        if version is None:
            from importlib import metadata

            version = metadata.version("av")
    except Exception:
        return None
    major = _major(version)
    if major is None or major < NEW_LINE:
        return None
    return (
        f"PyAV {version} is too new for speech recognition (needs av < {NEW_LINE}). "
        f"Downgrade in the terminal: {REPAIR_COMMAND}"
    )
