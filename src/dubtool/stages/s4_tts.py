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

# Step names recorded in failures.json ("step" field). Update 12 task 4:
# the alternate-voice steps are gone — a finished video never contains two
# voices; the ladder is the sentence as is, then a clause-punctuation
# split, ALWAYS with the same voice.
STEP_DIRECT = "direct"
STEP_SPLIT = "split"
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
            limiter = getattr(self, "limiter", None)
            try:
                # Update 12 task 5: every request paces through the global
                # limiter; a refusal is ANY failure (handshake 503 counts).
                if limiter is not None:
                    limiter.gate()
                try:
                    self.provider.synthesize(text, voice, raw)
                except Exception:
                    if limiter is not None:
                        limiter.record(False)
                    raise
                if limiter is not None:
                    limiter.record(True)
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
        # Update 12: tts.alt_voice is no longer used (one voice per video);
        # a legacy value in config only logs a note.
        if tts_cfg.get("alt_voice"):
            log.info(
                "tts: the configured tts.alt_voice setting is no longer used "
                "(one voice per video) — remove it from config.yaml"
            )
        self.delays = [float(v) for v in (tts_cfg.get("attempt_delays_s") or [2.0, 5.0])]
        # Update 12 task 3.3: sentence units default to a 200-char split cap.
        self.max_chars = max(25, int(tts_cfg.get("max_chars_per_request", 200)))
        self.silence_s = float(tts_cfg.get("join_silence_s", 0.08))
        self.unit_budget_s = float(tts_cfg.get("unit_budget_s", 60.0))
        self.refuse_ids: set[int] = set()   # fault injection (AGU11)
        self.tried: dict[int, set[str]] = {}  # step names already attempted per segment
        self.attempt_no: dict[int, int] = {}

    def steps(self, base_voice: str, text_len: int) -> list[tuple[str, str]]:
        """Update 12 task 4 (one voice per video): the repair ladder is
        the sentence as is, plus (only when the text is longer than
        `tts.max_chars_per_request`) a clause-punctuation split — always
        the same voice; a short backoff between the steps; no alternate
        voice steps at all."""
        if text_len > self.max_chars:
            return [(STEP_DIRECT, base_voice), (STEP_SPLIT, base_voice)]
        return [(STEP_DIRECT, base_voice)]

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
        step_list = self.steps(base_voice, len(text))
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
            if seg_id in self.refuse_ids:
                # Fault `tts:refuse-ids=…` — the service refuses this text
                # with the one configured voice; the segment stays pending
                # (deferred rounds, then the budget decision). Same voice
                # always — no alternate-voice repair path exists.
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

        if step == STEP_SPLIT:
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
    """Update 12 task 4: what to do when segments are still missing after
    the rounds — wait 10 more minutes / switch the WHOLE video to the
    backup voice / stop. The GUI answers through the dialog; headless runs
    use `tts.budget_policy`: 'stop' (default) or 'switch_all' (the legacy
    'fallback' value maps to switch_all)."""
    from dubtool.audio_utils import format_mmss
    from dubtool.tts.fallback import build_backup

    tts_cfg = getattr(config, "tts", {}) or {}
    dialog = getattr(config, "tts_budget_dialog", None)
    if dialog is not None:
        try:
            backup = build_backup(config)
        except Exception:
            backup = None
        fallback_ok = backup is not None
        backup_name = getattr(backup, "label", None) or getattr(backup, "name", "")
        try:
            choice = str(
                dialog(missing_count, format_mmss(waited_total_s), fallback_ok, backup_name) or ""
            )
        except Exception as exc:
            log.warning("tts budget dialog failed (%s) — stopping", exc)
            return "stop"
        choice = choice.strip().lower()
        if choice in ("wait10", "switch_all", "stop"):
            return choice
        if choice == "fallback":  # legacy dialog value
            return "switch_all"
        log.warning("tts budget dialog returned an unknown choice %r — stopping", choice)
        return "stop"
    # Headless/API run: no dialog. `tts.budget_policy`: stop (default) or
    # switch_all; the legacy 'fallback' value means switch_all too.
    policy = str(tts_cfg.get("budget_policy", "stop")).lower()
    if policy in ("switch_all", "fallback"):
        try:
            if build_backup(config) is not None:
                log.info(
                    "tts: budget policy — %d segment(s) still missing, switching "
                    "the whole video to the backup voice (headless run)",
                    missing_count,
                )
                return "switch_all"
        except Exception:
            pass
        log.warning(
            "tts: budget policy switch_all needs a backup voice — none is "
            "configured; %d segment(s) stay missing", missing_count,
        )
        return "stop"
    log.info(
        "tts: budget policy — %d segment(s) still missing, stopping "
        "(TTSUnavailableError); the finished clips are preserved",
        missing_count,
    )
    return "stop"


