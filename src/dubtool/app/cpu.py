"""CPU-usage limits (AGENT_UPDATE_6.md 4.2).

Settings → General "Mức dùng CPU / CPU usage": low / balanced (default) /
maximum, mapped to a thread budget from `os.cpu_count()`:

    low      -> max(1, cores // 4)
    balanced -> max(2, cores // 2)
    maximum  -> cores

The budget goes to Whisper (`cpu_threads`) and to FFmpeg (`-threads N` on
the encode). It is deliberately NOT part of the resume/job hash: it changes
speed, not outputs. FFmpeg children also run below normal priority (see
`proc.run(low_priority=True)`), and while Whisper runs the app lowers its
own priority class so the rest of the computer stays responsive.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("dubtool")

LEVELS = ("low", "balanced", "maximum")
DEFAULT_LEVEL = "balanced"


def thread_budget(level: str | None = None, cores: int | None = None) -> int:
    """Thread count for the given level (defaults to the saved setting)."""
    if cores is None:
        cores = os.cpu_count() or 1
    level = (level or current_level()).lower()
    if level == "low":
        return max(1, cores // 4)
    if level == "maximum":
        return max(1, cores)
    return max(2, cores // 2)  # balanced


def current_level() -> str:
    try:
        from dubtool.app import settings as settings_mod

        return str(settings_mod.load().get("cpu_usage", DEFAULT_LEVEL))
    except Exception:
        return DEFAULT_LEVEL


# ------------------------------------------------------- app priority (s2)
_saved_priority = None


def lower_app_priority() -> None:
    """Allow stage 2's heavy work without freezing the desktop. Windows: the
    process priority class goes to below normal; restore with
    `restore_app_priority()`."""
    global _saved_priority
    if _saved_priority is not None:
        return
    try:
        import psutil

        proc = psutil.Process()
        _saved_priority = proc.nice()
        if os.name == "nt":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            try:
                proc.nice(10)
            except (OSError, PermissionError):
                pass
        log.info("cpu: app priority lowered for the heavy stage")
    except Exception as exc:  # cosmetics only; never fail a stage over this
        _saved_priority = None
        log.info("cpu: could not lower app priority (%s)", exc)


def restore_app_priority() -> None:
    global _saved_priority
    if _saved_priority is None:
        return
    try:
        import psutil

        psutil.Process().nice(_saved_priority)
        log.info("cpu: app priority restored")
    except Exception as exc:
        log.info("cpu: could not restore app priority (%s)", exc)
    finally:
        _saved_priority = None
