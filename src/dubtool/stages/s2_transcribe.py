"""Stage 2: transcribe with faster-whisper -> transcript.json.

Cleanup: merge segments shorter than 0.8 s into neighbors, split segments
longer than 12 s at sentence punctuation where possible, then dedupe repeats
that straddle segment boundaries (AGENT_UPDATE_3.md 8.3). Ids stay sequential.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from dubtool import audio_utils

log = logging.getLogger("dubtool")

MIN_SEGMENT_S = 0.8
MAX_SEGMENT_S = 12.0
_SENTENCE_ENDS = ".!?"

# Common abbreviations: a sentence-final "period" after these does not close
# a sentence (used both by the splitter and dedupe-boundary checks).
ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "vs.", "etc.", "e.g.", "i.e.", "fig.", "no.", "approx.",
}


def _last_word(text: str) -> str:
    m = re.search(r"[\w'’]+\.*$", text.strip())
    return m.group(0).lower() if m else ""


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if _last_word(stripped) in ABBREVIATIONS:
        return False
    return stripped[-1] in ".!?…"


def _split_text_at_punctuation(text: str) -> tuple[str, str] | None:
    """Split text at the sentence-boundary character closest to the middle."""
    candidates = [i + 1 for i, ch in enumerate(text[:-1]) if ch in _SENTENCE_ENDS]
    if not candidates:
        return None
    mid = len(text) / 2
    pos = min(candidates, key=lambda p: abs(p - mid))
    return text[:pos].strip(), text[pos:].strip()


def split_long(segments: list[dict[str, Any]], max_s: float = MAX_SEGMENT_S) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in segments:
        duration = seg["end"] - seg["start"]
        if duration <= max_s:
            out.append(seg)
            continue
        parts = _split_text_at_punctuation(seg["text"])
        if parts is None:
            # No punctuation: midpoint split as a pragmatic fallback.
            parts = (seg["text"][: len(seg["text"]) // 2], seg["text"][len(seg["text"]) // 2 :])
        left, right = parts
        ratio = len(left) / max(len(left) + len(right), 1)
        mid_time = seg["start"] + ratio * duration
        out.append({"start": seg["start"], "end": mid_time, "text": left})
        out.append({"start": mid_time, "end": seg["end"], "text": right})
        # Recurse in case one half is still too long.
        if out[-2]["end"] - out[-2]["start"] > max_s or out[-1]["end"] - out[-1]["start"] > max_s:
            tail = out[-2:]
            del out[-2:]
            out.extend(split_long(tail, max_s))
    return out


def _words(text: str) -> list[str]:
    """Lowercased word tokens without punctuation, kept in order."""
    return re.findall(r"[\w'’]+", text.lower(), flags=re.UNICODE)


def dedupe_repeats(
    segments: list[dict[str, Any]], min_words: int = 3, max_words: int = 8
) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    """AGENT_UPDATE_3.md 8.3: remove repeats that straddle a boundary.

    For each pair of consecutive segments, compare the last n words of
    segment i with the first n words of segment i+1 (case- and
    punctuation-insensitive) for n from max_words down to min_words. On the
    longest match, remove those words from the START of segment i+1. Repetition
    inside one segment is left alone. Returns (segments, removals) where
    removals is [(kept_id, removed_text)] in occurrence order.
    """
    segs = [dict(s) for s in segments]
    removals: list[tuple[int, str]] = []
    i = 0
    while i < len(segs) - 1:
        tail_words = _words(segs[i]["text"])
        head_words = _words(segs[i + 1]["text"])
        matched = 0
        for n in range(min(max_words, len(tail_words), len(head_words)), min_words - 1, -1):
            if tail_words[-n:] == head_words[:n]:
                matched = n
                break
        if not matched:
            i += 1
            continue
        # Remove the matched words from the start of segment i+1: cut after
        # the matched-th word (by token position, never inside a word), then
        # move any immediately following punctuation/whitespace into the
        # removed text so the remainder starts with real content.
        raw = segs[i + 1]["text"]
        tokens = list(re.finditer(r"[\w'’]+|[^\w'’]+", raw))
        word_matches = [m for m in tokens if _words(m.group(0))]
        cut_idx = word_matches[matched - 1].end()
        remainder_raw = raw[cut_idx:]
        moved = remainder_raw[: len(remainder_raw) - len(remainder_raw.lstrip(" \t\r\n\u00a0.,;:!?…\"'\u201d\u2019)\u3009]"))]
        remainder = remainder_raw[len(moved):]
        removed_raw = raw[:cut_idx] + moved
        removals.append((segs[i + 1].get("id", i + 1), removed_raw.strip()))
        log.info(
            "s2 dedupe: removed %d repeated words from start of segment %d: %r",
            matched, segs[i + 1].get("id", i + 1), removed_raw.strip(),
        )
        if not remainder:
            # Segment became empty: extend segment i's end over it and drop it.
            segs[i]["end"] = segs[i + 1]["end"]
            segs.pop(i + 1)
            # Do not advance i: the next pair is now (i, old i+2).
        else:
            segs[i + 1]["text"] = remainder
            i += 1
    return segs, removals


def merge_short(segments: list[dict[str, Any]], min_s: float = MIN_SEGMENT_S) -> list[dict[str, Any]]:
    """Merge segments shorter than min_s into neighbors."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        if out and (seg["end"] - seg["start"]) < min_s:
            out[-1]["text"] = (out[-1]["text"] + " " + seg["text"]).strip()
            out[-1]["end"] = seg["end"]
        else:
            out.append(dict(seg))
    # A short first segment with no previous neighbor merges into the next.
    if len(out) >= 2 and (out[0]["end"] - out[0]["start"]) < min_s:
        out[1]["text"] = (out[0]["text"] + " " + out[1]["text"]).strip()
        out[1]["start"] = out[0]["start"]
        out.pop(0)
    return out


