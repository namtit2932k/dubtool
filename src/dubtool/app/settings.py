"""settings.json in %APPDATA%/DubTool — never contains secrets (AGU4 8)."""

from __future__ import annotations

import json
import os
import re
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
    "export_readable": True,     # AGU7 task 4: write <name>_vi_translation next to the video
    "readable_format": "docx",   # docx | txt
    "cpu_usage": "balanced",     # low | balanced | maximum (AGU6 4.2)
    "video_encode_preset": "veryfast",  # veryfast | faster | medium (AGU6 4.2)
    "jobs_max_days": 14,         # prune old job folders after N days (AGU6 6.7)
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
    if str(settings.get("cpu_usage", "")).lower() not in ("low", "balanced", "maximum"):
        problems.append("cpu_usage must be low|balanced|maximum")
    if str(settings.get("readable_format", "docx")).lower() not in ("docx", "txt"):
        problems.append("readable_format must be docx|txt")
    if str(settings.get("video_encode_preset", "")).lower() not in ("veryfast", "faster", "medium"):
        problems.append("video_encode_preset must be veryfast|faster|medium")
    days = settings.get("jobs_max_days", 14)
    if not isinstance(days, int) or not (1 <= days <= 365):
        problems.append("jobs_max_days must be an integer between 1 and 365")
    for mode, model in (settings.get("mode_models") or {}).items():
        if mode not in ("fast", "accurate"):
            problems.append(f"unknown mode override: {mode}")
            continue
        model_id = str(model).strip()
        if not model_id:
            problems.append(f"model override for {mode} is empty")
        elif not re.fullmatch(r"[A-Za-z0-9._/\-]{1,128}", model_id):
            problems.append(
                f"model id for {mode} has characters outside A-Z a-z 0-9 . _ - /: {model_id[:40]}"
            )
    return problems
