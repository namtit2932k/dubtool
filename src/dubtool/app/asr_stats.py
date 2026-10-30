"""AGENT_UPDATE_8 task 7: stage-2 (Whisper) statistics for the Settings
tooltip — the real elapsed time of the last transcription and the video
length it was measured on. A tiny JSON file under the settings folder,
never containing secrets."""

from __future__ import annotations

import json

from dubtool.app import paths

FILENAME = "asr_stats.json"


def _path():
    return paths.settings_dir() / FILENAME


def save(stats: dict) -> None:
    """Persist the last stage-2 measurement. Never raises."""
    if not isinstance(stats, dict) or not stats:
        return
    try:
        data = stats
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def last() -> dict:
    """{} when there is no measurement yet."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
