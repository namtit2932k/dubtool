"""Stage 4: synthesize Vietnamese clips per unit using offline VieNeu-TTS v3.

Canonical audio lives in ``tts/cache/<hash>.wav`` (PCM 16-bit WAV at the model's
native sample rate: 48 kHz for ``v3turbo_int8``, 24 kHz for ``v3nano``), keyed by
``(voice, text, provider, mode, precision, trim settings)``. Unchanged lines are
never regenerated (idempotent, resumable). When silence trimming is enabled, the
cache stores the trimmed WAV and ``entry["tts_duration"]`` is measured after trim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from dubtool import audio_utils, heartbeat
from dubtool.audio_utils import MediaCache
from dubtool.job import StageCancelled, read_json, write_json
from dubtool.tts.base import TTSFailure, TTSUnavailableError
from dubtool.tts.vieneu import (
    DEFAULT_TTS_MODE,
    DEFAULT_VOICE,
    TTS_MODES,
    VieNeuTTSProvider,
    normalize_mode,
    resolve_voice,
)
from dubtool.tts_failures import TTSFailureLog, Timer

log = logging.getLogger("dubtool")

STEP_DIRECT = "direct"
STEP_SPLIT = "split"


def tts_clip_path(tts_dir: Path, base_name: str) -> Path:
    """Canonical Stage 4 output path for a unit/segment (WAV)."""
    return Path(tts_dir) / f"{base_name}.wav"


def existing_tts_clip_path(tts_dir: Path, base_name: str) -> Path | None:
    """Return existing WAV clip, or legacy MP3 clip from an older job resume."""
    wav = Path(tts_dir) / f"{base_name}.wav"
    if wav.exists():
        return wav
    mp3 = Path(tts_dir) / f"{base_name}.mp3"
    if mp3.exists():
        return mp3
    return None


def tts_options(config) -> dict:
    """Synthesis options that affect audio content (part of the cache key)."""
    tts_cfg = getattr(config, "tts", {}) or {}
    mode = normalize_mode(tts_cfg.get("mode", DEFAULT_TTS_MODE))
    spec = TTS_MODES[mode]
    options: dict = {
        "provider": "vieneu",
        "mode": mode,
        "precision": spec["precision"],
    }
    if mode == "v3nano":
        options["steps"] = int(tts_cfg.get("steps", spec.get("steps", 16)) or 16)
    if getattr(config, "tts_trim_silence", False):
        options["trim"] = {
            "threshold_db": int(getattr(config, "tts_silence_threshold_db", -35)),
            "margin_s": float(getattr(config, "tts_silence_margin_s", 0.05)),
        }
    return options


def build_tts(config, cache_dir: Path, media_cache=None) -> "SegmentTTS":
    opts = tts_options(config)
    tts_cfg = getattr(config, "tts", {}) or {}
    mode = opts["mode"]
    threads_cfg = int(tts_cfg.get("threads", 0) or 0)
    if threads_cfg <= 0:
        enc_threads = getattr(config, "encode_threads", 0)
        threads_cfg = int(enc_threads) if isinstance(enc_threads, int) and enc_threads > 0 else 0
    steps = int(tts_cfg.get("steps", 16) or 16)
    provider = VieNeuTTSProvider(
        mode=mode,
        threads=threads_cfg or None,
        steps=steps,
    )
    voice = resolve_voice(getattr(config, "tts_voice", DEFAULT_VOICE), mode)
    return SegmentTTS(
        provider,
        cache_dir,
        voice,
        options=opts,
        media_cache=media_cache,
    )


def os_replace_strong(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    os.replace(str(src), str(dst))


class SegmentTTS:
    def __init__(
        self,
        provider,
        cache_dir: Path,
        default_voice: str = DEFAULT_VOICE,
        options: dict | None = None,
        media_cache=None,
    ):
        self.provider = provider
        self.cache_dir = Path(cache_dir)
        self.default_voice = default_voice
        self.options = options or {
            "provider": "vieneu",
            "mode": DEFAULT_TTS_MODE,
            "precision": TTS_MODES[DEFAULT_TTS_MODE]["precision"],
        }
        self.durations: dict[str, float] = {}
        self.media_cache = media_cache
        self.cancel = None
        self._dur_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._dur_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _duration(self, path: Path) -> float:
        """Probe through the shared MediaCache unless overridden."""
        if self.media_cache is not None:
            return self.media_cache.duration(path)
        return audio_utils.probe_duration(path)

    def key_for(self, seg: dict) -> str:
        voice = seg.get("tts_voice") or self.default_voice
        text = (seg.get("vi") or "").strip()
        return self._key(voice, text)

    def _key(self, voice: str, text: str) -> str:
        payload = f"{voice}\x00{text}\x00" + json.dumps(self.options, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _cache_valid(path: Path) -> bool:
        """A cache entry counts only if it is a readable audio file."""
        if not path.exists() or path.stat().st_size < 400:
            return False
        try:
            return audio_utils.probe_duration(path) > 0
        except Exception:
            path.unlink(missing_ok=True)
            return False

    def _clip_readable(self, path: Path) -> bool:
        """_cache_valid, probing through the MediaCache when available."""
        if not path.exists() or path.stat().st_size < 400:
            return False
        if self.media_cache is None:
            return self._cache_valid(path)
        try:
            return self.media_cache.duration(path) > 0
        except Exception:
            path.unlink(missing_ok=True)
            return False

    def synthesize_text(self, text: str, voice: str) -> tuple[float, Path]:
        """Synthesize one (text, voice) pair into the WAV content cache."""
        text = (text or "").strip()
        if not text:
            raise RuntimeError("refusing to synthesize empty text")
        key = self._key(voice, text)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / f"{key}.wav"

        def _usable(path: Path) -> bool:
            return path.exists() and path.stat().st_size >= 400 and self._clip_readable(path)

        if not _usable(cache_file):
            with self._lock_for(key):
                if not _usable(cache_file):
                    raw = self.cache_dir / f"~raw-{uuid.uuid4().hex}.wav"
                    try:
                        if self.provider is not None and hasattr(self.provider, "cancel"):
                            self.provider.cancel = self.cancel
                        self.provider.synthesize(text, voice, raw)
                        trim = self.options.get("trim")
                        if trim:
                            tmp = self.cache_dir / f"~trim-{uuid.uuid4().hex}.wav"
                            try:
                                audio_utils.trim_silence(
                                    raw,
                                    tmp,
                                    threshold_db=trim["threshold_db"],
                                    margin_s=trim["margin_s"],
                                    cancel=self.cancel,
                                )
                                if tmp.exists() and self._duration(tmp) > 0.05:
                                    os_replace_strong(tmp, cache_file)
                                else:
                                    os_replace_strong(raw, cache_file)
                            finally:
                                tmp.unlink(missing_ok=True)
                        else:
                            os_replace_strong(raw, cache_file)
                    finally:
                        raw.unlink(missing_ok=True)
        with self._dur_lock:
            dur = self.durations.get(key)
        if dur is None:
            dur = self._duration(cache_file)
            with self._dur_lock:
                self.durations[key] = dur
        return dur, cache_file

    def synthesize(self, seg: dict, cancel=None) -> tuple[float, Path]:
        """Return (duration s after trim, cache path) for this segment's text."""
        text = (seg.get("vi") or "").strip()
        if not text:
            raise RuntimeError(f"segment id={seg.get('id')} has no Vietnamese text")
        if cancel is not None:
            self.cancel = cancel
        voice = seg.get("tts_voice") or self.default_voice
        return self.synthesize_text(text, voice)


