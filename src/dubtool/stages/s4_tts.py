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
    return SegmentTTS(
        EdgeTTSProvider(rate=opts.get("rate", "+0%")),
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


def run(job, config, force: bool = False, cancel=None) -> None:
    audio_utils.require_ffmpeg()
    from dubtool.job import StageCancelled
    from dubtool.audio_utils import MediaCache

    media_cache = MediaCache(job.dir)

    manifest = {
        "cache_dir": "tts/cache",
        "durations": {},
    }
    entries = read_json(job.translated_path)
    cache_dir = job.tts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = TTSFailureLog(job.dir)
    tts = build_tts(config, cache_dir, media_cache=media_cache)
    tts.cancel = cancel
    regenerated = 0
    for position, entry in enumerate(entries, start=1):
        if cancel is not None and cancel.is_set():
            raise StageCancelled(f"s4: cancelled by user at segment {entry['id']}")
        if not entry.get("vi"):
            log.warning("s4: segment id=%d has no vi text, skipping TTS", entry["id"])
            log.info("s4: progress %d/%d", position, len(entries))
            continue
        if (not force) and entry.get("tts_duration") is not None:
            existing_clip = job.tts_dir / f"{entry['id']}.mp3"
            if existing_clip.exists() and (tts._clip_readable(existing_clip) or tts._cache_valid(existing_clip)):
                log.info("s4: progress %d/%d", position, len(entries))
                continue  # already synthesized and not stale
        timer = Timer()
        try:
            duration, cache_path = tts.synthesize(entry, cancel=cancel)
        except TTSFailure as exc:
            failures.record(entry["id"], 1, entry["vi"], exc, timer.elapsed())
            print(failures.summary([e.get("vi") or "" for e in entries]))
            raise RuntimeError(f"s4: TTS failed for segment id={entry['id']}: {exc}") from exc
        out_clip = job.tts_dir / f"{entry['id']}.mp3"
        shutil.copyfile(str(cache_path), str(out_clip))
        entry["tts_duration"] = round(duration, 3)
        entry["fit_status"] = "pending"
        regenerated += 1
        log.info("s4: progress %d/%d", position, len(entries))
        # Save after every segment so a mid-stage kill loses at most one clip.
        write_json(job.translated_path, entries)
    write_json(job.translated_path, entries)
    log.info("s4: synthesized %d new clips (voice=%s)", regenerated, config.tts_voice)
    log.info(failures.summary([e.get("vi") or "" for e in entries]))


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
