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
# Sentence-ending punctuation across Latin, CJK, Arabic, and Devanagari scripts
# (Spanish opening marks ¿ ¡ do not end sentences).
_SENTENCE_ENDS = ".!?…。！？؟۔।"

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
    stripped = (text or "").strip()
    if len(stripped) < 2:
        return None
    candidates: list[int] = []
    for i, ch in enumerate(stripped[:-1]):
        if ch in _SENTENCE_ENDS:
            if stripped[i + 1] in _SENTENCE_ENDS:
                continue
            pos = i + 1
            if stripped[:pos].strip() and stripped[pos:].strip():
                candidates.append(pos)
    if not candidates:
        return None
    mid = len(stripped) / 2
    pos = min(candidates, key=lambda p: abs(p - mid))
    return stripped[:pos].strip(), stripped[pos:].strip()


def split_long(segments: list[dict[str, Any]], max_s: float = MAX_SEGMENT_S) -> list[dict[str, Any]]:
    """Iteratively split segments longer than max_s without unbounded recursion."""
    out: list[dict[str, Any]] = []
    queue = [dict(s) for s in segments]
    idx = 0
    while idx < len(queue):
        seg = queue[idx]
        idx += 1
        duration = float(seg["end"]) - float(seg["start"])
        text = (seg.get("text") or "").strip()
        if duration <= max_s or len(text) < 2:
            out.append(seg)
            continue
        parts = _split_text_at_punctuation(text)
        if parts is None:
            mid = len(text) // 2
            left = text[:mid].strip()
            right = text[mid:].strip()
            if not left or not right:
                out.append(seg)
                continue
            parts = (left, right)
        left, right = parts
        if not left or not right or len(left) >= len(text) or len(right) >= len(text):
            out.append(seg)
            continue
        ratio = len(left) / max(len(left) + len(right), 1)
        mid_time = float(seg["start"]) + ratio * duration
        if mid_time <= float(seg["start"]) or mid_time >= float(seg["end"]):
            out.append(seg)
            continue
        left_seg = dict(seg, start=float(seg["start"]), end=mid_time, text=left)
        right_seg = dict(seg, start=mid_time, end=float(seg["end"]), text=right)
        queue[idx:idx] = [left_seg, right_seg]
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

from dubtool import languages as lang_mod

LANGUAGE_NAMES: dict[str, str] = dict(lang_mod.WHISPER_LANGUAGES)
_SPACELESS_LANGS = set(lang_mod.SPACELESS_LANGS)


class VietnameseSourceDetected(RuntimeError):
    """Raised after stage 2 finishes transcription when the video is already
    Vietnamese, so the pipeline stops before translation/TTS and can offer
    subtitles-only (.srt + transcript) output."""

    def __init__(self, probability: float, transcript_path: Path | None = None):
        self.probability = float(probability)
        self.transcript_path = transcript_path
        pct = int(round(self.probability * 100))
        super().__init__(
            f"Video có vẻ đã là tiếng Việt (độ tin cậy {pct}%). Không cần dịch."
        )


def language_name(code: str | None) -> str:
    return lang_mod.english_name(code)


