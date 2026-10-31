"""Job-folder housekeeping (AGENT_UPDATE_6.md 6.7).

Prunes old finished job folders under the jobs directory (setting
"jobs_max_days", default 14). Rules:

- Never delete the job that is running.
- Never delete any job modified in the last 3 days (possibly resumable).
- The Whisper model cache is never deleted automatically (only reported,
  so the "Dọn dẹp ngay" button can show its size).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

RESUME_KEEP_DAYS = 3


def tree_size(p: Path) -> int:
    if not p.exists() or p.is_symlink():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for entry in os.scandir(p):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += tree_size(Path(entry.path))
            except OSError:
                continue
    except OSError:
        pass
    return total


def human(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes} B" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} GB"


def _job_running(job: Path) -> bool:
    state = job / "state.json"
    if not state.exists():
        return False
    try:
        st = json.loads(state.read_text(encoding="utf-8"))
    except Exception:
        return False
    stages = st.get("stages") if isinstance(st, dict) else None
    return bool(stages) and any(v == "running" for v in stages.values())


def job_stats(jobs_dir: Path) -> tuple[int, int, float]:
    """(count, total-size bytes, oldest mtime) for the Settings working-files
    group (Update 9 task 6). Symlinks are skipped; missing dir -> (0, 0, 0)."""
    count = 0
    total = 0
    oldest = 0.0
    if not jobs_dir.is_dir():
        return 0, 0, 0.0
    for entry in jobs_dir.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        count += 1
        total += tree_size(entry)
        mt = _mtime(entry)
        if mt and (oldest == 0.0 or mt < oldest):
            oldest = mt
    return count, total, oldest


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def prune_plan(jobs_dir: Path, *, days: int, running_job_id: str | None = None,
               now: float | None = None, all_jobs: bool = False) -> tuple[list[Path], int]:
    """Dry run of prune_jobs: which folders it *would* delete and how many
    bytes that frees (Update 9 task 6 — the confirmation dialog shows these
    numbers before anything is removed). Same selection rules, no deletion."""
    return _prunable(jobs_dir, days=days, running_job_id=running_job_id,
                     now=now, all_jobs=all_jobs, delete=False)


def prune_jobs(jobs_dir: Path, *, days: int, running_job_id: str | None = None,
               now: float | None = None, all_jobs: bool = False) -> tuple[list[Path], int]:
    """Delete old finished job folders; return (deleted, reclaimed bytes).

    "Finished" here means: not running, older than both the age limit and
    the 3-day resumability window. Ageing uses the folder's own mtime
    (the latest write to that job), never state.json contents.

    `all_jobs=True` (Update 9 task 6, the "delete everything" choice in the
    confirmation dialog) skips BOTH age checks but still keeps any job whose
    state.json shows a running stage. The running-job check itself stays —
    an active run can never lose its files from here."""
    return _prunable(jobs_dir, days=days, running_job_id=running_job_id,
                     now=now, all_jobs=all_jobs, delete=True)


def _prunable(jobs_dir: Path, *, days: int, running_job_id: str | None,
              now: float | None, all_jobs: bool,
              delete: bool) -> tuple[list[Path], int]:
    """Shared selection for prune_plan (delete=False) and prune_jobs."""
    now = time.time() if now is None else now
    cutoff = now - max(1, int(days)) * 86400
    resume_cutoff = now - RESUME_KEEP_DAYS * 86400
    deleted: list[Path] = []
    reclaimed = 0
    if not jobs_dir.is_dir():
        return deleted, reclaimed
    for job in [p for p in jobs_dir.iterdir() if p.is_dir() and not p.is_symlink()]:
        if running_job_id and job.name == running_job_id:
            continue
        try:
            age = _mtime(job)
        except OSError:
            continue
        if not all_jobs:
            if age >= resume_cutoff:
                continue      # could still be a resumable run from the last 3 days
            if age >= cutoff:
                continue      # inside the kept-days window
        if _job_running(job):
            continue          # never delete the job that is running
        size = tree_size(job)
        if not delete:
            deleted.append(job)
            reclaimed += size
            continue
        try:
            _rmtree_nofollow(job)
        except OSError:
            continue
        deleted.append(job)
        reclaimed += size
    return deleted, reclaimed


def _rmtree_nofollow(dirpath: Path) -> None:
    for entry in os.scandir(dirpath):
        try:
            p = Path(entry.path)
            if entry.is_symlink():
                if entry.is_dir():
                    p.rmdir()
                else:
                    p.unlink()
            elif entry.is_dir():
                _rmtree_nofollow(p)
            else:
                p.unlink()
        except OSError:
            continue
    try:
        dirpath.rmdir()
    except OSError:
        pass


if __name__ == "__main__":  # pragma: no cover - manual check helper
    from dubtool.app.paths import jobs_dir

    dels, rec = prune_jobs(jobs_dir(), days=14)
    for p in dels:
        print("deleted", p)
    print("reclaimed", human(rec))
