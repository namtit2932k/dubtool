"""settings.json in %APPDATA%/DubTool — never contains secrets (AGU4 8)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from dubtool.app import paths

FILENAME = "settings.json"

DEFAULTS: dict = {
    "language": "vi",            # vi | en
    "whisper_model": "small",
    "whisper_device": "auto",    # auto | cpu | cuda
    "burn_subtitles": True,
    "save_srt": False,
    "original_gain_db": -14.0,
    "voice": "vi-VN-HoaiMyNeural",
    "keep_intermediate": False,
    "default_output_dir": "",
    "jobs_dir": "",              # empty = app default
    "mode_models": {},           # {"fast": "<model id>", "accurate": "<model id>"}
    "privacy_acknowledged": False,
}


def path() -> Path:
    return paths.settings_dir() / FILENAME


def load(raw_path: Path | None = None) -> dict:
    p = raw_path or path()
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in (data or {}).items() if v is not None or k in ("privacy_acknowledged",)})
    merged["mode_models"] = dict(data.get("mode_models") or {})
    return merged


def save(settings: dict, raw_path: Path | None = None) -> None:
    """Atomic write; caller must ensure the dict has no secrets."""
    p = raw_path or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp_path), str(p))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def validate(settings: dict) -> list[str]:
    """Return a list of human-readable problems; empty when valid."""
    problems: list[str] = []
    lang = str(settings.get("language", ""))
    if lang not in ("vi", "en"):
        problems.append("language must be 'vi' or 'en'")
    if str(settings.get("whisper_model", "")) not in ("tiny", "base", "small", "medium", "large-v3"):
        problems.append("whisper_model must be tiny|base|small|medium|large-v3")
    if str(settings.get("whisper_device", "")) not in ("auto", "cpu", "cuda"):
        problems.append("whisper_device must be auto|cpu|cuda")
    gain = settings.get("original_gain_db")
    if not isinstance(gain, (int, float)) or not (-24 <= float(gain) <= -6):
        problems.append("original_gain_db must be between -24 and -6 dB")
    voice = str(settings.get("voice", ""))
    if voice not in ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"):
        problems.append("voice must be a known edge-tts Vietnamese voice")
    for mode, model in (settings.get("mode_models") or {}).items():
        if mode not in ("fast", "accurate"):
            problems.append(f"unknown mode override: {mode}")
        elif not str(model).strip():
            problems.append(f"model override for {mode} is empty")
    return problems
