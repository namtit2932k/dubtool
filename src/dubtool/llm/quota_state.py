"""Persistent per-(key, model) daily-quota state (AGENT_UPDATE_6.md 5.3).

Gemini free-tier daily quotas are per project AND per model and reset at
midnight Pacific time (ai.google.dev/gemini-api/docs/rate-limits). Within
one run the KeyPool already tracks a daily-limited key; this module extends
that state across runs on disk so a rerun never wastes a request on a key
we already know is exhausted for that model.

File: %LOCALAPPDATA%/DubTool/quota_state.json shaped as
  { "<first 12 hex of sha256(key)>|<model>": {"until": <epoch s>, "reason": "per-day"} }

The file never contains key values or anything from which a key can be
reconstructed (only a truncated, salted-by-nothing fingerprint). `until`
comes from the API's retryDelay hint when available, otherwise from the
next midnight Pacific (US DST approximated).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
from pathlib import Path

_FILE_NAME = "quota_state.json"
_lock = threading.Lock()


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def state_path() -> Path:
    from dubtool.app.paths import app_data_root

    return app_data_root() / _FILE_NAME


# ------------------------------------------------------------------ clock
def next_pacific_midnight(now_epoch: float) -> float:
    """Next midnight America/Los_Angeles after `now_epoch`, in epoch seconds.

    zoneinfo's database is not always present on Windows, so US DST is
    approximated: PDT (UTC-7) from the second Sunday of March 02:00 local
    to the first Sunday of November 02:00 local, PST (UTC-8) otherwise.
    Good to a few minutes, which is fine for a quota reset fallback.
    """
    import datetime as _dt

    def transition(y: int, month: int, which_sunday: int) -> _dt.date:
        d = _dt.date(y, month, 1)
        weekday_left = (6 - d.weekday()) % 7  # 0 = Monday .. 6 = Sunday
        # which_sunday=0 -> first Sunday
        return d + _dt.timedelta(days=weekday_left + 7 * which_sunday)

    now = _dt.datetime.fromtimestamp(now_epoch, tz=_dt.timezone.utc)
    year = now.year
    dst_start = transition(year, 3, 1)   # second Sunday of March
    dst_end = transition(year, 11, 0)    # first Sunday of November
    today = (now + _dt.timedelta(days=0)).date()
    offset = -8  # PST default
    if dst_start <= today < dst_end:
        offset = -7
    # The next 00:00 Pacific local time after `now` (kept as UTC math so no
    # named tz database is needed — good to a few minutes).
    for add in (1, 2):
        day_local = today + _dt.timedelta(days=add)
        candidate = _dt.datetime.combine(day_local, _dt.time(0, 0), tzinfo=_dt.timezone.utc)
        candidate = candidate + _dt.timedelta(hours=-offset)
        if (candidate - now).total_seconds() > 60:
            return candidate.timestamp()
    raise RuntimeError("unreachable")


def load(now: float | None = None) -> dict[str, dict]:
    """Entries whose `until` passed are removed on load (AGU6 5.3)."""
    now = time.time() if now is None else now
    path = state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict] = {}
    changed = False
    for name, entry in data.items():
        if not isinstance(entry, dict) or "until" not in entry:
            changed = True
            continue
        try:
            until = float(entry["until"])
        except (TypeError, ValueError):
            changed = True
            continue
        if until > now:
            cleaned[str(name)] = {"until": until, "reason": str(entry.get("reason", "per-day"))}
        else:
            changed = True
    if changed:
        _save(cleaned)
    return cleaned


def _save(data: dict[str, dict]) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        import os

        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def mark_daily(key: str, model: str, until: float, now: float | None = None) -> None:
    """Record (key fingerprint, model) as daily-exhausted until `until`."""
    now = time.time() if now is None else now
    with _lock:
        data = load(now)
        data[f"{fingerprint(key)}|{model}"] = {
            "until": float(until),
            "reason": "per-day",
        }
        _save(data)


def exhausted_until(key: str, model: str, now: float | None = None) -> float | None:
    with _lock:
        data = load(time.time() if now is None else now)
    entry = data.get(f"{fingerprint(key)}|{model}")
    if entry is None:
        return None
    try:
        return float(entry["until"])
    except (KeyError, TypeError, ValueError):
        return None
