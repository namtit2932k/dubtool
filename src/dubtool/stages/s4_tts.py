"""Stage 4: synthesize Vietnamese clips per segment, cached by (text, voice).

Canonical audio lives in tts/cache/<hash>.mp3 keyed by (text, voice, rate,
silence-trim settings — AGENT_UPDATE_3.md task 2). Durations are measured per
sampling so unchanged lines are never regenerated (idempotent, resumable).
When trimming is on, the cache stores the *trimmed* audio and
`entry["tts_duration"]` is measured after the trim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from pathlib import Path

from dubtool import audio_utils
from dubtool.audio_utils import MediaCache
from dubtool.job import read_json, write_json
from dubtool.tts.base import TTSFailure
from dubtool.tts_failures import TTSFailureLog, Timer

log = logging.getLogger("dubtool")


def tts_options(config) -> dict:
    """Synthesis options that affect audio content (part of the cache key)."""
    options: dict = {"rate": getattr(config, "tts_rate", "+0%")}
    if getattr(config, "tts_trim_silence", False):
        options["trim"] = {
            "threshold_db": int(getattr(config, "tts_silence_threshold_db", -35)),
            "margin_s": float(getattr(config, "tts_silence_margin_s", 0.05)),
        }
    return options


def build_tts(config, cache_dir: Path, media_cache=None) -> "SegmentTTS":
    from dubtool.tts.edge import EdgeTTSProvider

    opts = tts_options(config)
    tts_cfg = getattr(config, "tts", {}) or {}
    return SegmentTTS(
        EdgeTTSProvider(
            rate=opts.get("rate", "+0%"),
            rate_limit_s=float(tts_cfg.get("pacing_delay_s", 0.3)),
            max_cooldown_s=float(tts_cfg.get("max_cooldown_s", 120.0)),
        ),
        cache_dir,
        config.tts_voice,
        options=opts,
        media_cache=media_cache,
    )


class SegmentTTS:
    def __init__(
        self,
        provider,
        cache_dir: Path,
        default_voice: str = "vi-VN-HoaiMyNeural",
        options: dict | None = None,
        media_cache=None,
    ):
        self.provider = provider
        self.cache_dir = cache_dir
        self.default_voice = default_voice
        self.options = options or {"rate": "+0%"}
        self.durations: dict[str, float] = {}
        self.media_cache = media_cache
        self.cancel = None

    def _duration(self, path: Path) -> float:
        """Probe through the shared MediaCache unless overridden."""
        if self.media_cache is not None:
            return self.media_cache.duration(path)
        return audio_utils.probe_duration(path)

    def key_for(self, seg: dict) -> str:
        voice = seg.get("tts_voice") or self.default_voice
        text = (seg.get("vi") or "").strip()
        payload = f"{voice}\x00{text}\x00" + json.dumps(self.options, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _cache_valid(path: Path) -> bool:
        """A cache entry counts only if it is a readable audio file."""
        if not path.exists() or path.stat().st_size < 1000:
            return False
        try:
            audio_utils.probe_duration(path)
            return True
        except Exception:
            path.unlink(missing_ok=True)  # drop poisoned cache entries
            return False

    def _clip_readable(self, path: Path) -> bool:
        """_cache_valid, probing through the MediaCache when available."""
        if self.media_cache is None:
            return self._cache_valid(path)
        try:
            return self.media_cache.duration(path) > 0
        except Exception:
            path.unlink(missing_ok=True)
            return False

    def synthesize(self, seg: dict, cancel=None) -> tuple[float, Path]:
        """Return (duration s after trim, cache path) for this segment's text."""
        text = (seg.get("vi") or "").strip()
        if not text:
            raise RuntimeError(f"segment id={seg['id']} has no Vietnamese text")
        key = self.key_for(seg)
        cache_file = self.cache_dir / f"{key}.mp3"
        if not self._cache_valid(cache_file):
            voice = seg.get("tts_voice") or self.default_voice
            raw = self.cache_dir / f"~raw-{uuid.uuid4().hex}.mp3"
            try:
                self.provider.synthesize(text, voice, raw)
                trim = self.options.get("trim")
                if trim:
                    tmp = self.cache_dir / f"~trim-{uuid.uuid4().hex}.mp3"
                    try:
                        audio_utils.trim_silence(
                            raw, tmp, threshold_db=trim["threshold_db"], margin_s=trim["margin_s"],
                            cancel=cancel or self.cancel,
                        )
                        if self._duration(tmp) > 0.05:
                            os_replace_strong(tmp, cache_file)
                        else:
                            # Trim emptied the clip (paranoia): keep raw audio.
                            os_replace_strong(raw, cache_file)
                    finally:
                        tmp.unlink(missing_ok=True)
                else:
                    os_replace_strong(raw, cache_file)
            finally:
                raw.unlink(missing_ok=True)
        if key not in self.durations:
            self.durations[key] = self._duration(cache_file)
        return self.durations[key], cache_file