def cleanup_segments(
    segments: list[dict[str, Any]], dedupe_min_words: int = 3
) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    """Split long, merge short, dedupe boundary repeats, re-index ids."""
    cleaned = merge_short(split_long(segments))
    cleaned, removals = dedupe_repeats(cleaned, min_words=dedupe_min_words)
    for i, seg in enumerate(cleaned):
        seg["id"] = i
    return cleaned, removals


# ------------------------------------------------ boundary word repeats
# Update 12 task 3.6: words that are legitimately doubled ("very very",
# "so so") never collapse — the recognizer boundary repeats below skip them.
BOUNDARY_STOP_WORDS = {
    "very", "that", "had", "no", "so", "bye", "really", "okay", "ok",
    "yes", "well", "now", "then", "never", "not", "too", "again",
}


def _norm_word(token: str) -> str:
    return re.sub(r"[^\w'’]", "", token).lower()


def collapse_boundary_word(
    segments: list[dict[str, Any]], words: list[dict] | None
) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    """Update 12 task 3.6: collapse an identical word repeated at a
    segment boundary using the word timestamps.

    When the last word of segment i and the first word of segment i+1 are
    identical (case-insensitive) and their time ranges overlap or are less
    than 0.1 s apart, the second occurrence is dropped — but never for a
    word that is legitimately doubled (the stop list above). Complements
    the existing rule that removes runs of three or more repeated words.
    Returns (segments, removals) with occurrence-order entries
    [(segment_id_kept, dropped_word)]. Every removal is logged.
    """
    if not words:
        return segments, []
    segs = [dict(s) for s in segments]
    removals: list[tuple[int, str]] = []

    def _tokens(text: str) -> list[re.Match]:
        return list(re.finditer(r"[\w'’]+", text or ""))

    i = 0
    while i < len(segs) - 1:
        tail_matches = _tokens(segs[i]["text"])
        head_matches = _tokens(segs[i + 1]["text"])
        if not tail_matches or not head_matches:
            i += 1
            continue
        tail = _norm_word(tail_matches[-1].group(0))
        head = _norm_word(head_matches[0].group(0))
        if not tail or tail != head or tail in BOUNDARY_STOP_WORDS:
            i += 1
            continue
        tail_span = _time_of_seg_word(segs[i], tail_matches[-1], words, last=True)
        head_span = _time_of_seg_word(segs[i + 1], head_matches[0], words, last=False)
        if tail_span is None or head_span is None:
            i += 1
            continue
        tail_start, tail_end = tail_span
        head_start, head_end = head_span
        # Overlapping ranges, or ranges less than 0.1 s apart.
        gap = max(0.0, max(tail_start - head_end, head_start - tail_end))
        if not (gap < 0.1 or (head_start < tail_end and tail_start < head_end)):
            i += 1
            continue
        # Drop the second occurrence from the start of segment i+1.
        raw = segs[i + 1]["text"]
        begin = head_matches[0].start()
        end = head_matches[0].end()
        remainder = raw[end:].lstrip(" \t\r\n\u00a0")
        removed = raw[begin:end]
        if not remainder:
            i += 1
            continue  # never empty out a segment for one word
        removals.append((int(segs[i + 1].get("id", i + 1)), removed))
        log.info(
            "s2: boundary repeat dropped: word %r removed from segment %d "
            "(same word ends segment %d, times %.2f/%.2f)",
            removed, segs[i + 1].get("id", i + 1), segs[i].get("id", i),
            tail_start, head_start,
        )
        segs[i + 1]["text"] = remainder
        i += 1
    return segs, removals