def _select_vad_windows(
    audio,
    sample_rate: int = 16000,
    max_windows: int = 3,
    window_s: float = 30.0,
    speech_regions: list[dict] | None = None,
) -> list[tuple[float, Any]]:
    """Pick up to `max_windows` speech-containing windows (start, middle, end)
    using Silero VAD (or evenly spaced fallback if VAD yields none)."""
    total_samples = len(audio)
    win_samples = int(window_s * sample_rate)
    if total_samples <= 0:
        return []
    if speech_regions is None:
        try:
            from faster_whisper.vad import get_speech_timestamps

            speech_regions = get_speech_timestamps(audio, sampling_rate=sample_rate)
        except Exception as exc:
            log.info("s2: VAD window lookup unavailable (%s); falling back to spaced windows", exc)
            speech_regions = []

    starts: list[int] = []
    eff_win_samples = win_samples
    if speech_regions:
        # Compute total active speech span across regions. If Silero VAD merges
        # continuous speech into 1-2 long regions (>15s), sample start/mid/end
        # inside the speech span using non-overlapping windows up to window_s.
        first_s = max(0, int(speech_regions[0].get("start", 0)))
        last_e = min(total_samples, int(speech_regions[-1].get("end", total_samples)))
        span = max(0, last_e - first_s)
        n = len(speech_regions)
        if n >= max_windows:
            pick_indices = [0, n // 2, n - 1]
            for idx in pick_indices[:max_windows]:
                st = max(0, int(speech_regions[idx].get("start", 0)))
                if st not in starts:
                    starts.append(st)
        elif span >= int(15.0 * sample_rate):
            eff_win_samples = min(win_samples, max(int(10.0 * sample_rate), span // max_windows))
            last_start = max(first_s, last_e - eff_win_samples)
            mid_start = first_s + max(0, (last_start - first_s) // 2)
            starts = sorted({first_s, mid_start, last_start})[:max_windows]
        else:
            for reg in speech_regions[:max_windows]:
                st = max(0, int(reg.get("start", 0)))
                if st not in starts:
                    starts.append(st)
    if not starts:
        if total_samples <= int(15.0 * sample_rate):
            starts = [0]
        else:
            eff_win_samples = min(win_samples, max(int(10.0 * sample_rate), total_samples // max_windows))
            last_start = max(0, total_samples - eff_win_samples)
            mid_start = max(0, last_start // 2)
            starts = sorted({0, mid_start, last_start})[:max_windows]

    windows: list[tuple[float, Any]] = []
    for st in starts[:max_windows]:
        end = min(total_samples, st + eff_win_samples)
        if end > st:
            windows.append((round(st / float(sample_rate), 2), audio[st:end]))
    return windows


def _call_detect_language(model, window_audio) -> tuple[str, float, list[tuple[str, float]]]:
    """Call faster-whisper detect_language with signature-safe fallback for mocks."""
    try:
        lang, prob, all_probs = model.detect_language(
            window_audio,
            vad_filter=False,
            language_detection_segments=1,
            language_detection_threshold=0.5,
        )
    except TypeError:
        res = model.detect_language(window_audio)
        if len(res) == 3:
            lang, prob, all_probs = res
        else:
            lang, prob = res[0], res[1]
            all_probs = []
    return str(lang or "en").lower(), float(prob or 0.0), list(all_probs or [])


def _emit_language_notice(info: dict, config) -> None:
    """Emit detected language + confidence and optional non-English medium tip."""
    code = str(info.get("code") or "en").lower()
    pct = int(round(float(info.get("probability") or 0.0) * 100))
    method = str(info.get("method") or "auto")
    label = lang_mod.display_name(code, ui_lang="vi")
    if method == "manual":
        msg = f"Ngôn ngữ: {label} (chọn tay)"
    else:
        msg = f"Ngôn ngữ: {label} ({pct}%), đã nhận diện tự động"
    try:
        from dubtool import heartbeat

        heartbeat.emit(msg)
        if info.get("mixed"):
            heartbeat.emit("Cảnh báo: video có nhiều ngôn ngữ; một số đoạn có thể nhận dạng sai")
        elif info.get("uncertain"):
            heartbeat.emit(f"Cảnh báo: độ tin cậy nhận diện ngôn ngữ thấp ({pct}%)")
        w_model = str(getattr(config, "whisper_model", "small") or "small").lower()
        if code != "en" and code != "vi" and w_model in ("tiny", "base", "small"):
            heartbeat.emit("Gợi ý: Ngôn ngữ này nhận dạng chính xác hơn với mô hình medium (chậm hơn)")
    except Exception:
        pass


def detect_source_language(
    model,
    job,
    config,
    *,
    audio=None,
    speech_regions: list[dict] | None = None,
) -> dict[str, Any]:
    """Run up-to-3-window VAD language detection for `auto` mode or validate a
    manual source language choice. Stores `source_language` in `job.state`."""
    requested = (config.asr.get("language") or "auto").strip().lower()
    min_conf = float(config.asr.get("language_min_confidence", 0.60) or 0.60)
    whisper_model = str(getattr(config, "whisper_model", "small") or "small").strip()

    if requested != "auto":
        if lang_mod.is_english_only_whisper_model(whisper_model) and requested != "en":
            raise RuntimeError(
                f"Mô hình Whisper '{whisper_model}' chỉ hỗ trợ tiếng Anh, "
                f"không thể dùng cho {lang_mod.display_name(requested, 'vi')}. "
                "Hãy chọn mô hình đa ngôn ngữ (small, medium, ...)."
            )
        info = {
            "code": requested,
            "name": language_name(requested),
            "probability": 1.0,
            "method": "manual",
            "uncertain": False,
            "mixed": False,
            "windows": [],
            "multilingual_used": False,
        }
        if hasattr(job, "state") and isinstance(job.state, dict):
            job.state["source_language"] = info
            if hasattr(job, "save"):
                job.save()
        _emit_language_notice(info, config)
        return info

    if not hasattr(model, "detect_language"):
        return {
            "code": "en",
            "name": "English",
            "probability": 1.0,
            "method": "auto",
            "uncertain": False,
            "mixed": False,
            "windows": [],
            "multilingual_used": False,
        }

    if audio is None:
        from faster_whisper import decode_audio

        audio = decode_audio(str(job.audio_path), sampling_rate=16000)

    win_slices = _select_vad_windows(audio, speech_regions=speech_regions)
    win_records: list[dict[str, Any]] = []
    prob_sums: dict[str, float] = {}
    win_top_counts: dict[str, int] = {}

    for start_s, w_audio in win_slices:
        w_lang, w_prob, all_probs = _call_detect_language(model, w_audio)
        win_records.append({
            "start": start_s,
            "language": w_lang,
            "probability": round(w_prob, 4),
        })
        win_top_counts[w_lang] = win_top_counts.get(w_lang, 0) + 1
        if all_probs:
            for code, p in all_probs:
                c = str(code).lower()
                prob_sums[c] = prob_sums.get(c, 0.0) + float(p)
        else:
            prob_sums[w_lang] = prob_sums.get(w_lang, 0.0) + w_prob

    n_win = max(1, len(win_records))
    mean_probs = {c: s / n_win for c, s in prob_sums.items()}
    ranked = sorted(mean_probs.items(), key=lambda kv: kv[1], reverse=True)
    best_code, best_prob = ranked[0] if ranked else ("en", 0.0)

    # Uncertain if best_prob < min_conf OR two languages each appear in >= 25% of windows.
    significant_langs = [c for c, cnt in win_top_counts.items() if (cnt / n_win) >= 0.25]
    mixed = len(significant_langs) >= 2
    uncertain = (best_prob < min_conf) or mixed

    candidates = [
        {"code": c, "name": language_name(c), "probability": round(p, 4)}
        for c, p in ranked[:3]
    ]

    method = "auto"
    if uncertain:
        dialog_cb = getattr(config, "language_dialog", None)
        if callable(dialog_cb):
            chosen = dialog_cb(candidates, mixed)
            if not chosen:
                from dubtool.api import StageCancelled

                raise StageCancelled("user cancelled language selection")
            best_code = str(chosen).strip().lower()
            best_prob = float(mean_probs.get(best_code, best_prob))
            method = "auto_confirmed"
        else:
            log.warning(
                "s2: uncertain source language detection (best=%s %.1f%%, mixed=%s, windows=%s) — continuing with %s",
                best_code, best_prob * 100.0, mixed, win_records, best_code,
            )

    if lang_mod.is_english_only_whisper_model(whisper_model) and best_code != "en":
        raise RuntimeError(
            f"Mô hình Whisper '{whisper_model}' chỉ hỗ trợ tiếng Anh, "
            f"không thể dùng cho {lang_mod.display_name(best_code, 'vi')}. "
            "Hãy chọn mô hình đa ngôn ngữ (small, medium, ...)."
        )

    info = {
        "code": best_code,
        "name": language_name(best_code),
        "probability": round(best_prob, 4),
        "method": method,
        "uncertain": bool(uncertain),
        "mixed": bool(mixed),
        "candidates": candidates,
        "windows": win_records,
        "multilingual_used": bool(mixed),
    }
    if hasattr(job, "state") and isinstance(job.state, dict):
        job.state["source_language"] = info
        if hasattr(job, "save"):
            job.save()
    config.asr["language"] = best_code
    log.info(
        "s2: detected language=%s (%.1f%%, windows=%d, uncertain=%s, mixed=%s)",
        best_code, best_prob * 100.0, len(win_records), uncertain, mixed,
    )
    _emit_language_notice(info, config)
    return info


def _transcribe(model, job, config, *, multilingual: bool = False) -> None:
    """Whisper pass + cleanup + write transcript.json (AGU6 4.2 split)."""
    want_words = bool(config.asr.get("word_timestamps", True))
    lang = config.asr.get("language")
    effective_lang = lang if lang and lang != "auto" else None
    kwargs: dict[str, Any] = {
        "language": None if multilingual else effective_lang,
        "vad_filter": True,
        "condition_on_previous_text": bool(
            config.asr.get("condition_on_previous_text", False)
        ),
    }
    if multilingual:
        kwargs["multilingual"] = True
    if config.asr.get("initial_prompt") and (effective_lang == "en") and not multilingual:
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
        retried = False
        if "multilingual" in kwargs and "multilingual" in str(exc):
            log.warning("s2: faster-whisper does not accept multilingual (%s) — continuing single-language", exc)
            kwargs.pop("multilingual", None)
            kwargs["language"] = effective_lang
            retried = True
        if want_words and "word_timestamps" in str(exc):
            log.warning(
                "s2: faster-whisper %s does not accept word_timestamps (%s) "
                "— continuing without word times", _fw_version(), exc,
            )
            kwargs.pop("word_timestamps", None)
            retried = True
        if not retried:
            raise
        raw_segments, info = model.transcribe(str(job.audio_path), **kwargs)
        segments, words = _collect(raw_segments, _asr_progress_cb(info.duration))
    if not segments:
        raise RuntimeError("transcription produced no segments — is the audio silent?")

    resolved_code = (effective_lang or getattr(info, "language", None) or "en").lower()
    if resolved_code in _SPACELESS_LANGS:
        cleaned = merge_short(split_long(segments))
        removals: list[tuple[int, str]] = []
        for i, seg in enumerate(cleaned):
            seg["id"] = i
    else:
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
    if hasattr(job, "state") and isinstance(job.state, dict) and "source_language" not in job.state:
        job.state["source_language"] = {
            "code": resolved_code,
            "name": language_name(resolved_code),
            "probability": float(getattr(info, "language_probability", 1.0) or 1.0),
            "method": "manual" if effective_lang else "auto",
            "uncertain": False,
            "mixed": False,
            "windows": [],
            "multilingual_used": bool(multilingual),
        }
        if hasattr(job, "save"):
            job.save()
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
    model = None
    try:
        model = WhisperModel(
            config.whisper_model,
            device=config.device,
            compute_type=config.compute_type,
            cpu_threads=threads,
        )

        lang_info = detect_source_language(model, job, config)
        min_conf = float(config.asr.get("language_min_confidence", 0.60) or 0.60)
        _transcribe(model, job, config, multilingual=bool(lang_info.get("mixed")))
        detected_code = str(lang_info.get("code") or "en").lower()
        detected_prob = float(lang_info.get("probability") or 0.0)
        if detected_code == "vi" and (detected_prob >= min_conf or lang_info.get("method") == "manual"):
            raise VietnameseSourceDetected(detected_prob, transcript_path=job.transcript_path)
    finally:
        # Release Whisper weights before Stage 4 loads VieNeu ONNX into RAM.
        del model
        import gc as _gc

        _gc.collect()
        cpu_mod.restore_app_priority()
