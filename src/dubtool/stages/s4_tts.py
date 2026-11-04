"""Stage 4: synthesize Vietnamese clips per segment, cached by (text, voice).

Canonical audio lives in tts/cache/<hash>.mp3 keyed by (text, voice, rate,
silence-trim settings — AGENT_UPDATE_3.md task 2). Durations are measured per
sampling so unchanged lines are never regenerated (idempotent, resumable).
When trimming is on, the cache stores the *trimmed* audio and
`entry["tts_duration"]` is measured after the trim.

AGENT_UPDATE_11 (tasks 3.1-3.4):
- EdgeTTSProvider performs exactly ONE network attempt per call under a hard
  per-request cap (`tts.request_timeout_s`, default 45 s). The old
  5-attempt exponential-backoff ladder inside the provider could keep a
  refused segment busy for ~80 s on its own; retries belong to the stage.
- Per segment the stage runs a bounded repair ladder:
      direct -> sentence split -> alternate voice -> alternate voice + split
  with small waits between steps (`tts.attempt_delays_s`, default 2/5 s),
  all counted against a per-segment budget (`tts.unit_budget_s`, 60 s). A
  segment that burns its budget is deferred to a retry round instead of
  blocking the pending queue; already-synthesized split parts stay cached.
- Deferred retry rounds (`tts.retry_rounds`) start after SHORT cooldowns
  (`tts.round_cooldown_s`, 30/60/120 s). After
  `tts.breaker_consecutive` segments exhaust the whole ladder the pass
  pauses for `tts.breaker_pause_s` (default 60, bounded) and continues.
- Still-missing segments trigger the budget decision (`tts.budget_policy`):
  in the GUI a dialog asks wait-10-min / backup voice / stop
  (config.tts_budget_dialog); headless runs use the configured backup voice
  automatically, otherwise stop — never an unbounded silent wait.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from pathlib import Path

from dubtool import audio_utils, heartbeat
from dubtool.audio_utils import MediaCache
from dubtool.job import read_json, write_json
from dubtool.tts.base import TTSFailure
from dubtool.tts_failures import TTSFailureLog, Timer

log = logging.getLogger("dubtool")

# AGU11: step names recorded in failures.json ("step" field).
STEP_DIRECT = "direct"
STEP_SPLIT = "split"
STEP_ALT = "alt_voice"
STEP_ALT_SPLIT = "alt_split"
_BREAKER_PAUSE_S = 60.0     # default bounded pause when the breaker trips
_WAIT10_S = 600.0           # the budget dialog's gated wait


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
            max_cooldown_s=min(float(tts_cfg.get("max_cooldown_s", 120.0)), 30.0),
            request_timeout_s=float(tts_cfg.get("request_timeout_s", 45.0)),
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
        return self._key(voice, text)

    def _key(self, voice: str, text: str) -> str:
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

    def synthesize_text(self, text: str, voice: str) -> tuple[float, Path]:
        """One (text, voice) synthesis into the content cache.

        AGU11: the split steps share the same cache discipline, so parts
        synthesized once are reused on every later attempt.
        """
        text = (text or "").strip()
        if not text:
            raise RuntimeError("refusing to synthesize empty text")
        key = self._key(voice, text)
        cache_file = self.cache_dir / f"{key}.mp3"
        if not self._cache_valid(cache_file):
            raw = self.cache_dir / f"~raw-{uuid.uuid4().hex}.mp3"
            try:
                self.provider.synthesize(text, voice, raw)
                trim = self.options.get("trim")
                if trim:
                    tmp = self.cache_dir / f"~trim-{uuid.uuid4().hex}.mp3"
                    try:
                        audio_utils.trim_silence(
                            raw, tmp, threshold_db=trim["threshold_db"], margin_s=trim["margin_s"],
                            cancel=self.cancel,
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
        cache_file = self.cache_dir / f"{key}.mp3"
        if key not in self.durations:
            self.durations[key] = self._duration(cache_file)
        return self.durations[key], cache_file

    def synthesize(self, seg: dict, cancel=None) -> tuple[float, Path]:
        """Return (duration s after trim, cache path) for this segment's text."""
        text = (seg.get("vi") or "").strip()
        if not text:
            raise RuntimeError(f"segment id={seg.get('id')} has no Vietnamese text")
        if cancel is not None:
            self.cancel = cancel
        voice = seg.get("tts_voice") or self.default_voice
        return self.synthesize_text(text, voice)