def os_replace_strong(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    src.replace(dst)


def _sleep_cooldown(seconds: float, cancel=None, stage_cancel_cls=None) -> None:
    """AGU8 2.1: wait out a round cooldown with a visible countdown, honoring
    Cancel at once (checked between 1-second steps)."""
    import time as _time

    remaining = float(seconds)
    announced = -1
    while remaining > 0:
        if cancel is not None and cancel.is_set():
            raise (stage_cancel_cls or RuntimeError)("s4: cancelled during the retry cooldown")
        left = int(remaining + 0.999)
        if left != announced and (left <= 10 or left % 10 == 0 or left == int(seconds)):
            log.info("s4: dịch vụ giọng đọc đang bận, thử lại sau %d s", left)
        announced = left
        step = min(1.0, remaining)
        _time.sleep(step)
        remaining -= step


def _measure_clock_offset_once(state: dict) -> None:
    """AGU8 2.2: measure the system clock offset once per run against one
    HTTPS Date header and log it as a diagnostic. Never changes the clock."""
    if state.get("clock_offset_done"):
        return
    state["clock_offset_done"] = True
    try:
        from dubtool import netcheck

        hosts = netcheck.edge_tts_hosts() or [netcheck.GEMINI_HOST]
        offset = netcheck.clock_offset(hosts[0])
        if offset is None:
            log.info("s4: clock offset could not be measured (no response Date header)")
        else:
            log.info("s4: system clock offset vs %s: %+.2f s (diagnostic only)", hosts[0], offset)
            netcheck.record_offset(hosts[0], offset)
            # AGU9 10.7: a big offset keeps surfacing (dialog + diagnostics);
            # here it stays a one-line possibility, never a cause claim.
            if abs(offset) > 30.0:
                log.info("s4: clock is about %+.0f s off vs the server — consider syncing Windows time", offset)
    except Exception as exc:
        log.info("s4: clock offset measurement failed: %s", exc)


def _fault_wrap(config):
    """Debug-only fault injection (AGU8 2.5): --fault tts:fail-first=N wraps
    the provider so the first N synthesize attempts raise TTSFailure."""
    from dubtool import faults as faults_mod

    spec = faults_mod.get("tts")
    if not spec:
        return None
    faults_mod.announce()

    from dubtool.tts.base import TTSFailure  # noqa: E402

    class _FaultProvider:
        def __init__(self, inner, fail_first: int):
            self.inner = inner
            self.fail_first = fail_first
            self.calls = 0

        def synthesize(self, text, voice, out_path):
            self.calls += 1
            if self.calls <= self.fail_first:
                raise TTSFailure(
                    f"injected fault tts:fail-first ({self.calls}/{self.fail_first})"
                )
            return self.inner.synthesize(text, voice, out_path)

    fail_first = int(spec.get("fail-first", 0))
    if fail_first <= 0:
        return None
    return lambda inner: _FaultProvider(inner, fail_first)


def _fail_rate(config) -> str:
    """The edge-tts rate string that gets logged with every failure."""
    tts_cfg = getattr(config, "tts", {}) or {}
    return str(tts_cfg.get("rate", getattr(config, "tts_rate", "+0%")))


def run(job, config, force: bool = False, cancel=None) -> None:
    """AGU8 2.1: a failed segment never aborts the stage.

    Flow: first pass (one attempt per segment; failures are recorded and the
    pass continues) -> deferred retry rounds with long cooldowns (config
    tts.retry_rounds / round_cooldown_s; the circuit breaker stops calling
    the service after tts.breaker_consecutive consecutive failures and jumps
    straight into the round cooldown) -> optional backup voice (config
    tts.fallback) for whatever is still missing -> otherwise the dedicated
    TTSUnavailableError, with every finished clip preserved for Resume."""
    import time as _time

    from dubtool.job import StageCancelled
    from dubtool.tts.base import TTSFailure, TTSUnavailableError
    from dubtool.tts.fallback import BackupTTS, build_backup

    audio_utils.require_ffmpeg()
    media_cache = MediaCache(job.dir)

    entries = read_json(job.translated_path)
    cache_dir = job.tts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = TTSFailureLog(job.dir)
    tts_cfg = getattr(config, "tts", {}) or {}
    tts = build_tts(config, cache_dir, media_cache=media_cache)
    tts.cancel = cancel
    provider = getattr(tts, "provider", None)  # fakes may not have one
    fault_factory = _fault_wrap(config)
    if fault_factory is not None and provider is not None:
        tts.provider = fault_factory(provider)

    def clip_ok(entry) -> bool:
        if entry.get("tts_duration") is not None:
            existing = job.tts_dir / f"{entry['id']}.mp3"
            if existing.exists() and (
                tts._clip_readable(existing) or tts._cache_valid(existing)
            ):
                return True
        return False

    def seg_voice(entry) -> str:
        return entry.get("tts_voice") or config.tts_voice

    def position(entry) -> int:
        for i, e in enumerate(entries, start=1):
            if e is entry:
                return i
        return len(entries)

    idle = [e for e in entries if e.get("vi") and (force or not clip_ok(e))]
    finished_count = len(entries) - len(idle)
    pacing_delay = float(tts_cfg.get("pacing_delay_s", 0.3))
    pacing_start = float(tts_cfg.get("pacing_delay_s", 0.3))
    max_delay = float(tts_cfg.get("max_delay_s", 2.0))
    breaker_at = max(1, int(tts_cfg.get("breaker_consecutive", 3)))
    rounds = max(0, int(tts_cfg.get("retry_rounds", 3)))
    cooldowns = [float(c) for c in (tts_cfg.get("round_cooldown_s") or [60, 180, 300])]
    attempt_no: dict[int, int] = {}
    state: dict = {}
    fallback_uses = 0
    started = _time.monotonic()

    def attempt(entry) -> bool:
        """One synthesis attempt; False records the failure (never fatal)."""
        nonlocal pacing_delay, fallback_uses
        timer = Timer()
        rate_provider = getattr(tts, "provider", None)
        if rate_provider is not None:
            rate_provider.rate_limit_s = pacing_delay
        seg_id = entry["id"]
        attempt_no[seg_id] = attempt_no.get(seg_id, 0) + 1
        duration, cache_path = None, None
        try:
            duration, cache_path = tts.synthesize(entry, cancel=cancel)
        except TTSFailure as exc:
            failures.record(
                seg_id,
                attempt_no[seg_id],
                entry["vi"],
                exc,
                timer.elapsed(),
                voice=seg_voice(entry),
                rate=_fail_rate(config),
            )
            _measure_clock_offset_once(state)
            pacing_delay = min(max_delay, max(pacing_delay * 2.0, 0.1))
            return False
        shutil.copyfile(str(cache_path), str(job.tts_dir / f"{seg_id}.mp3"))
        entry["tts_duration"] = round(duration, 3)
        entry["fit_status"] = "pending"
        pacing_delay = max(pacing_start, pacing_delay * 0.7)
        return True

    def still_missing() -> list:
        return [e for e in entries if e.get("vi") and (e in idle) and not clip_ok(e)]

    all_texts = [e.get("vi") or "" for e in entries]

    # ------------------------------------------------------ first pass
    consecutive = 0
    for entry in idle:
        if cancel is not None and cancel.is_set():
            raise StageCancelled(f"s4: cancelled by user at segment {entry['id']}")
        ok = attempt(entry)
        if ok:
            finished_count += 1
            consecutive = 0
        else:
            consecutive += 1
            if consecutive >= breaker_at:
                log.warning(
                    "s4: circuit breaker tripped after %d consecutive failures — "
                    "stopping the pass and going to the deferred-round cooldown",
                    consecutive,
                )
                break
        log.info("s4: progress %d/%d", position(entry), len(entries))
        write_json(job.translated_path, entries)

    # --------------------------------------------- deferred retry rounds
    used_rounds = 0
    for round_number in range(rounds):
        missing = still_missing()
        if not missing:
            break
        used_rounds = round_number + 1
        cooldown = cooldowns[round_number] if round_number < len(cooldowns) else cooldowns[-1]
        _sleep_cooldown(cooldown, cancel=cancel, stage_cancel_cls=StageCancelled)
        consecutive = 0  # a fresh round starts with the trust reset
        for entry in missing:
            if cancel is not None and cancel.is_set():
                raise StageCancelled(f"s4: cancelled during retry round {round_number + 1}")
            if clip_ok(entry):
                continue
            ok = attempt(entry)
            if ok:
                finished_count += 1
                consecutive = 0
            else:
                consecutive += 1
                if consecutive >= breaker_at:
                    log.warning(
                        "s4: circuit breaker tripped again in round %d — "
                        "skipping to the next cooldown",
                        round_number + 1,
                    )
                    break
            log.info("s4: progress %d/%d", position(entry), len(entries))
            write_json(job.translated_path, entries)

    # ----------------------------------------------------- backup voice
    missing_final = still_missing()
    if missing_final and str(tts_cfg.get("fallback", "none")).lower() != "none":
        backup = build_backup(config)
        if backup is not None:
            log.warning(
                "s4: %d segment(s) still missing after %d round(s) — using the "
                "backup voice. A different voice on a few segments is only "
                "acceptable because the alternative is a failed job.",
                len(missing_final), used_rounds,
            )
            backup_tts = SegmentTTS(
                BackupTTS(tts.provider, backup), cache_dir, config.tts_voice,
                options=tts.options, media_cache=media_cache,
            )
            tts.provider = backup_tts.provider
            for entry in missing_final:
                if cancel is not None and cancel.is_set():
                    raise StageCancelled("s4: cancelled during the backup-voice pass")
                if attempt(entry):
                    finished_count += 1
                tts.provider = backup_tts.provider  # keep counting fallback uses
                log.info("s4: progress %d/%d", position(entry), len(entries))
                write_json(job.translated_path, entries)
            fallback_uses = backup_tts.provider.fallback_uses

    # ------------------------------------------------------------ finish
    write_json(job.translated_path, entries)
    final_missing = still_missing()
    elapsed = _time.monotonic() - started
    if final_missing:
        raise TTSUnavailableError(
            sorted({e["id"] for e in final_missing}), finished_count
        )
    log.info(
        "s4: all clips present: %d new (voice=%s), retry_rounds_used=%d, "
        "fallback_voice_used=%d, %.1fs total",
        finished_count, config.tts_voice, used_rounds, fallback_uses, elapsed,
    )
    log.info(failures.summary(all_texts))


def regenerate_tts(job, config, ids: list[int], force: bool = True) -> None:
    """`retts`: re-synthesize the given segments (e.g. after manual edits)."""
    audio_utils.require_ffmpeg()
    entries = read_json(job.translated_path)
    cache_dir = job.tts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = TTSFailureLog(job.dir)
    tts = build_tts(config, cache_dir)
    id_set = set(ids)
    regenerated = 0
    for entry in entries:
        if entry["id"] not in id_set:
            continue
        if not entry.get("vi"):
            raise RuntimeError(f"retts: segment id={entry['id']} has no Vietnamese text")
        timer = Timer()
        try:
            duration, cache_path = tts.synthesize(entry)
        except TTSFailure as exc:
            failures.record(entry["id"], 1, entry["vi"], exc, timer.elapsed())
            raise
        shutil.copyfile(str(cache_path), str(job.tts_dir / f"{entry['id']}.mp3"))
        entry["tts_duration"] = round(duration, 3)
        entry["fit_status"] = "pending"
        regenerated += 1
        write_json(job.translated_path, entries)
    # Drop stale fitted clips so s5 refits these ids.
    for seg_id in id_set:
        stale = job.fitted_dir / f"{seg_id}.wav"
        if stale.exists():
            stale.unlink()
    if regenerated != len(id_set):
        missing = sorted(id_set - {e["id"] for e in entries})
        log.warning("retts: ids not found in translated.json: %s", missing)
    log.info("retts: regenerated %d clips", regenerated)


def _make_provider():
    from dubtool.tts.edge import EdgeTTSProvider

    return EdgeTTSProvider()

