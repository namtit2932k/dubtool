"""Playback schedule (AGENT_UPDATE_3.md task 3).

Vietnamese clips must never overlap: each clip is placed at
`play_start = max(orig_start, previous_play_end + gap)` up to
`max_shift_s`; beyond the cap the segment is marked `overlap: true` and
plays at `orig_start + max_shift_s`. Pure function over (start, duration)
pairs; stage 6 writes schedule.json from it and both s6 and s7 read it.

schedule.json format:
[{"id": 0, "orig_start": 0.52, "play_start": 0.52, "play_end": 4.31, "shift": 0.0, "overlap": false}]
"""

from __future__ import annotations

import logging
from pathlib import Path

from dubtool.job import read_json, write_json

log = logging.getLogger("dubtool")

DEFAULT_GAP_S = 0.08
DEFAULT_MAX_SHIFT_S = 2.0


def schedule_clips(
    items: list[tuple[int, float, float]],
    gap_s: float = DEFAULT_GAP_S,
    max_shift_s: float = DEFAULT_MAX_SHIFT_S,
) -> list[dict]:
    """Build the playback schedule from (id, orig_start, duration) pairs.

    Items are processed in order of orig_start. Segments whose text is empty
    (no duration) must not take part: pass only segments that have a clip.
    """
    ordered = sorted(items, key=lambda t: (t[1], t[0]))
    schedule: list[dict] = []
    prev_play_end = -float("inf")
    for seg_id, orig_start, duration in ordered:
        play_start = max(orig_start, prev_play_end + gap_s)
        shift = play_start - orig_start
        overlap = False
        if shift > max_shift_s:
            # Cap the shift; if this segment then collides with a later
            # clip's original start that is unavoidable — mark it.
            play_start = orig_start + max_shift_s
            shift = max_shift_s
            overlap = True
        play_end = play_start + max(float(duration), 0.0)
        schedule.append(
            {
                "id": seg_id,
                "orig_start": round(orig_start, 6),
                "play_start": round(play_start, 6),
                "play_end": round(play_end, 6),
                "shift": round(shift, 6),
                "overlap": overlap,
            }
        )
        prev_play_end = play_end
    return schedule


def schedule_stats(schedule: list[dict]) -> dict:
    shifted = [s for s in schedule if s["shift"] > 1e-9]
    overlapping = [s for s in schedule if s.get("overlap")]
    return {
        "count": len(schedule),
        "shifted": len(shifted),
        "max_shift": round(max((s["shift"] for s in schedule), default=0.0), 3),
        "total_shift": round(sum(s["shift"] for s in schedule), 3),
        "overlap_ids": [s["id"] for s in overlapping],
    }


def schedule_path(job_dir: Path) -> Path:
    return Path(job_dir) / "schedule.json"


def write_schedule(job_dir: Path, schedule: list[dict]) -> None:
    write_json(schedule_path(job_dir), schedule)


def load_schedule(job_dir: Path) -> list[dict] | None:
    path = schedule_path(job_dir)
    if not path.exists():
        return None
    try:
        data = read_json(path)
    except Exception:
        return None
    return data if isinstance(data, list) else None


def log_report(schedule: list[dict]) -> None:
    stats = schedule_stats(schedule)
    log.info(
        "schedule: %d clips, %d shifted, max_shift=%.2fs, total_shift=%.2fs, overlap ids=%s",
        stats["count"], stats["shifted"], stats["max_shift"], stats["total_shift"],
        stats["overlap_ids"],
    )
