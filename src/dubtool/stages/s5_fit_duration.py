"""Stage 5: fit each Vietnamese clip into its time slot [start, end+slack].

Per segment, in order (AGENT.md section 5):
  2. tts_duration <= slot              -> "ok"
  3. tts_duration <= slot * 1.15       -> "stretched" (ffmpeg atempo)
  4. too long and not reviewed          -> LLM-shorten the one sentence,
     regenerate TTS, re-measure, repeat; max 3 attempts -> then "shortened"
     if it finally fits, else fall through to 5
  5. still too long                    -> "overflow" at max stretch 1.15
Reviewed segments are never rewritten; stretch only.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from dubtool import audio_utils
from dubtool.job import read_json, write_json
from dubtool.tts.base import TTSFailure

log = logging.getLogger("dubtool")


def compute_slot(seg: dict, next_start: float | None, tail_slack_s: float = 1.0) -> float:
    if next_start is not None:
        return next_start - seg["start"]
    return (seg["end"] - seg["start"]) + tail_slack_s


def final_status(
    duration: float,
    slot: float,
    max_stretch: float,
    reviewed: bool,
    attempts_used: int,
) -> str:
    if duration <= slot:
        return "shortened" if attempts_used > 0 else "ok"
    if duration <= slot * max_stretch:
        return "stretched"
    return "overflow"


def materialize_clip(
    source: Path, out: Path, status: str, duration: float, slot: float, max_stretch: float
) -> None:
    """Write the final fitted/<id>.wav for the decided status (idempotent)."""
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    if status in ("ok", "shortened"):
        if duration <= slot:
            audio_utils.convert_to_wav(source, out)
            return
    if status == "stretched":
        audio_utils.atempo_stretch(source, out, min(duration / slot, max_stretch))
    elif status == "overflow":
        audio_utils.atempo_stretch(source, out, max_stretch)
    else:
        audio_utils.convert_to_wav(source, out)


def cache_key(entry: dict, voice: str) -> str:
    return hashlib.sha256(f"{voice}\x00{entry['vi']}".encode("utf-8")).hexdigest()[:16]


def run(job, config, force: bool = False, cancel=None) -> None:
    from dubtool.job import StageCancelled

    audio_utils.require_ffmpeg()
    entries = read_json(job.translated_path)
    entries.sort(key=lambda e: e["start"])

    counts: dict[str, int] = {}
    overflow_ids: list[int] = []

    from dubtool.llm import LLMClient, get_provider
    from dubtool.stages.s4_tts import build_tts
    from dubtool.tts_failures import TTSFailureLog, Timer

    failures = TTSFailureLog(job.dir)

    # Lazy LLM provider: a run that needs no shortening works without a API
    # key (AGENT_UPDATE_3.md task 4).
    def _llm_factory() -> LLMClient:
        return LLMClient(get_provider(config))

    llm: LLMClient | None = None
    tts = build_tts(config, job.tts_dir / "cache")
    max_attempts = int(getattr(config, "max_fit_attempts", 3))

    for index, entry in enumerate(entries):
        processed = index + 1
        if cancel is not None and cancel.is_set():
            raise StageCancelled(f"s5: cancelled by user at segment {entry['id']}")
        seg_id = entry["id"]
        source = job.tts_dir / f"{seg_id}.mp3"
        if not entry.get("vi") or not source.exists():
            log.warning("s5: segment id=%d missing vi/tts clip, skipping", seg_id)
            log.info("s5: progress %d/%d", processed, len(entries))
            continue
        fitted = job.fitted_dir / f"{seg_id}.wav"
        if fitted.exists() and not force and entry.get("fit_status") not in (None, "pending"):
            counts[entry["fit_status"]] = counts.get(entry["fit_status"], 0) + 1
            log.info("s5: progress %d/%d", processed, len(entries))
            continue  # resume-safe: already fitted

        next_start = entries[index + 1]["start"] if index + 1 < len(entries) else None
        slot = compute_slot(entry, next_start)
        duration = entry.get("tts_duration") or audio_utils.probe_duration(source)
        attempts_used = 0

        # Track the values that match the clip currently on disk, so a failed
        # shorten + re-TTS never leaves text describing audio we kept
        # (AGENT_UPDATE_3.md task 4).
        prev_vi = entry.get("vi")
        prev_duration = entry.get("tts_duration")

        # Task 5: whenever a segment is processed in this run, regenerate
        # fitted/<id>.wav — stale audio can never survive with --force.
        fitted.unlink(missing_ok=True)

        # Step 4: shorten + regenerate while the clip is too long.
        while (
            duration > slot * config.max_stretch
            and not entry.get("reviewed")
            and attempts_used < max_attempts
        ):
            attempts_used += 1
            if llm is None:
                llm = _llm_factory()
            new_max = max(int(entry["max_chars"] * 0.85), 1)
            try:
                shortened = llm.shorten(entry["text"], entry["vi"], new_max)
            except Exception as exc:
                # LLM failure: leave the entry untouched and fall through to
                # the overflow logic (task 4).
                log.warning(
                    "s5: id=%d shorten failed (attempt %d/%d, %d chars): %s",
                    seg_id, attempts_used, max_attempts, len(entry.get("vi") or ""), exc,
                )
                break
            entry["vi"] = shortened
            timer = Timer()
            try:
                duration, cache_path = tts.synthesize(entry)
            except TTSFailure as exc:
                failures.record(seg_id, attempts_used, entry["vi"], exc, timer.elapsed())
                # Restore so vi and tts_duration match the clip actually used.
                entry["vi"] = prev_vi
                entry["tts_duration"] = prev_duration
                duration = prev_duration or duration
                log.warning(
                    "s5: id=%d TTS failed after shorten; restored previous text and clip"
                    " (falls through to overflow)", seg_id,
                )
                break
            shutil.copyfile(str(cache_path), str(source))
            log.info(
                "s5: id=%d shortened (attempt %d/%d) -> %.2fs (slot %.2fs)",
                seg_id, attempts_used, max_attempts, duration, slot,
            )
            entry["tts_duration"] = round(duration, 3)
            prev_duration = entry["tts_duration"]

        status = final_status(duration, slot, config.max_stretch, bool(entry.get("reviewed")), attempts_used)
        entry["fit_status"] = status
        counts[status] = counts.get(status, 0) + 1
        if status == "overflow":
            overflow_ids.append(seg_id)
        materialize_clip(source, fitted, status, duration, slot, config.max_stretch)
        log.info("s5: progress %d/%d", processed, len(entries))
        write_json(job.translated_path, entries)  # crash-safe: save per segment

    write_json(job.translated_path, entries)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    log.info("s5: fit summary: %s", summary)
    if overflow_ids:
        log.warning("s5: overflow segment ids: %s", overflow_ids)
    log.info(failures.summary([e.get("vi") or "" for e in entries], max_attempts=max_attempts))
    log.info("s5: done")