def os_replace_strong(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    src.replace(dst)


# ------------------------------------------------------------------ ladder (AGU11 3.3)


def _split_text_chunks(text: str, max_chars: int) -> list[str]:
    """Split at sentence boundaries into pieces of at most `max_chars`
    characters (floor 25); over-long sentences are split by commas, then by
    words. A short text degenerates to [text]."""
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
    """Concatenate split-step pieces into one cache mp3 with a small piece
    of silence between parts (ffmpeg concat demuxer, stream copy)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = audio_utils.ffmpeg_bin()
    from dubtool import proc

    list_file = out_path.parent / f"~concat-{uuid.uuid4().hex}.txt"
    silence = out_path.parent / f"~silence-{uuid.uuid4().hex}.mp3"
    try:
        if silence_s > 0:
            result = proc.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", f"{max(0.0, silence_s):.3f}", "-q:a", "9",
                    str(silence),
                ],
                capture=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed generating silence: {result.returncode}")
        lines: list[str] = []
        for i, part in enumerate(parts):
            if i > 0 and silence_s > 0:
                lines.append(f"file '{silence.as_posix()}'")
            lines.append(f"file '{part.as_posix()}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")
        result = proc.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(out_path),
            ],
            capture=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.returncode}")
    finally:
        list_file.unlink(missing_ok=True)
        silence.unlink(missing_ok=True)


def _panel_text(key: str, fallback: str, **kw) -> str:
    """Resolve a heartbeat line into the active language; falls back to the
    English template when the i18n registry is unavailable (CLI/test runs)."""
    try:
        from dubtool.app.i18n import tr

        text = tr(key, **kw)
        if text and text != key:
            return text
    except Exception:
        pass
    return fallback.format(**kw)


def _step_label(step: str) -> str:
    names = {
        STEP_DIRECT: ("tts.step_retry", "a direct retry"),
        STEP_SPLIT: ("tts.step_words", "sentence splitting"),
        STEP_ALT: ("tts.step_alt_voice", "the alternate voice"),
        STEP_ALT_SPLIT: ("tts.step_alt_voice", "the alternate voice with sentence splitting"),
    }
    key, fallback = names.get(step, ("tts.step_retry", "a retry"))
    return _panel_text(key, fallback)


def _fail_rate(config) -> str:
    """The edge-tts rate string that gets logged with every failure."""
    tts_cfg = getattr(config, "tts", {}) or {}
    return str(tts_cfg.get("rate", getattr(config, "tts_rate", "+0%")))


class _Ladder:
    """The per-segment repair ladder (AGENT_UPDATE_11 3.3)."""

    def __init__(self, tts: "SegmentTTS", failures: TTSFailureLog, config, cancel=None):
        tts_cfg = getattr(config, "tts", {}) or {}
        self.tts = tts
        self.failures = failures
        self.config = config
        self.cancel = cancel
        self.primary_voice = str(getattr(config, "tts_voice", "vi-VN-HoaiMyNeural"))
        self.alt_voice = str(tts_cfg.get("alt_voice", "") or "")
        self.delays = [float(v) for v in (tts_cfg.get("attempt_delays_s") or [2.0, 5.0])]
        self.max_chars = max(25, int(tts_cfg.get("max_chars_per_request", 90)))
        self.silence_s = float(tts_cfg.get("join_silence_s", 0.08))
        self.unit_budget_s = float(tts_cfg.get("unit_budget_s", 60.0))
        self.refuse_ids: set[int] = set()   # fault injection (AGU11)
        self.tried: dict[int, set[str]] = {}  # step names already attempted per segment
        self.attempt_no: dict[int, int] = {}

    def steps(self, base_voice: str) -> list[tuple[str, str]]:
        steps = [(STEP_DIRECT, base_voice), (STEP_SPLIT, base_voice)]
        if self.alt_voice and self.alt_voice != base_voice:
            steps += [(STEP_ALT, self.alt_voice), (STEP_ALT_SPLIT, self.alt_voice)]
        return steps

    def _record(self, seg_id: int, text: str, exc: Exception, elapsed: float,
                step: str, voice: str) -> None:
        self.attempt_no[seg_id] = self.attempt_no.get(seg_id, 0) + 1
        self.failures.record(
            seg_id,
            self.attempt_no[seg_id],
            text,
            exc,
            elapsed,
            voice=voice,
            rate=_fail_rate(self.config),
            details={"step": step},
        )

    def _sleep_between_steps(self, seconds: float, seg_id: int, step: str) -> float:
        """A bounded, visible wait before the next repair step. Returns the
        actual slept seconds so the segment budget can exclude them."""
        if seconds <= 0:
            return 0.0

        def text(remaining: float) -> str:
            return _panel_text(
                "tts.cooldown",
                "The voice service refused segment {id} — retrying with {step} in {n} s",
                id=seg_id, step=_step_label(step), n=int(remaining + 0.999),
            )

        return heartbeat.sleeping(seconds, text, cancel=self.cancel)

    def attempt(self, entry: dict, budget_s: float | None = None):
        """Run the ladder for one entry.

        Returns (state, duration, cache_path) with state
          "done"      — duration/cache_path are set (success this attempt),
          "deferred"  — the segment budget ran out mid-ladder (retry later),
          "exhausted" — every step failed (goes to the retry rounds).
        Every failed step is recorded in failures.json with its step name."""
        import time as _time

        seg_id = int(entry["id"])
        base_voice = entry.get("tts_voice") or self.primary_voice
        text = (entry.get("vi") or "").strip()
        attempted = self.tried.setdefault(seg_id, set())
        budget = self.unit_budget_s if budget_s is None else float(budget_s)
        deadline = _time.monotonic() + budget
        step_list = self.steps(base_voice)
        state = "exhausted"
        slept_here = 0.0  # programmed waits do not count against the budget
        for index, (step, step_voice) in enumerate(step_list):
            if step in attempted:
                continue
            if attempted or index > 0:
                # bound the in-step wait by both the config and the budget
                remaining = deadline - _time.monotonic() + slept_here
                if remaining <= 0:
                    return "deferred", None, None
                delay = self.delays[min(index, len(self.delays) - 1)]
                slept_here += self._sleep_between_steps(min(delay, remaining), seg_id, step)
            remaining = deadline - _time.monotonic() + slept_here
            if remaining <= 0:
                return "deferred", None, None
            if self.cancel is not None and self.cancel.is_set():
                return "deferred", None, None  # the pass loop re-raises with context
            if seg_id in self.refuse_ids and step_voice == base_voice:
                # AGU11 3.6: fault `tts:refuse-ids=…` — the service refuses
                # this text with the primary voice on every attempt; the
                # alternate-voice steps below are the repair path.
                timer = Timer()
                self._record(
                    seg_id, text,
                    TTSFailure("injected fault tts:refuse-ids (service refuses this text + voice)"),
                    timer.elapsed(), step, step_voice,
                )
                attempted.add(step)
                continue
            self.attempt_no[seg_id] = self.attempt_no.get(seg_id, 0) + 1
            timer = Timer()
            try:
                duration, cache_path = self._synthesize_step(entry, text, step_voice, step)
            except TTSFailure as exc:
                self._record(seg_id, text, exc, timer.elapsed(), step, step_voice)
                attempted.add(step)
                continue
            return "done", duration, cache_path
        return state, None, None

    def _synthesize_step(self, entry: dict, text: str, voice: str, step: str) -> tuple[float, Path]:
        """One repair step. Stage-level fakes that only offer the old
        `synthesize(entry)` API are served by it for the direct step and
        reported as unusable for the split steps (no parts to render)."""
        via_full = hasattr(self.tts, "synthesize_text")

        if step in (STEP_SPLIT, STEP_ALT_SPLIT):
            if not via_full:
                raise TTSFailure("this synthesis target cannot render split parts")
            chunks = _split_text_chunks(text, self.max_chars)
            if len(chunks) == 1:
                return self.tts.synthesize_text(chunks[0], voice)
            parts: list[Path] = []
            for chunk in chunks:
                _d, part_path = self.tts.synthesize_text(chunk, voice)
                parts.append(part_path)
            token = hashlib.sha256(
                "\x00".join(str(p) for p in parts).encode("utf-8")
            ).hexdigest()[:16]
            cache_path = self.tts.cache_dir / f"join-{token}.mp3"
            if not self.tts._cache_valid(cache_path):
                tmp = self.tts.cache_dir / f"{cache_path.name}~.mp3"
                _join_pieces(parts, tmp, self.silence_s)
                os_replace_strong(tmp, cache_path)
            duration = self.tts._duration(cache_path)
            return duration, cache_path
        if via_full:
            return self.tts.synthesize_text(text, voice)
        return self.tts.synthesize(entry, self.cancel)


# ------------------------------------------------------------------ faults (AGU8 2.5 + AGU11)


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


def _fault_spec(config):
    """Debug-only fault injection (AGU8 2.5; AGU11 extends the kinds):
    --fault tts:fail-first=N (first N provider calls fail),
           tts:hang=N (first N provider calls hang 90 s — a real service
                       hang would be cut by the 45-s request hard cap),
           tts:refuse-ids=4,12,... (these ids fail every primary-voice
                       attempt; the alternate-voice steps can still recover)
    Returns (provider_factory_or_None, refuse_ids)."""
    from dubtool import faults as faults_mod

    spec = faults_mod.get("tts")
    if not spec:
        return None, set()
    faults_mod.announce()

    refuse_ids = {int(v) for v in (spec.get("refuse-ids") or [])}
    fail_first = int(spec.get("fail-first", 0) or 0)
    hang_n = int(spec.get("hang", 0) or 0)
    if fail_first <= 0 and hang_n <= 0:
        return None, refuse_ids

    class _FaultProvider:
        def __init__(self, inner):
            self.inner = inner
            self.calls = 0

        def synthesize(self, text, voice, out_path):
            self.calls += 1
            if 0 < fail_first and self.calls <= fail_first:
                raise TTSFailure(
                    f"injected fault tts:fail-first ({self.calls}/{fail_first})"
                )
            if 0 < hang_n and self.calls <= fail_first + hang_n:
                import time as _t

                _t.sleep(90.0)  # > the 45-s hard cap (see tests/test_agu11_tts.py)
                raise TTSFailure("injected fault tts:hang (sleep ended)")
            return self.inner.synthesize(text, voice, out_path)

    return _FaultProvider, refuse_ids


def _budget_decision(config, missing_count: int, waited_total_s: float) -> str:
    """AGU11 3.4: what to do when segments are still missing after the
    rounds — wait 10 more minutes / use the backup voice / stop."""
    from dubtool.audio_utils import format_mmss
    from dubtool.tts.fallback import build_backup

    tts_cfg = getattr(config, "tts", {}) or {}
    dialog = getattr(config, "tts_budget_dialog", None)
    if dialog is not None:
        try:
            fallback_ok = build_backup(config) is not None
        except Exception:
            fallback_ok = False
        try:
            choice = str(dialog(missing_count, format_mmss(waited_total_s), fallback_ok) or "")
        except Exception as exc:
            log.warning("tts budget dialog failed (%s) — stopping", exc)
            return "stop"
        choice = choice.strip().lower()
        if choice in ("wait10", "fallback", "stop"):
            return choice
        log.warning("tts budget dialog returned an unknown choice %r — stopping", choice)
        return "stop"
    # Headless/API run: no dialog. Conservative automatic policy: use the
    # configured backup voice when it exists, otherwise stop.
    mode = str(tts_cfg.get("fallback", "none")).lower()
    if mode not in ("", "none"):
        try:
            if build_backup(config) is not None:
                log.info(
                    "tts: budget policy — %d segment(s) still missing, using the "
                    "backup voice (headless run, tts.budget_policy=ask unusable)",
                    missing_count,
                )
                return "fallback"
        except Exception:
            pass
    log.info(
        "tts: budget policy — %d segment(s) still missing, stopping "
        "(TTSUnavailableError); the finished clips are preserved",
        missing_count,
    )
    return "stop"


def run(job, config, force: bool = False, cancel=None) -> None:
    """A failed segment never aborts the stage (AGU8 2.1 + AGU11 3.3).

    Flow: a bounded repair ladder per segment (first pass) -> deferred retry
    rounds after short cooldowns -> the budget decision (wait 10 min /
    backup voice / stop) -> TTSUnavailableError only when nothing can cover
    the missing segments. Every finished clip is preserved for Resume and
    every failed step is recorded with context in tts_failures.json."""

    import time as _time

    from dubtool.job import StageCancelled
    from dubtool.tts.base import TTSUnavailableError
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
    fault_factory, refuse_ids = _fault_spec(config)
    if fault_factory is not None and provider is not None:
        tts.provider = fault_factory(provider)
    ladder = _Ladder(tts, failures, config, cancel=cancel)
    ladder.refuse_ids = refuse_ids

    def clip_ok(entry) -> bool:
        if entry.get("tts_duration") is not None:
            existing = job.tts_dir / f"{entry['id']}.mp3"
            if existing.exists() and (
                tts._clip_readable(existing) or tts._cache_valid(existing)
            ):
                return True
        return False

    def still_missing() -> list:
        return [e for e in entries if e.get("vi") and (e in idle) and not clip_ok(e)]

    all_texts = [e.get("vi") or "" for e in entries]

    idle = [e for e in entries if e.get("vi") and (force or not clip_ok(e))]
    finished_count = len(entries) - len(idle)
    breaker_at = max(1, int(tts_cfg.get("breaker_consecutive", 3)))
    breaker_pause_s = float(tts_cfg.get("breaker_pause_s", _BREAKER_PAUSE_S))
    rounds = max(0, int(tts_cfg.get("retry_rounds", 3)))
    cooldowns = [float(c) for c in (tts_cfg.get("round_cooldown_s") or [30.0, 60.0, 120.0])]
    stage_budget_s = float(tts_cfg.get("stage_budget_s", 600.0))
    budget_policy = str(tts_cfg.get("budget_policy", "ask")).lower()
    state_box: dict = {}
    fallback_uses = 0
    used_rounds = 0
    started = _time.monotonic()

    def heartbeat_row(round_no: int, rounds_total: int) -> None:
        missing = still_missing()
        heartbeat.emit(_panel_text(
            "tts.stage_row",
            "Voice generation — {done}/{total} done, {pending} retrying (round {round}/{rounds})",
            done=len(entries) - len(missing), total=len(entries),
            pending=len(missing), round=round_no, rounds=rounds_total,
        ))

    def seg_pass(entries_list, round_no: int, rounds_total: int) -> None:
        """One pass over the given segments with the shared breaker logic."""
        nonlocal finished_count, fallback_uses

        def _apply(entry, duration, cache_path) -> None:
            shutil.copyfile(str(cache_path), str(job.tts_dir / f"{entry['id']}.mp3"))
            entry["tts_duration"] = round(duration, 3)
            entry["fit_status"] = "pending"

        consecutive = 0  # a fresh pass starts with the breaker trust reset
        for entry in entries_list:
            if cancel is not None and cancel.is_set():
                raise StageCancelled(f"s4: cancelled by user at segment {entry['id']}")
            if clip_ok(entry):
                continue
            heartbeat_row(round_no, rounds_total)
            state, duration, cache_path = ladder.attempt(entry)
            if state == "done":
                _apply(entry, duration, cache_path)
                finished_count += 1
                consecutive = 0
            else:
                consecutive += 1
                if consecutive >= breaker_at:
                    log.warning(
                        "s4: circuit breaker paused the pass after %d segment(s) "
                        "exhausted the ladder — %.0f s bounded pause, then it continues",
                        consecutive, breaker_pause_s,
                    )
                    heartbeat.sleeping(
                        breaker_pause_s,
                        lambda remaining: _panel_text(
                            "tts.breaker",
                            "The voice service is not responding — pause {n} s, then continue",
                            n=int(remaining + 0.999),
                        ),
                        cancel=cancel,
                    )
                    consecutive = 0
            log.info("s4: progress %d/%d", position(entry), len(entries))
            heartbeat_row(round_no, rounds_total)
            write_json(job.translated_path, entries)

    def position(entry) -> int:
        for i, e in enumerate(entries, start=1):
            if e is entry:
                return i
        return len(entries)

    log.info(
        "s4: tts budget policy %s — stage wait budget %.0f s, unit budget %.0f s/segment",
        budget_policy, stage_budget_s, float(tts_cfg.get("unit_budget_s", 60.0)),
    )

    # ------------------------------------------------------ first pass
    seg_pass(idle, 1, rounds + 1)

    # --------------------------------------------- deferred retry rounds
    for round_number in range(rounds):
        missing = still_missing()
        if not missing:
            break
        if _time.monotonic() - started >= stage_budget_s:
            # AGU11 3.4: once the stage wait budget is consumed the deferred
            # rounds do not stretch it silently — the budget decision takes
            # over (wait 10 min / backup voice / stop).
            log.info(
                "s4: stage wait budget (%.0f s) reached — the rounds stop and "
                "the budget decision takes over", stage_budget_s,
            )
            heartbeat.emit(_panel_text(
                "tts.stage_budget",
                "The voice stage used its waiting budget ({n} s) — deciding the next step",
                n=int(stage_budget_s),
            ))
            break
        used_rounds = round_number + 1
        cooldown = cooldowns[round_number] if round_number < len(cooldowns) else cooldowns[-1]
        log.info("s4: retry round %d/%d in %.0f s", round_number + 1, rounds, cooldown)
        heartbeat.sleeping(
            cooldown,
            lambda remaining: _panel_text(
                "tts.round_wait",
                "The voice service is busy — retrying the missing segments in {n} s",
                n=int(remaining + 0.999),
            ),
            cancel=cancel,
        )
        for entry in still_missing():
            # AGU11: after the service cooldown every repair step is open
            # again — a round is a fresh ladder, not a rerun of the same
            # already-tried steps. Cached split parts still get reused.
            ladder.tried.pop(int(entry["id"]), None)
        seg_pass(missing, round_number + 1, rounds + 1)

    # ----------------------------------------------------- budget decision
    missing_final = still_missing()
    if missing_final:
        waited_total = _time.monotonic() - started
        decision = _budget_decision(config, len(missing_final), waited_total)
        if decision == "wait10":
            log.info("tts: waiting %.0f s (gated by the budget dialog), then one quick retry pass", _WAIT10_S)
            heartbeat.sleeping(
                _WAIT10_S,
                lambda remaining: _panel_text(
                    "tts.budget_wait",
                    "The voice service is unstable — waiting {n} s, then one more repair pass",
                    n=int(remaining + 0.999),
                ),
                cancel=cancel,
            )
            for entry in still_missing():
                ladder.tried.pop(int(entry["id"]), None)  # all steps are open again
            seg_pass(still_missing(), rounds + 1, rounds + 1)
            missing_final = still_missing()
            if missing_final:
                # AGU11 3.6: the budget decision may be asked again after
                # the extra (bounded) wait produced nothing.
                waited_total = _time.monotonic() - started
                decision = _budget_decision(config, len(missing_final), waited_total)
                if decision == "wait10":
                    decision = "stop"  # one gated wait per run
        if missing_final and (decision == "fallback" or decision == "wait10"):
            backup = build_backup(config)
            if backup is None:
                log.warning(
                    "tts: the backup voice is not available — %d segment(s) stay missing",
                    len(missing_final),
                )
            else:
                log.warning(
                    "tts: %d segment(s) still missing — using the backup voice. "
                    "A different voice on a few segments is only acceptable "
                    "because the alternative is a failed job.",
                    len(missing_final),
                )
                backup_tts_provider = BackupTTS(tts.provider, backup)
                tts.provider = backup_tts_provider
                _measure_clock_offset_once(state_box)
                for entry in missing_final:
                    # AGU11: the primary just changed — every step of the
                    # ladder is open again for the segments using it.
                    ladder.tried.pop(int(entry["id"]), None)
                for entry in missing_final:
                    if cancel is not None and cancel.is_set():
                        raise StageCancelled("s4: cancelled during the backup-voice pass")
                    if clip_ok(entry):
                        continue
                    state, duration, cache_path = ladder.attempt(entry)
                    if state == "done":
                        shutil.copyfile(str(cache_path), str(job.tts_dir / f"{entry['id']}.mp3"))
                        entry["tts_duration"] = round(duration, 3)
                        entry["fit_status"] = "pending"
                        tts.provider = backup_tts_provider  # keep counting fallback uses
                        finished_count += 1
                    log.info("s4: progress %d/%d", position(entry), len(entries))
                    heartbeat_row(rounds + 1, rounds + 1)
                    write_json(job.translated_path, entries)
                fallback_uses = backup_tts_provider.fallback_uses
                write_json(job.translated_path, entries)

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