# ------------------------------------------------------------------ text split & WAV join


def _split_text_chunks(text: str, max_chars: int) -> list[str]:
    """Split at sentence boundaries into pieces of at most `max_chars`
    characters (floor 25); over-long sentences split by commas, then words."""
    max_chars = max(25, int(max_chars))

    def hard_split(part: str) -> list[str]:
        words = part.split()
        chunks: list[str] = []
        line = ""
        for word in words:
            if line and len(line) + 1 + len(word) > max_chars:
                chunks.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            chunks.append(line)
        return chunks or [part]

    def pack(part: str) -> list[str]:
        if len(part) <= max_chars:
            return [part]
        subs = [s.strip() for s in re.split(r"(?<=[,;:])\s+", part) if s.strip()]
        out: list[str] = []
        buf = ""
        for sub in subs:
            if len(sub) > max_chars:
                out.extend(hard_split(sub))
                continue
            if buf and len(buf) + 1 + len(sub) > max_chars:
                out.append(buf)
                buf = sub
            else:
                buf = f"{buf} {sub}".strip()
        if buf:
            out.append(buf)
        return out

    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    if not sentences:
        sentences = [text.strip()]
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        for piece in pack(sent):
            if buf and len(buf) + 1 + len(piece) > max_chars:
                chunks.append(buf)
                buf = piece
            else:
                buf = f"{buf} {piece}".strip()
    if buf:
        chunks.append(buf)
    return chunks