def _time_of_seg_word(seg: dict, match: re.Match, words: list[dict], *,
                      last: bool) -> tuple[float, float] | None:
    """The (start, end) span of `match` inside `seg`: word-list entries
    whose time falls inside the segment and whose token equals the match."""
    norm = _norm_word(match.group(0))
    candidates = [
        w for w in words
        if _norm_word(w.get("w") or "") == norm
        and float(w.get("start") or 0.0) >= float(seg["start"]) - 0.05
        and float(w.get("end") or 0.0) <= float(seg["end"]) + 0.05
    ]
    if not candidates:
        return None
    chosen = candidates[-1] if last else candidates[0]
    return float(chosen["start"]), float(chosen["end"])


def _finalize(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach contract fields and sequential ids (AGENT.md section 4)."""
    return [
        {
            "id": i,
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": seg["text"],
            "speaker": seg.get("speaker"),
        }
        for i, seg in enumerate(segments)
    ]


def _postprocess_only(job, config) -> None:
    """Apply dedupe + merge/split to the existing transcript without Whisper."""
    from dubtool.job import read_json, write_json

    segments = [
        {"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]}
        for s in read_json(job.transcript_path)
    ]
    cleaned = merge_short(split_long(segments))
    cleaned, removals = dedupe_repeats(
        cleaned, min_words=int(config.asr.get("dedupe_min_words", 3))
    )
    # Update 12 task 3.6: single boundary-word repeats collapse against the
    # word timestamps (never for legitimately doubled words).
    try:
        from dubtool.punctuate import read_words

        cleaned, boundary_removals = collapse_boundary_word(cleaned, read_words(job))
        removals = removals + boundary_removals
    except Exception:
        log.info("s2 postprocess: boundary-word collapse skipped (no word times)")
    for i, seg in enumerate(cleaned):
        seg["id"] = i
    write_json(job.transcript_path, _finalize(cleaned))
    for seg_id, removed in removals:
        print(f"s2 postprocess: removed repeat from segment {seg_id}: {removed!r}")
    log.info("s2 postprocess: %d duplicate removals, %d segments remain", len(removals), len(cleaned))

def _transcribe(model, job, config) -> None:
    """Whisper pass + cleanup + write transcript.json (AGU6 4.2 split)."""
    want_words = bool(config.asr.get("word_timestamps", True))
    kwargs: dict[str, Any] = {
        "language": "en",
        "vad_filter": True,
        "condition_on_previous_text": bool(
            config.asr.get("condition_on_previous_text", False)
        ),
    }
    if config.asr.get("initial_prompt"):
        kwargs["initial_prompt"] = config.asr.get("initial_prompt")
    words: list[dict] | None = None
    try:
        if want_words:
            # AGU8 4.2: word-level timestamps -> words.json (the transcript
            # contract stays untouched).
            kwargs["word_timestamps"] = True
        raw_segments, info = model.transcribe(str(job.audio_path), **kwargs)
        segments, words = _collect(raw_segments, _asr_progress_cb(info.duration))
    except TypeError as exc:
        if want_words and "word_timestamps" in str(exc):
            log.warning(
                "s2: faster-whisper %s does not accept word_timestamps (%s) "
                "— continuing without word times", _fw_version(), exc,
            )
            kwargs.pop("word_timestamps", None)
            raw_segments, info = model.transcribe(str(job.audio_path), **kwargs)
            segments, words = _collect(raw_segments, _asr_progress_cb(info.duration))
        else:
            raise
    if not segments:
        raise RuntimeError("transcription produced no segments — is the audio silent?")

    cleaned, removals = cleanup_segments(
        segments, dedupe_min_words=int(config.asr.get("dedupe_min_words", 3))
    )
    # Update 12 task 3.6: single boundary-word repeats collapse against the
    # word timestamps (never for legitimately doubled words).
    if words:
        cleaned, boundary_removals = collapse_boundary_word(cleaned, words)
        removals = removals + boundary_removals
    for seg_id, removed in removals:
        log.info("s2: removed repeated words from segment %d: %r", seg_id, removed)
    from dubtool.job import write_json

    write_json(job.transcript_path, _finalize(cleaned))
    if words:
        from dubtool.punctuate import write_words

        write_words(job, words)
    log.info(
        "s2: transcribed %d raw -> %d cleaned segments (%.1fs of speech, "
        "lang=%s, words=%s)",
        len(segments), len(cleaned), info.duration, info.language,
        len(words) if words else "n/a",
    )


def _collect(raw_segments, progress_cb=None) -> tuple[list[dict], list[dict] | None]:
    segments: list[dict] = []
    words: list[dict] = []
    for s in raw_segments:
        if not (s.text and s.text.strip()):
            continue
        segments.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip()})
        if progress_cb is not None:
            progress_cb(float(s.end))
        ws = getattr(s, "words", None)
        for w in ws or []:
            token = (getattr(w, "word", "") or "").strip()
            if token:
                words.append({"w": token, "start": float(w.start), "end": float(w.end)})
    return segments, words


def _asr_progress_cb(total_s: float):
    """Update 9 task 3.3: percent-of-audio ASR heartbeat, emitted with the
    progress-details channel as the segments stream in (throttled by whole
    percent; silent when nothing is bound)."""
    total = max(0.0, float(total_s or 0.0))
    last_pct = -1

    def cb(se: float) -> None:
        nonlocal last_pct
        if total <= 0:
            return
        pct = min(100, int(se / total * 100))
        if pct == last_pct:
            return
        last_pct = pct
        try:
            from dubtool import heartbeat
            from dubtool.app import i18n

            heartbeat.emit(i18n.tr("hb.asr_progress", n=pct))
        except Exception:
            pass

    return cb


def _fw_version() -> str:
    try:
        import faster_whisper

        return getattr(faster_whisper, "__version__", "?")
    except Exception:
        return "?"


def run(job, config, force: bool = False) -> None:
    out = job.transcript_path
    if out.exists() and not force:
        log.info("s2: transcript.json already exists, skipping")
        return

    from faster_whisper import WhisperModel  # lazy: keeps tests offline-light

    audio_utils.require_ffmpeg()
    # AGU6 4.2: cap Whisper's CPU threads at the chosen "Mức dùng CPU" level
    # and run the app itself below normal so the desktop stays responsive
    # (restored in the finally below).
    from dubtool.app import cpu as cpu_mod

    threads = cpu_mod.thread_budget()
    cpu_mod.lower_app_priority()
    log.info(
        "s2: loading whisper model %s on %s (%s, %d threads)",
        config.whisper_model, config.device, config.compute_type, threads,
    )
    try:
        model = WhisperModel(
            config.whisper_model,
            device=config.device,
            compute_type=config.compute_type,
            cpu_threads=threads,
        )
        segments = _transcribe(model, job, config)
    finally:
        cpu_mod.restore_app_priority()
