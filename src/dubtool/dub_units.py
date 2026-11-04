"""Update 12 task 3: sentence-level dub units — the primary data of
stages 3-7 in sentence mode.

The unit of dubbing is the sentence unit from build_units (`units.json`).
The translation of a sentence is no longer cut into fragments that are
voiced separately: one clip per whole sentence, stretched or shortened as
a unit (stage 5), scheduled as a unit (stage 6), and shown as one or more
consecutive cues (stage 7). Redistribution over segments stays available
for display only (translated.json keeps per-segment `vi` with a
`derived: true` flag; no stage 4-7 code reads it in sentence mode).

Config `dub.granularity: sentence` (default) | `segment`. The segment
value keeps the old per-segment behavior (and resumes old jobs). The
granularity is part of `inputs_hash`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("dubtool")


def units_path(job):
    """The job's dub_units.json path (fillna for older job objects and
    test fakes that predate this update: falls back to job.dir)."""
    cached = getattr(job, "dub_units_path", None)
    if cached is not None:
        return cached
    return job.dir / "dub_units.json"


def granularity_for_job(job, config) -> str:
    """Which granularity this run uses.

    - The job already being a sentence-mode job (dub_units.json exists):
      sentence (resume).
    - The job already translated per segment (translated.json with clips
      but no dub_units.json): segment (resume an old job unchanged).
    - Otherwise (fresh job): the configured value (default sentence).
    """
    units_file = units_path(job)
    translated = getattr(job, "translated_path", None)
    if units_file.exists():
        return "sentence"
    if translated is not None and translated.exists() and not units_file.exists():
        return str((getattr(config, "dub", {}) or {}).get("granularity", "segment")).lower()
    return str((getattr(config, "dub", {}) or {}).get("granularity", "sentence")).lower()


def build_dub_units(units: list[dict], translations: dict[int, dict]) -> list[dict]:
    """One dub record per sentence unit.

    `translations` maps unit_id -> {"vi": ..., "model": ...}. `max_chars`
    is the sum of the unit's segments' budgets (information for display,
    never a per-fragment order). `seg_ids`/start/end/time come from the
    plain units; fit/voice fields start empty and are filled by stages 4-5.
    """
    records: list[dict] = []
    for unit in sorted(units, key=lambda u: int(u["unit_id"])):
        uid = int(unit["unit_id"])
        tr = translations.get(uid) or {}
        records.append(
            {
                "unit_id": uid,
                "seg_ids": list(unit.get("seg_ids") or []),
                "start": float(unit["start"]),
                "end": float(unit["end"]),
                "text": unit.get("text") or "",
                "vi": tr.get("vi") or None,
                "max_chars": int(unit.get("max_chars") or 0),
                "model": tr.get("model"),
                "voice": None,
                "tts_duration": None,
                "fit_status": "pending",
                "reviewed": bool(unit.get("reviewed")),
            }
        )
    return records


def load_dub_units(job) -> list[dict] | None:
    try:
        raw = json.loads(units_path(job).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, list) else None


def write_dub_units(job, records: list[dict]) -> None:
    """Atomically write dub_units.json (ensure_ascii=False, UTF-8, indent)."""
    from dubtool.job import write_json

    write_json(units_path(job), records)


def set_translation(records: list[dict], unit_id: int, vi: str, model: str | None) -> bool:
    """Stamp the unit's Vietnamese sentence (and the translating model)."""
    for rec in records:
        if int(rec["unit_id"]) == int(unit_id):
            rec["vi"] = (vi or "").strip()
            rec["model"] = model
            return True
    return False


def a_unit_is_reviewed_unit(unit: dict, entries_by_id: dict[int, dict]) -> bool:
    """A unit containing a reviewed segment is treated as reviewed (never
    rewritten by the pipeline; the reviewer approved fragments or the unit)."""
    for seg_id in unit.get("seg_ids") or []:
        seg = entries_by_id.get(int(seg_id))
        if seg is not None and seg.get("reviewed"):
            return True
    return False


def file_base(entry: dict, mode: str) -> str:
    """The media file base name for an entry: `u<unit_id>` in sentence
    mode (one clip per sentence unit) or the plain segment id."""
    if mode == "sentence":
        return f"u{int(entry['id'])}"
    return str(int(entry["id"]))


def source_for_stage(job, config):
    """The common data source for stages 5-8 (Update 12 task 3): returns

        (mode, entries, persist, base)

    - sentence: dub_units.json — one entry per sentence unit (each gets an
      in-memory `id` alias), media under `u<unit_id>`, persisted back to
      dub_units.json;
    - segment: translated.json (old jobs resume unchanged), media under
      `<id>`.
    """
    mode = granularity_for_job(job, config)
    if mode == "sentence":
        records = load_dub_units(job)
        if not records:
            log.warning(
                "sentence mode requested but dub_units.json is missing — "
                "falling back to segment mode"
            )
            mode = "segment"
        else:
            for rec in records:
                rec.setdefault("id", int(rec["unit_id"]))

            def persist_sentence() -> None:
                write_dub_units(job, records)

            return (
                mode, records, persist_sentence,
                (lambda e: file_base(e, "sentence")),
            )
    from dubtool.job import read_json

    entries = read_json(job.translated_path)

    def persist_segment() -> None:
        from dubtool.job import write_json

        write_json(job.translated_path, entries)

    return mode, entries, persist_segment, (lambda e: file_base(e, "segment"))
