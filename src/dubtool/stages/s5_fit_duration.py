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
    source: Path, out: Path, status: str, duration: float, slot: float, max_stretch: float,
    *, cancel=None,
) -> None:
    """Write the final fitted/<id>.wav for the decided status (idempotent)."""
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    if status in ("ok", "shortened"):
        if duration <= slot:
            audio_utils.convert_to_wav(source, out, cancel=cancel)
            return
    if status == "stretched":
        audio_utils.atempo_stretch(source, out, min(duration / slot, max_stretch), cancel=cancel)
    elif status == "overflow":
        audio_utils.atempo_stretch(source, out, max_stretch, cancel=cancel)
    else:
        audio_utils.convert_to_wav(source, out, cancel=cancel)


def cache_key(entry: dict, voice: str) -> str:
    return hashlib.sha256(f"{voice}\x00{entry['vi']}".encode("utf-8")).hexdigest()[:16]


def run(job, config, force: bool = False, cancel=None, api_keys: list[str] | None = None) -> None:
    from dubtool.job import StageCancelled

    audio_utils.require_ffmpeg()
    entries = read_json(job.translated_path)
    entries.sort(key=lambda e: e["start"])

    counts: dict[str, int] = {}
    overflow_ids: list[int] = []
    done_early = 0  # entries kept from an earlier run (progress accounting)

    from dubtool.llm import LLMClient, RateLimitExhausted, get_provider
    from dubtool.llm.budget import BudgetExhausted
    from dubtool.stages.s4_tts import build_tts
    from dubtool.tts_failures import TTSFailureLog, Timer

    failures = TTSFailureLog(job.dir)
    from dubtool.audio_utils import MediaCache

    media_cache = MediaCache(job.dir)

    tts = build_tts(config, job.tts_dir / "cache", media_cache=media_cache)
    tts.cancel = cancel
    max_attempts = int(getattr(config, "max_fit_attempts", 3))
    chunk_max = 40  # AGU6 5.1: at most 40 shortening items per request

    # ------------------------------------------------ batched fitting (5.1)
    # AGENT_UPDATE_6.md task 5.1: the per-segment `llm.shorten` loop is
    # replaced with ROUND-based batches — all segments still too long are
    # shortened in chunks of at most 40 in ONE shorten_batch request per
    # chunk, then TTS is regenerated only for texts that actually changed.
    # This keeps max_fit_attempts rounds but collapses roughly one request
    # per segment per attempt into a handful of requests total.

    class _Ctx:
        __slots__ = ("entry", "source", "fitted", "slot", "attempts")

        def __init__(self, entry: dict, source: Path, fitted: Path, slot: float):
            self.entry = entry
            self.source = source
            self.fitted = fitted
            self.slot = slot
            self.attempts = 0

    contexts: dict[int, _Ctx] = {}
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
            done_early += 1
            continue  # resume-safe: already fitted
        next_start = entries[index + 1]["start"] if index + 1 < len(entries) else None
        context = _Ctx(entry, source, fitted, compute_slot(entry, next_start))
        contexts[seg_id] = context
        # Task 5 (AGU3): whenever a segment is processed in this run,
        # regenerate fitted/<id>.wav — stale audio never survives --force.
        fitted.unlink(missing_ok=True)
        if entry.get("tts_duration") is None:
            entry["tts_duration"] = round(
                media_cache.duration(source, root=job.dir), 3
            )

    def _llm_factory(mode: str | None = None) -> LLMClient | None:
        """Build the selected provider; `mode` overrides to a named pip
        mode's model (the 5.3 fallback). None when a fallback cannot be
        built (missing config/keys) — the caller then runs without LLM."""
        import copy as _copy

        if mode is None:
            try:
                return LLMClient(get_provider(config, api_keys=api_keys, cancel=cancel))
            except Exception as exc:
                log.warning("s5: no LLM provider available for shortening: %s", exc)
                return None
        from dubtool.app import modes as modes_mod

        try:
            from dubtool.app import settings as settings_mod  # noqa: B950

            user_settings = settings_mod.load()
        except Exception:
            user_settings = {}
        try:
            mode_cfg = modes_mod.get(mode, user_settings)
        except Exception as exc:
            log.info("s5: fallback mode %r unavailable: %s", mode, exc)
            return None
        pid = str(mode_cfg.get("provider", "gemini"))
        cfg2 = _copy.copy(config)
        llm_overrides = dict(getattr(config, "llm", {}) or {})
        pid_cfg = dict(llm_overrides.get(pid, {}) or {})
        pid_cfg["model"] = mode_cfg.get("model") or pid_cfg.get("model")
        llm_overrides[pid] = pid_cfg
        cfg2.llm = llm_overrides
        try:
            provider = get_provider(cfg2, provider_id=pid, api_keys=api_keys, cancel=cancel)
            return LLMClient(provider, cancel=cancel)
        except Exception as exc:
            log.info("s5: fallback provider for mode %r failed: %s", mode, exc)
            return None

    llm: LLMClient | None = None
    llm_mode: str | None = None       # which mode the client was built for
    budget_note_logged = False

    def _oversized(ctx: _Ctx) -> bool:
        entry = ctx.entry
        if entry.get("reviewed"):
            return False
        duration = entry.get("tts_duration") or 0.0
        return duration > ctx.slot * config.max_stretch

    for round_number in range(1, max_attempts + 1):
        if cancel is not None and cancel.is_set():
            raise StageCancelled(f"s5: cancelled by user in round {round_number}")
        candidates = [
            ctx for ctx in contexts.values()
            if ctx.attempts < max_attempts and _oversized(ctx)
        ]
        if not candidates:
            break
        if llm is None:
            llm = _llm_factory()
            llm_mode = None if llm is not None else "none"
            if llm is None:
                log.warning(
                    "s5: %d segment(s) too long but no LLM available — "
                    "overflow stretch will be applied",
                    len(candidates),
                )
                break
        log.info(
            "s5: round %d/%d: %d over-long segment(s) queued (%d batch(es))",
            round_number, max_attempts, len(candidates),
            (len(candidates) + chunk_max - 1) // chunk_max,
        )
        items: list[dict] = []
        for ctx in candidates:
            entry = ctx.entry
            new_max = max(int(entry["max_chars"] * 0.85), 1)
            items.append({
                "id": entry["id"],
                "text": entry.get("text") or "",
                "vi": entry["vi"],
                "max_chars": new_max,
            })
        updated_any = False
        try:
            for chunk_start in range(0, len(items), chunk_max):
                if cancel is not None and cancel.is_set():
                    raise StageCancelled(f"s5: cancelled in round {round_number}")
                chunk = items[chunk_start : chunk_start + chunk_max]
                responses = llm.shorten_batch(chunk)
                by_id = {r.get("id"): r for r in responses if isinstance(r, dict)}
                for ctx in candidates[chunk_start : chunk_start + chunk_max]:
                    entry = ctx.entry
                    response = by_id.get(entry["id"])
                    if response is None:
                        continue  # unchanged / not returned: keep the text
                    prev_vi = entry["vi"]
                    prev_duration = entry.get("tts_duration")
                    shortened = str(response.get("vi") or "").strip()
                    if not shortened or shortened == prev_vi:
                        continue  # same text: nothing to re-synthesize
                    entry["vi"] = shortened
                    timer = Timer()
                    try:
                        duration, cache_path = tts.synthesize(entry, cancel=cancel)
                    except TTSFailure as exc:
                        failures.record(entry["id"], ctx.attempts + 1, entry["vi"], exc, timer.elapsed())
                        # Restore so vi and tts_duration keep describing the
                        # clip actually used (AGENT_UPDATE_3.md task 4).
                        entry["vi"] = prev_vi
                        entry["tts_duration"] = prev_duration
                        log.warning(
                            "s5: id=%d TTS failed after shorten; restored previous "
                            "text and clip (falls through to overflow)", entry["id"],
                        )
                        continue
                    shutil.copyfile(str(cache_path), str(ctx.source))
                    entry["tts_duration"] = round(duration, 3)
                    log.info(
                        "s5: id=%d shortened (round %d/%d) -> %.2fs (slot %.2fs)",
                        entry["id"], round_number, max_attempts, duration, ctx.slot,
                    )
                    updated_any = True
                write_json(job.translated_path, entries)  # crash-safe per chunk
        except BudgetExhausted as exc:  # 5.2: finish the run with what exists
            if not budget_note_logged:
                budget_note_logged = True
                log.warning("s5: %s — remaining over-long segments stretch only", exc)
        except RateLimitExhausted as exc:
            # 5.3 fallback: when the selected mode's daily quota is gone,
            # try ONE retry round with the configured fallback mode (fast).
            fallback_mode = str(
                (getattr(config, "llm", {}) or {}).get("shorten_fallback_mode", "fast")
                or ""
            ).lower() if isinstance(getattr(config, "llm", {}), dict) else ""
            if fallback_mode and fallback_mode != llm_mode:
                log.warning("s5: %s — retrying with the %r mode", exc, fallback_mode)
                llm = _llm_factory(fallback_mode)
                llm_mode = fallback_mode if llm is not None else llm_mode
                # Re-queue: this round's candidates keep attempts at 0 more;
                # the fallback batch happens on the NEXT loop round.
                continue
            log.warning("s5: %s — remaining over-long segments stretch only", exc)
        except StageCancelled:
            raise
        except Exception as exc:
            # LLM failure: leave entries untouched; they fall through to the
            # overflow logic (AGENT_UPDATE_3.md task 4).
            log.warning("s5: shorten round %d failed: %s", round_number, exc)
        for ctx in candidates:
            ctx.attempts += 1
        if not updated_any:
            log.info("s5: round %d produced no text changes — stopping", round_number)
            break

    # ------------------------------------------------------------- finalize
    for finalize_index, (seg_id, ctx) in enumerate(contexts.items()):
        entry = ctx.entry
        duration = entry.get("tts_duration") or 0.0
        raw = media_cache.duration(ctx.source, root=job.dir)
        if not entry.get("tts_duration"):
            duration = raw
            entry["tts_duration"] = round(raw, 3)
        status = final_status(
            duration, ctx.slot, config.max_stretch, bool(entry.get("reviewed")), ctx.attempts
        )
        entry["fit_status"] = status
        counts[status] = counts.get(status, 0) + 1
        if status == "overflow":
            overflow_ids.append(seg_id)
        materialize_clip(
            ctx.source, ctx.fitted, status, duration, ctx.slot, config.max_stretch,
            cancel=cancel,
        )
        log.info(
            "s5: progress %d/%d", done_early + finalize_index + 1, len(entries)
        )
        write_json(job.translated_path, entries)  # crash-safe: save per segment

    write_json(job.translated_path, entries)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    log.info("s5: fit summary: %s", summary)
    if overflow_ids:
        log.warning("s5: overflow segment ids: %s", overflow_ids)
    log.info(failures.summary([e.get("vi") or "" for e in entries], max_attempts=max_attempts))
    log.info("s5: done")