# ------------------------------------------------- Update 12 task 5: rate


class VoiceRequestLimiter:
    """A global token bucket for ALL edge-tts requests (`tts.max_rps`,
    default 1 request per second), adaptive:

    - when more than 30% of the last 10 requests were refused (a refusal
      is ANY failure — NoAudioReceived counts, and a handshake 503 counts),
      the interval doubles (minimum one request per 5 s) and the change is
      logged AND sent to the details panel;
    - after 10 consecutive successes the interval recovers by half of the
      excess (slowly), never below the configured interval.
    """

    MIN_INTERVAL_S = 5.0

    def __init__(self, max_rps: float = 1.0, cancel=None, name="edge-tts"):
        self.cancel = cancel
        self.name = name
        self.min_interval_s = 1.0 / max_rps if max_rps and max_rps > 0 else 0.0
        self.current_interval_s = self.min_interval_s
        self._last_allowed: float | None = None
        self.recent: list[bool] = []      # outcomes of the last <= 10 requests
        self.success_streak = 0
        self.refusals = 0
        self.requests = 0

    def gate(self) -> None:
        """Wait until the next request is allowed; visible, bounded waits."""
        if self.current_interval_s <= 0:
            return
        import time as _time

        now = _time.monotonic()
        wait_until = (
            now if self._last_allowed is None
            else max(self._last_allowed + self.current_interval_s, now)
        )
        remaining = max(0.0, wait_until - now)
        if remaining > 0:
            def text(r: float) -> str:
                return _panel_text(
                    "tts.rate_wait",
                    "Voice request pacing — next request in {n} s",
                    n=int(r + 0.999),
                )

            heartbeat.sleeping(remaining, text, cancel=self.cancel)
        self._last_allowed = _time.monotonic()

    def record(self, ok: bool) -> None:
        """Feed one request outcome; adjusts the interval when needed."""
        self.requests += 1
        self.recent.append(bool(ok))
        if len(self.recent) > 10:
            self.recent.pop(0)
        if ok:
            self.refusals = self.refusals if False else self.refusals
            self.success_streak += 1
            if self.success_streak >= 10 and self.current_interval_s > self.min_interval_s:
                excess = self.current_interval_s - self.min_interval_s
                self.current_interval_s = max(
                    self.min_interval_s, self.current_interval_s - excess / 2.0
                )
                self.success_streak = 0
                self._announce()
            return
        self.refusals += 1
        self.success_streak = 0
        recent = self.recent[-10:]
        refused_share = (1.0 - (sum(recent) / len(recent))) if recent else 1.0
        if refused_share > 0.3:
            new_interval = min(self.MIN_INTERVAL_S, self.current_interval_s * 2.0)
            if new_interval != self.current_interval_s:
                self.current_interval_s = new_interval
                log.warning(
                    "tts limiter: %.0f%% of the last 10 requests were refused — "
                    "rate lowered to one request per %.1f s",
                    refused_share * 100.0, self.current_interval_s,
                )
                self._announce()

    def _announce(self) -> None:
        try:
            heartbeat.emit(_panel_text(
                "tts.rate",
                "Voice request rate: one per {n:.1f} s",
                n=self.current_interval_s,
            ))
        except Exception:
            pass


# ------------------------------------------------ Update 12: voice + source


def _state_path(job):
    """The job's state.json path (tolerates older test fakes)."""
    cached = getattr(job, "state_path", None)
    if cached is not None:
        return cached
    return job.dir / "state.json"


def _state_voice(job) -> str | None:
    """Update 12 task 4 item 2: one video-wide voice recorded in state.json."""
    try:
        from dubtool.job import read_json

        state = read_json(_state_path(job))
        voice = state.get("voice") if isinstance(state, dict) else None
        return str(voice) if voice else None
    except Exception:
        return None


def _set_state_voice(job, voice: str, *, switched: bool = False,
                     reason: str = "") -> None:
    """Record (or update) the video-wide voice in state.json (atomic)."""
    import time as _time

    from dubtool.job import read_json, write_json

    state = {}
    try:
        state = read_json(_state_path(job))
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    state["voice"] = voice
    if switched:
        state["voice_switched_at"] = _time.strftime("%Y-%m-%dT%H:%M:%S")
        state["voice_switch_reason"] = reason or "budget decision"
    write_json(_state_path(job), state)


