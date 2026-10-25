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
    for i, seg in enumerate(cleaned):
        seg["id"] = i
    write_json(job.transcript_path, _finalize(cleaned))
    for seg_id, removed in removals:
        print(f"s2 postprocess: removed repeat from segment {seg_id}: {removed!r}")
    log.info("s2 postprocess: %d duplicate removals, %d segments remain", len(removals), len(cleaned))


def run(job, config, force: bool = False) -> None:
    out = job.transcript_path
    if out.exists() and not force:
        log.info("s2: transcript.json already exists, skipping")
        return

    from faster_whisper import WhisperModel  # lazy: keeps tests offline-light

    audio_utils.require_ffmpeg()
    log.info("s2: loading whisper model %s on %s (%s)", config.whisper_model, config.device, config.compute_type)
    model = WhisperModel(
        config.whisper_model,
        device=config.device,
        compute_type=config.compute_type,
    )
    raw_segments, info = model.transcribe(
        str(job.audio_path),
        language="en",
        vad_filter=True,
        condition_on_previous_text=bool(
            config.asr.get("condition_on_previous_text", False)
        ),
    )
    segments = [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in raw_segments
        if s.text and s.text.strip()
    ]
    if not segments:
        raise RuntimeError("transcription produced no segments — is the audio silent?")

    cleaned, removals = cleanup_segments(
        segments, dedupe_min_words=int(config.asr.get("dedupe_min_words", 3))
    )
    for seg_id, removed in removals:
        log.info("s2: removed repeated words from segment %d: %r", seg_id, removed)
    from dubtool.job import write_json

    write_json(out, _finalize(cleaned))
    log.info(
        "s2: transcribed %d raw -> %d cleaned segments (%.1fs of speech, lang=%s)",
        len(segments), len(cleaned), info.duration, info.language,
    )