def _join_pieces(parts: list[Path], out_path: Path, silence_s: float) -> None:
    """Concatenate WAV pieces at their native sample rate (48 kHz or 24 kHz)
    with a brief silence pad between parts using soundfile + numpy."""
    import numpy as np
    import soundfile as sf

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    arrays: list[np.ndarray] = []
    target_sr: int | None = None

    for idx, part in enumerate(parts):
        data, sr = sf.read(str(part), dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        arr = np.asarray(data, dtype=np.float32).reshape(-1)
        if target_sr is None:
            target_sr = int(sr)
        elif int(sr) != target_sr and arr.size > 0:
            # Linear resample fallback if mixed rates ever occur.
            new_len = max(1, int(round(arr.size * target_sr / float(sr))))
            xp = np.linspace(0.0, 1.0, arr.size, endpoint=True)
            xnew = np.linspace(0.0, 1.0, new_len, endpoint=True)
            arr = np.interp(xnew, xp, arr).astype(np.float32)
        if idx > 0 and silence_s > 0 and target_sr:
            pad = int(round(max(0.0, float(silence_s)) * target_sr))
            if pad > 0:
                arrays.append(np.zeros(pad, dtype=np.float32))
        arrays.append(arr)

    if not arrays or target_sr is None:
        raise RuntimeError("cannot join empty audio parts")

    joined = np.clip(np.concatenate(arrays), -1.0, 1.0)
    tmp = out_path.parent / f"~join-{uuid.uuid4().hex}.wav"
    try:
        sf.write(str(tmp), joined, target_sr, subtype="PCM_16")
        os_replace_strong(tmp, out_path)
    finally:
        tmp.unlink(missing_ok=True)


def _panel_text(key: str, fallback: str, **kw) -> str:
    try:
        from dubtool.app.i18n import tr

        text = tr(key, **kw)
        if text and text != key:
            return text
    except Exception:
        pass
    return fallback.format(**kw)


# ------------------------------------------------------------------ state & faults


def _state_path(job) -> Path:
    cached = getattr(job, "state_path", None)
    if cached is not None:
        return cached
    return job.dir / "state.json"


def _state_voice(job) -> str | None:
    try:
        state = read_json(_state_path(job))
        voice = state.get("voice") if isinstance(state, dict) else None
        return str(voice) if voice else None
    except Exception:
        return None


def _set_state_voice(job, voice: str, *, mode: str = DEFAULT_TTS_MODE) -> None:
    state: dict = {}
    try:
        loaded = read_json(_state_path(job))
        if isinstance(loaded, dict):
            state = loaded
    except Exception:
        state = {}
    state["voice"] = voice
    state["tts_mode"] = mode
    write_json(_state_path(job), state)


def _fault_spec():
    """Debug-only fault injection (--fault tts:fail-first=N / tts:refuse-ids=...)."""
    from dubtool import faults as faults_mod

    spec = faults_mod.get("tts")
    if not spec:
        return None, set()
    faults_mod.announce()

    refuse_ids = {int(v) for v in (spec.get("refuse-ids") or [])}
    fail_first = int(spec.get("fail-first", 0) or 0)
    if fail_first <= 0:
        return None, refuse_ids

    class _FaultProvider:
        def __init__(self, inner):
            self.inner = inner
            self.calls = 0
            self.cancel = None

        def synthesize(self, text, voice, out_path):
            self.calls += 1
            if self.calls <= fail_first:
                raise TTSFailure(f"injected fault tts:fail-first ({self.calls}/{fail_first})")
            if hasattr(self.inner, "cancel"):
                self.inner.cancel = self.cancel
            return self.inner.synthesize(text, voice, out_path)

        def close(self):
            if hasattr(self.inner, "close"):
                self.inner.close()

    return _FaultProvider, refuse_ids


def _entries_source(job, config, resolved_voice: str):
    """Select sentence dub units (default) or legacy per-segment entries."""
    from dubtool.dub_units import granularity_for_job, load_dub_units

    mode = granularity_for_job(job, config)
    if mode == "sentence":
        records = load_dub_units(job)
        if not records:
            log.warning(
                "s4: sentence mode requested but dub_units.json is missing — "
                "falling back to segment mode for this run"
            )
            mode = "segment"
        else:
            for rec in records:
                rec.setdefault("id", int(rec["unit_id"]))
                rec["tts_voice"] = resolve_voice(
                    rec.get("tts_voice") or resolved_voice,
                    (getattr(config, "tts", {}) or {}).get("mode", DEFAULT_TTS_MODE),
                )

            def persist_sentence() -> None:
                from dubtool.dub_units import write_dub_units

                write_dub_units(job, records)

            return mode, records, persist_sentence

    entries = read_json(job.translated_path)
    for rec in entries:
        if rec.get("tts_voice"):
            rec["tts_voice"] = resolve_voice(
                rec["tts_voice"],
                (getattr(config, "tts", {}) or {}).get("mode", DEFAULT_TTS_MODE),
            )

    def persist_segment() -> None:
        write_json(job.translated_path, entries)

    return "segment", entries, persist_segment


def _synthesize_entry(
    tts: SegmentTTS,
    entry: dict,
    *,
    voice: str,
    tts_mode: str,
    max_chars: int,
    silence_s: float,
    direct_retries: int,
    failures: TTSFailureLog,
    refuse_ids: set[int],
    cancel=None,
) -> tuple[float, Path] | None:
    """Synthesize one entry (direct -> split if long -> bounded local retry)."""
    seg_id = int(entry["id"])
    text = (entry.get("vi") or "").strip()
    via_full = hasattr(tts, "synthesize_text")

    steps = [STEP_DIRECT]
    if len(text) > max_chars and via_full:
        steps.append(STEP_SPLIT)

    attempt_no = 0
    for step in steps:
        tries = (1 + direct_retries) if step == STEP_DIRECT else 1
        for _ in range(tries):
            if cancel is not None and cancel.is_set():
                raise StageCancelled(f"s4: cancelled by user at segment {seg_id}")
            attempt_no += 1
            timer = Timer()
            if seg_id in refuse_ids:
                failures.record(
                    seg_id,
                    attempt_no,
                    text,
                    TTSFailure("injected fault tts:refuse-ids"),
                    timer.elapsed(),
                    voice=voice,
                    mode=tts_mode,
                    details={"step": step},
                )
                break
            try:
                if step == STEP_SPLIT:
                    chunks = _split_text_chunks(text, max_chars)
                    if len(chunks) == 1:
                        return tts.synthesize_text(chunks[0], voice)
                    parts: list[Path] = []
                    for chunk in chunks:
                        if cancel is not None and cancel.is_set():
                            raise StageCancelled(f"s4: cancelled by user at segment {seg_id}")
                        _d, part_path = tts.synthesize_text(chunk, voice)
                        parts.append(part_path)
                    token = hashlib.sha256(
                        "\x00".join(str(p) for p in parts).encode("utf-8")
                    ).hexdigest()[:16]
                    joined_path = tts.cache_dir / f"join-{token}.wav"
                    if not tts._cache_valid(joined_path):
                        _join_pieces(parts, joined_path, silence_s)
                    return tts._duration(joined_path), joined_path
                if via_full:
                    return tts.synthesize_text(text, voice)
                return tts.synthesize(entry, cancel)
            except StageCancelled:
                raise
            except TTSFailure as exc:
                failures.record(
                    seg_id,
                    attempt_no,
                    text,
                    exc,
                    timer.elapsed(),
                    voice=voice,
                    mode=tts_mode,
                    details={"step": step},
                )
                continue
    return None


def run(job, config, force: bool = False, cancel=None) -> None:
    """Synthesize all Vietnamese clips locally using VieNeu-TTS v3 ONNX."""
    from dubtool.dub_units import file_base

    audio_utils.require_ffmpeg()
    media_cache = MediaCache(job.dir)

    tts_cfg = getattr(config, "tts", {}) or {}
    tts_mode = normalize_mode(tts_cfg.get("mode", DEFAULT_TTS_MODE))
    configured_voice = resolve_voice(getattr(config, "tts_voice", DEFAULT_VOICE), tts_mode)
    # Honor configured voice when explicitly changed; otherwise keep job voice.
    prior_voice = _state_voice(job)
    video_voice = resolve_voice(configured_voice or prior_voice, tts_mode)
    _set_state_voice(job, video_voice, mode=tts_mode)

    granularity, entries, persist = _entries_source(job, config, video_voice)
    cache_dir = job.tts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = TTSFailureLog(job.dir)

    tts = build_tts(config, cache_dir, media_cache=media_cache)
    tts.cancel = cancel
    provider = getattr(tts, "provider", None)
    if provider is not None and hasattr(provider, "cancel"):
        provider.cancel = cancel

    fault_factory, refuse_ids = _fault_spec()
    if fault_factory is not None and provider is not None:
        tts.provider = fault_factory(provider)

    max_chars = max(25, int(tts_cfg.get("max_chars_per_request", 200)))
    silence_s = float(tts_cfg.get("join_silence_s", 0.08))
    direct_retries = max(0, int(tts_cfg.get("direct_retries", 1)))

    def out_clip(entry) -> Path:
        return tts_clip_path(job.tts_dir, file_base(entry, granularity))

    def clip_ok(entry) -> bool:
        if entry.get("tts_duration") is None:
            return False
        existing = existing_tts_clip_path(job.tts_dir, file_base(entry, granularity))
        if existing is None:
            return False
        return tts._clip_readable(existing) or tts._cache_valid(existing)

    playable = [e for e in entries if (e.get("vi") or "").strip()]
    total_count = len(playable)
    todo = [e for e in playable if force or not clip_ok(e)]
    already_done = total_count - len(todo)
    generated = 0
    failed_ids: list[int] = []
    started = time.monotonic()

    log.info(
        "s4: offline VieNeu-TTS mode=%s voice=%s (%d to synthesize, %d cached)",
        tts_mode, video_voice, len(todo), already_done,
    )

    def _emit_row(done_n: int, failed_n: int) -> None:
        if failed_n > 0:
            heartbeat.emit(_panel_text(
                "tts.stage_row_failed",
                "Generating Vietnamese voice — {done}/{total} done ({failed} failed)",
                done=done_n, total=total_count, failed=failed_n,
            ))
        else:
            heartbeat.emit(_panel_text(
                "tts.stage_row",
                "Generating Vietnamese voice — {done}/{total} done",
                done=done_n, total=total_count,
            ))

    try:
        if already_done > 0:
            log.info("s4: progress %d/%d", already_done, total_count)
            _emit_row(already_done, 0)

        for entry in todo:
            if cancel is not None and cancel.is_set():
                raise StageCancelled(f"s4: cancelled by user at segment {entry['id']}")
            voice = resolve_voice(entry.get("tts_voice") or video_voice, tts_mode)
            entry["tts_voice"] = voice
            result = _synthesize_entry(
                tts,
                entry,
                voice=voice,
                tts_mode=tts_mode,
                max_chars=max_chars,
                silence_s=silence_s,
                direct_retries=direct_retries,
                failures=failures,
                refuse_ids=refuse_ids,
                cancel=cancel,
            )
            if result is not None:
                duration, cache_path = result
                dest = out_clip(entry)
                shutil.copyfile(str(cache_path), str(dest))
                # Clean legacy mp3 next to fresh wav so s5 always picks wav.
                legacy_mp3 = job.tts_dir / f"{file_base(entry, granularity)}.mp3"
                if legacy_mp3.exists() and legacy_mp3 != dest:
                    legacy_mp3.unlink(missing_ok=True)
                entry["tts_duration"] = round(duration, 3)
                entry["fit_status"] = "pending"
                entry["voice"] = voice
                generated += 1
            else:
                failed_ids.append(int(entry["id"]))

            done_now = already_done + generated
            log.info("s4: progress %d/%d", done_now, total_count)
            _emit_row(done_now, len(failed_ids))
            persist()
    finally:
        active_provider = getattr(tts, "provider", None)
        if active_provider is not None and hasattr(active_provider, "close"):
            try:
                active_provider.close()
            except Exception:
                pass

    persist()
    elapsed = time.monotonic() - started
    if failed_ids:
        raise TTSUnavailableError(sorted(set(failed_ids)), already_done + generated)

    heartbeat.emit(_panel_text(
        "tts.voice_summary",
        "Voice: {voice} ({mode})",
        voice=video_voice, mode=tts_mode,
    ))
    log.info(
        "s4: all %d clips ready (%d generated, %d cached, voice=%s, mode=%s, %.1fs total)",
        total_count, generated, already_done, video_voice, tts_mode, elapsed,
    )
    log.info(failures.summary([e.get("vi") or "" for e in entries]))


def regenerate_tts(job, config, ids: list[int], force: bool = True) -> None:
    """`retts`: re-synthesize the given entry ids (after manual text edits)."""
    from dubtool.dub_units import file_base, granularity_for_job, load_dub_units

    audio_utils.require_ffmpeg()
    tts_cfg = getattr(config, "tts", {}) or {}
    tts_mode = normalize_mode(tts_cfg.get("mode", DEFAULT_TTS_MODE))
    mode = granularity_for_job(job, config)
    records = None
    if mode == "sentence":
        records = load_dub_units(job)
        if not records:
            log.warning(
                "retts: sentence mode requested but dub_units.json is missing "
                "— falling back to segment mode"
            )
            mode = "segment"
        else:
            for rec in records:
                rec.setdefault("id", int(rec["unit_id"]))
            entries = records
    else:
        entries = read_json(job.translated_path)

    cache_dir = job.tts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = TTSFailureLog(job.dir)
    tts = build_tts(config, cache_dir)
    id_set = {int(i) for i in ids}
    regenerated = 0
    try:
        for entry in entries:
            if entry["id"] not in id_set:
                continue
            if not entry.get("vi"):
                raise RuntimeError(f"retts: entry id={entry['id']} has no Vietnamese text")
            voice = resolve_voice(
                entry.get("tts_voice") or getattr(config, "tts_voice", DEFAULT_VOICE),
                tts_mode,
            )
            entry["tts_voice"] = voice
            timer = Timer()
            try:
                duration, cache_path = tts.synthesize(entry)
            except TTSFailure as exc:
                failures.record(
                    entry["id"], 1, entry["vi"], exc, timer.elapsed(),
                    voice=voice, mode=tts_mode,
                )
                raise
            dest = tts_clip_path(job.tts_dir, file_base(entry, mode))
            shutil.copyfile(str(cache_path), str(dest))
            legacy_mp3 = job.tts_dir / f"{file_base(entry, mode)}.mp3"
            if legacy_mp3.exists():
                legacy_mp3.unlink(missing_ok=True)
            entry["tts_duration"] = round(duration, 3)
            entry["fit_status"] = "pending"
            entry["voice"] = voice
            regenerated += 1
            if records is None:
                write_json(job.translated_path, entries)
            else:
                from dubtool.dub_units import write_dub_units

                write_dub_units(job, records)
    finally:
        if hasattr(tts.provider, "close"):
            try:
                tts.provider.close()
            except Exception:
                pass

    for entry_id in id_set:
        base = "u" if records is not None else ""
        stale = job.fitted_dir / f"{base}{entry_id}.wav"
        if stale.exists():
            stale.unlink()
    if regenerated != len(id_set):
        missing = sorted(id_set - {e["id"] for e in entries})
        log.warning(
            "retts: ids not found in %s: %s",
            "dub_units.json" if records is not None else "translated.json",
            missing,
        )
    log.info("retts: regenerated %d clips", regenerated)
