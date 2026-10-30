"""Export a redacted diagnostics zip (AGU4 7): settings (no secrets), stage
state, the last run's log tail, dependency versions, and system info. Logs
are scrubbed line-by-line for anything that looks like a Gemini key.

AGENT_UPDATE_8.md task 7.2 additions: `language` comes from settings.json
(not just the env var), `chars_per_sec` comes from the loaded effective
config, and the zip gains the TTS failure summary, the quota summary (key
positions + models only), per-stage times and model names, the build id,
the Whisper model / CPU setting, free disk space of the output and jobs
folders, the connectivity table (2.2), and the s2 timing stats.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import time
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


def scrub_line(line: str) -> str:
    return _KEY_PATTERNS.sub("[REDACTED]", line)


def scrub_text(text: str, scrubber=None) -> str:
    sub = scrubber or scrub_line
    return "\n".join(sub(l) for l in text.splitlines())


# ------------------------------------------------------------------ sources
def _settings_json() -> dict:
    from dubtool.app import paths

    try:
        return json.loads((paths.settings_dir() / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _effective_config() -> dict:
    """The loaded, effective configuration (AGU8 7.2); never contains keys."""
    try:
        from dubtool import config as config_mod
        from dubtool.app import paths

        cfg = config_mod.load(paths.bundle_root())
        pid = cfg.llm_provider
        return {
            "whisper_model": cfg.whisper_model,
            "device": cfg.device,
            "compute_type": cfg.compute_type,
            "tts_voice": cfg.tts_voice,
            "llm_provider": pid,
            "llm_model": (cfg.provider_config(pid) or {}).get("model", ""),
            "chars_per_sec": cfg.chars_per_sec,
            # Model names actually configured for each stage that uses one.
            "stage_models": {
                "s2_transcribe": cfg.whisper_model,
                "s3_translate": (cfg.provider_config(pid) or {}).get("model", ""),
                "s4_tts_voice": cfg.tts_voice,
                "s5_fit_duration": (cfg.provider_config(pid) or {}).get("model", ""),
            },
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _build_id() -> str:
    try:
        from dubtool.app.main import _build_info_line

        return _build_info_line()
    except Exception:
        return ""


def _free_space() -> dict:
    out: dict = {}
    checked: list[tuple[str, Path]] = []
    try:
        from dubtool.app import paths

        checked.append(("jobs_dir", paths.jobs_dir()))
        target = paths.output_default()
        if target is not None:
            checked.append(("output_dir", target))
    except Exception:
        pass
    for name, folder in checked:
        probe = folder if folder.exists() else folder.parent
        try:
            out[name] = f"{shutil.disk_usage(probe).free / (1024 ** 3):.1f} GiB free ({probe})"
        except OSError as exc:
            out[name] = f"unavailable: {type(exc).__name__}"
    return out


def _connectivity() -> dict:
    try:
        from dubtool import netcheck

        t0 = time.monotonic()
        rows = netcheck.check_hosts(timeout_s=5.0)
        return {"elapsed_s": round(time.monotonic() - t0, 1), "rows": rows}
    except Exception as exc:
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


def _jobs_summary(jobs_dir: Path) -> list[dict]:
    """Per job: stage status, recorded stage times, last error. No text."""
    out: list[dict] = []
    for state_file in sorted(Path(jobs_dir).glob("*/state.json")):
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(state, dict):
            continue
        entry = {
            "job": state_file.parent.name,
            "stages": state.get("stages", {}),
            "stage_s": state.get("stage_s", {}) if isinstance(state.get("stage_s"), dict) else {},
            "last_error": state.get("last_error"),
        }
        out.append(entry)
    return out


def _tts_summary(jobs_dir: Path) -> dict:
    """Failure records across jobs, by exception type; first/last 5 with the
    text truncated to 80 chars (AGU8 7.2). Never contains key material."""
    records_all: list[tuple[str, dict]] = []
    for path in sorted(Path(jobs_dir).glob("*/tts_failures.json")):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(records, list):
            continue
        for rec in records:
            if isinstance(rec, dict):
                records_all.append((path.parent.name, rec))
    if not records_all:
        return {"failures": 0}
    by_kind: dict[str, int] = {}
    for job_id, rec in records_all:
        by_kind[str(rec.get("error_kind", "?"))] = by_kind.get(str(rec.get("error_kind", "?")), 0) + 1

    def slim(job_id: str, rec: dict) -> dict:
        return {
            "job": job_id,
            "id": rec.get("id"),
            "attempt": rec.get("attempt"),
            "chars": rec.get("chars"),
            "error_kind": rec.get("error_kind"),
            "error": str(rec.get("error", ""))[:80],
            "text": str(rec.get("text", ""))[:80],
            "voice": rec.get("voice"),
            "edge_tts_version": rec.get("edge_tts_version"),
            "elapsed_s": rec.get("elapsed_s"),
        }

    return {
        "failures": len(records_all),
        "by_kind": by_kind,
        "first": [slim(j, r) for j, r in records_all[:5]],
        "last": [slim(j, r) for j, r in records_all[-5:]],
    }


def _quota_summary() -> dict:
    """Active daily-quota entries as key POSITIONS (index in the stored key
    list) and models — never fingerprints or key values (AGU8 7.2)."""
    try:
        from dubtool.app import secrets
        from dubtool.llm import quota_state

        data = quota_state.load()
        keys = [k for k in (secrets.get_gemini_keys() or []) if k]
    except Exception as exc:
        return {"unavailable": f"{type(exc).__name__}: {exc}"}
    position = {quota_state.fingerprint(k): i + 1 for i, k in enumerate(keys)}
    entries = []
    for name, entry in data.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        fp, _, model = name.partition("|")
        label = f"key {position[fp]}/{len(keys)}" if fp in position else "unknown key"
        entries.append({"key": label, "model": model, "reason": entry.get("reason", "")})
    return {"active": entries}


def _asr_stats_summary() -> dict:
    try:
        from dubtool.app import asr_stats

        return asr_stats.last()
    except Exception:
        return {}


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
    settings = _settings_json()
    info = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "frozen": paths.is_frozen(),
        "app_version": _app_version(),
        "build_id": _build_id(),
        "key_source": _key_source(),
        "key_count": _key_count(),
        # AGU8 7.2: the language actually selected in settings (the env var
        # overrides it at runtime, so both are shown).
        "language": str(settings.get("language") or "") or "(unset)",
        "language_env": os.environ.get("DUBTOOL_LANG", ""),
        "cpu_usage": settings.get("cpu_usage", ""),
        "video_encode_preset": settings.get("video_encode_preset", ""),
        "effective_config": _effective_config(),
        "packages": versions,
        "free_space": _free_space(),
        "connectivity": _connectivity(),
        "last_s2_stats": _asr_stats_summary(),
        "quota_summary": _quota_summary(),
        "tts_failures": _tts_summary(_jobs_dir()),
        "jobs": _jobs_summary(_jobs_dir()),
    }
    return info


def _jobs_dir() -> Path:
    try:
        from dubtool.app import paths

        return paths.jobs_dir()
    except Exception:
        return Path("jobs")


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
            scrub_text(json.dumps(info, indent=2, ensure_ascii=False, default=str), scrubber),
        )
        # settings.json never contains secrets, but scrub defensively anyway
        sp = paths.settings_dir() / "settings.json"
        if sp.exists():
            zf.writestr("settings.json", scrub_text(sp.read_text(encoding="utf-8"), scrubber))
        # last few job logs, tail only
        root = Path(jobs_dir) if jobs_dir else _jobs_dir()
        if root.exists():
            logs = sorted(root.glob("*/pipeline.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            for log_file in logs[:3]:
                text = log_file.read_text(encoding="utf-8", errors="replace")[-20000:]
                zf.writestr(f"logs/{log_file.parent.name}/pipeline.log.tail", scrub_text(text, scrubber))
    return out_path
