"""Export a redacted diagnostics zip (AGU4 7): settings (no secrets), stage
state, the last run's log tail, dependency versions, and system info. Logs
are scrubbed line-by-line for anything that looks like a Gemini key.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# Gemini keys start with "AIza" and are 35–40 chars; newer keys issued by
# Google AI Studio start with "AQ." (used on this machine). Also scrub
# generic assignments like GEMINI_API_KEY=... just in case.
_KEY_PATTERNS = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|AQ\.[0-9A-Za-z_\-]{30,}|GEMINI_API_KEY\s*[=:]\s*\S+")


def _stored_key_values() -> set[str]:
    """The exact values of every stored key (AGENT_UPDATE_5.md 2.5) so the
    scrubber can catch them even when they match no pattern. Read from the
    secret store in memory only; failures degrade gracefully."""
    try:
        from dubtool.app import secrets

        return {k for k in secrets.get_gemini_keys() if k}
    except Exception:
        return set()


def _exact_values_re(values: set[str]):
    if not values:
        return None
    longest_first = sorted(values, key=len, reverse=True)
    return re.compile("|".join(re.escape(v) for v in longest_first))


def _compiled(i18n_unused=None):
    return _KEY_PATTERNS


def build_scrubber():
    """Combined redaction: generic patterns + all stored key values.
    Returns a scrub_line(text)->str function."""
    exact = _exact_values_re(_stored_key_values())

    def scrub_line(line: str) -> str:
        line = _KEY_PATTERNS.sub("[REDACTED]", line)
        if exact is not None:
            line = exact.sub("[REDACTED]", line)
        return line

    return scrub_line


# Default scrubber (no stored values loaded at import time).
def scrub_line(line: str) -> str:
    return _KEY_PATTERNS.sub("[REDACTED]", line)


def scrub_text(text: str, scrubber=None) -> str:
    sub = scrubber or scrub_line
    return "\n".join(sub(l) for l in text.splitlines())


def collect_info() -> dict:
    from dubtool import config as config_mod
    from dubtool.app import paths

    versions: dict[str, str] = {}
    for mod, name in (
        ("PySide6", "PySide6"),
        ("faster_whisper", "faster-whisper"),
        ("ctranslate2", "ctranslate2"),
        ("edge_tts", "edge-tts"),
        ("keyring", "keyring"),
        ("google.genai", "google-genai"),
    ):
        try:
            m = __import__(mod, fromlist=["x"])
            versions[name] = str(getattr(m, "__version__", "unknown"))
        except Exception as exc:
            versions[name] = f"unavailable: {type(exc).__name__}"
    info = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "frozen": paths.is_frozen(),
        "app_version": _app_version(),
        "key_source": _key_source(),
        "key_count": _key_count(),
        "language": os.environ.get("DUBTOOL_LANG", ""),
        "packages": versions,
        "config_defaults_chars_per_sec": config_mod.DEFAULTS.get("chars_per_sec"),
    }
    return info


def _app_version() -> str:
    try:
        from dubtool.app.main import app_version

        return app_version()
    except Exception:
        return "unknown"


def _key_source() -> str:
    try:
        from dubtool.app.secrets import key_source

        return key_source()  # 'keyring' | 'env' | 'none' — never the key itself
    except Exception:
        return "unknown"


def _key_count() -> int:
    try:
        from dubtool.app import secrets

        return len(secrets.get_gemini_keys())
    except Exception:
        return 0


def export_diagnostics(out_path: Path, jobs_dir: Path | None = None) -> Path:
    """Write the zip; returns its path. Contents are redacted."""
    from dubtool.app import paths

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    info = collect_info()
    # Scrub with knowledge of the live stored key values (in-memory only).
    scrubber = build_scrubber()
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "diagnostics.json",
            scrub_text(json.dumps(info, indent=2, ensure_ascii=False), scrubber),
        )
        # settings.json never contains secrets, but scrub defensively anyway
        sp = paths.settings_dir() / "settings.json"
        if sp.exists():
            zf.writestr("settings.json", scrub_text(sp.read_text(encoding="utf-8"), scrubber))
        # last few job logs, tail only
        root = Path(jobs_dir) if jobs_dir else paths.jobs_dir()
        if root.exists():
            logs = sorted(root.glob("*/pipeline.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            for log_file in logs[:3]:
                text = log_file.read_text(encoding="utf-8", errors="replace")[-20000:]
                zf.writestr(f"logs/{log_file.parent.name}/pipeline.log.tail", scrub_text(text, scrubber))
    return out_path