def _backup_label(config, provider) -> str:
    """The human-readable name of the backup voice (recorded in state and
    the summary when a whole-video switch happens)."""
    tts_cfg = getattr(config, "tts", {}) or {}
    mode = str(tts_cfg.get("fallback", "none")).lower()
    if mode == "windows_sapi":
        return f"Windows SAPI ({tts_cfg.get('sapi_voice') or 'mặc định'})"
    if mode == "piper":
        from pathlib import Path as _Path

        return f"Piper ({_Path(str(tts_cfg.get('piper_voice', ''))).name or 'mặc định'})"
    label = getattr(provider, "name", None)
    return str(label) if label else mode or "backup"


def _entries_source(job, config):
    """Update 12 task 3: the entries the voice stage works on.

    Sentence mode (dub.granularity sentence + dub_units.json present):
    one entry per sentence unit (id = unit_id, media under u<id>.mp3),
    persisted to dub_units.json. Segment mode: the old per-segment flow
    (translated.json, media under <id>.mp3) — old jobs resume unchanged.
    Returns (mode, entries, persist)."""
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
            # One in-memory alias: media naming uses entry['id'].
            for rec in records:
                rec.setdefault("id", int(rec["unit_id"]))
            voice = _state_voice(job) or str(getattr(config, "tts_voice", ""))
            for rec in records:
                rec.setdefault("tts_voice", voice)

            def persist_sentence() -> None:
                from dubtool.dub_units import write_dub_units

                write_dub_units(job, records)

            return mode, records, persist_sentence

    entries = read_json(job.translated_path)

    def persist_segment() -> None:
        write_json(job.translated_path, entries)

    return "segment", entries, persist_segment


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

    mode, entries, persist = _entries_source(job, config)
    from dubtool.dub_units import file_base

    tts_cfg = getattr(config, "tts", {}) or {}
    limiter = VoiceRequestLimiter(
        max_rps=float(tts_cfg.get("max_rps", 1.0) or 0.0), cancel=cancel,
    )
    # Update 12 task 4 item 2: one video-wide voice, recorded in state.json.
    video_voice = _state_voice(job) or str(getattr(config, "tts_voice", ""))
    _set_state_voice(job, video_voice)
    cache_dir = job.tts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures = TTSFailureLog(job.dir)
    tts = build_tts(config, cache_dir, media_cache=media_cache)
    tts.cancel = cancel
    tts.limiter = limiter  # every provider request goes through the gate
    provider = getattr(tts, "provider", None)  # fakes may not have one
    fault_factory, refuse_ids = _fault_spec(config)
    if fault_factory is not None and provider is not None:
        tts.provider = fault_factory(provider)
    ladder = _Ladder(tts, failures, config, cancel=cancel)
    ladder.refuse_ids = refuse_ids

    def clip_path(entry) -> Path:
        # Sentence mode uses u<unit_id>.mp3; segment mode keeps <id>.mp3.
        return job.tts_dir / f"{file_base(entry, mode)}.mp3"

    def clip_ok(entry) -> bool:
        if entry.get("tts_duration") is not None:
            existing = clip_path(entry)
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
    voice_switch: dict | None = None  # Update 12: set when the video-wide voice changed
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
            shutil.copyfile(str(cache_path), str(clip_path(entry)))
            entry["tts_duration"] = round(duration, 3)
            entry["fit_status"] = "pending"
            # Update 12 task 3 item 2: the voice that spoke this clip.
            entry["voice"] = str(entry.get("tts_voice") or video_voice)

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
            persist()

    def position(entry) -> int:
        for i, e in enumerate(entries, start=1):
            if e is entry:
                return i
        return len(entries)

    log.info(
        "s4: tts budget policy %s — stage wait budget %.0f s, unit budget %.0f s/segment",
        str(tts_cfg.get("budget_policy", "stop")), stage_budget_s,
        float(tts_cfg.get("unit_budget_s", 60.0)),
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
        if missing_final and decision == "switch_all":
            # Update 12 task 3 item 3: switching is an explicit, whole-video
            # action — the backup provider (never the failing primary) and
            # EVERY unit regenerate, so the finished video keeps exactly one
            # voice. Clips are cached per (text, voice), so switching back
            # reuses the earlier clips.
            backup = build_backup(config)
            if backup is None:
                log.warning(
                    "tts: the backup voice is not available — %d unit(s) stay missing",
                    len(missing_final),
                )
            else:
                backup_name = _backup_label(config, backup)
                log.warning(
                    "tts: %d unit(s) still missing — switching the whole video to "
                    "the backup voice and regenerating every unit "
                    "(the old-voice clips stay cached)",
                    len(missing_final),
                )
                heartbeat.emit(_panel_text(
                    "tts.switching",
                    "Voice service unstable — switching the whole video to: {voice}",
                    voice=backup_name,
                ))
                tts.provider = backup  # pure backup: no silent primary retries
                _measure_clock_offset_once(state_box)
                for entry in entries:
                    # A new video-wide voice: every step of the ladder is
                    # open again; the old-voice clip is retired (the
                    # content cache keeps (text, old voice) for switching back).
                    ladder.tried.pop(int(entry["id"]), None)
                    entry["tts_voice"] = backup_name
                for entry in entries:
                    clip_path(entry).unlink(missing_ok=True)
                seg_pass(list(entries), rounds + 1, rounds + 1)
                video_voice = backup_name
                voice_switch = {
                    "at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "to": backup_name,
                }
                _set_state_voice(
                    job, backup_name, switched=True,
                    reason="voice failures exceeded the stage budget",
                )
                persist()

    # ------------------------------------------------------------ finish
    persist()
    final_missing = still_missing()
    elapsed = _time.monotonic() - started
    if final_missing:
        raise TTSUnavailableError(
            sorted({e["id"] for e in final_missing}), finished_count
        )
    # Update 12 task 3 item 5: the summary states the video-wide voice
    # (and, when a switch happened, when and why).
    heartbeat.emit(_panel_text(
        "tts.voice_summary", "Voice: {voice}", voice=video_voice,
    ))
    if voice_switch:
        log.info(
            "s4: the voice was switched to %s at %s because voice failures "
            "exceeded the stage budget; every unit was regenerated with it",
            voice_switch["to"], voice_switch["at"],
        )
    distinct_voices = {e.get("voice") for e in entries if e.get("voice")}
    if len(distinct_voices) > 1 and not voice_switch:
        log.warning(
            "s4: unexpected mixed voices in the finished job: %s",
            sorted(v for v in distinct_voices if v),
        )
    log.info(
        "s4: all clips present: %d new (voice=%s), retry_rounds_used=%d, "
        "fallback_voice_used=%d, %.1fs total",
        finished_count, video_voice, used_rounds, fallback_uses, elapsed,
    )
    log.info(failures.summary(all_texts))


def regenerate_tts(job, config, ids: list[int], force: bool = True) -> None:
    """`retts`: re-synthesize the given entries (segment ids in segment
    mode, sentence-unit ids in sentence mode) — e.g. after manual edits."""
    import time as _time

    from dubtool.dub_units import file_base, granularity_for_job, load_dub_units

    audio_utils.require_ffmpeg()
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
    id_set = set(int(i) for i in ids)
    regenerated = 0
    for entry in entries:
        if entry["id"] not in id_set:
            continue
        if not entry.get("vi"):
            raise RuntimeError(f"retts: entry id={entry['id']} has no Vietnamese text")
        timer = Timer()
        try:
            duration, cache_path = tts.synthesize(entry)
        except TTSFailure as exc:
            failures.record(entry["id"], 1, entry["vi"], exc, timer.elapsed())
            raise
        shutil.copyfile(str(cache_path), str(job.tts_dir / f"{file_base(entry, mode)}.mp3"))
        entry["tts_duration"] = round(duration, 3)
        entry["fit_status"] = "pending"
        entry["voice"] = str(entry.get("tts_voice") or getattr(config, "tts_voice", ""))
        regenerated += 1
        if records is None:
            write_json(job.translated_path, entries)
        else:
            from dubtool.dub_units import write_dub_units

            write_dub_units(job, records)
    # Drop stale fitted clips so s5 refits these ids.
    for entry_id in id_set:
        base = "u" if records is not None else ""
        stale = job.fitted_dir / f"{base}{entry_id}.wav"
        if stale.exists():
            stale.unlink()
    if regenerated != len(id_set):
        missing = sorted(id_set - {e["id"] for e in entries})
        log.warning(
            "retts: ids not found in %s: %s",
            "dub_units.json" if records is not None else "translated.json", missing,
        )
    log.info("retts: regenerated %d clips", regenerated)
