"""Sentence-level translation units (AGENT_UPDATE_3.md task 7.2).

The ASR cuts segments at arbitrary points; translating whole sentences and
dividing the Vietnamese back across the original segments avoids broken
halves. Both functions are pure and unit-testable.

build_units: closes a unit when the segment ends with sentence-final
punctuation (unless abbreviation), the gap to the next segment exceeds
max_gap_s, the unit reaches max_duration_s, or it has max_segments segments.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("dubtool")

SENTENCE_FINAL = set(".!?…" + "\u2026")  # includes the single-char ellipsis
CLOSERS = "\"'”’)】]"
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "vs", "eg", "ie", "prof", "sr", "jr", "st",
    "etc", "no", "approx", "fig", "al", "inc", "ltd", "co",
}

CUT_PUNCTUATION = set(".,;:?!…")


def _strip_closers(token: str) -> str:
    while token and token[-1] in CLOSERS:
        token = token[:-1]
    return token


def ends_with_comma(text: str) -> bool:
    """True when the final word token is a comma tail (with optional closers)."""
    tokens = (text or "").strip().split()
    if not tokens:
        return False
    token = _strip_closers(tokens[-1])
    return bool(token) and token[-1] == ","


def ends_with_sentence(text: str) -> bool:
    """True when the final word ends with sentence-final punctuation and is
    not an abbreviation or a single initial."""
    tokens = (text or "").strip().split()
    if not tokens:
        return False
    token = _strip_closers(tokens[-1])
    if not token or token[-1] not in SENTENCE_FINAL:
        return False
    core = token.rstrip(".…").rstrip()
    if not core:
        return False  # a bare "..." — treat as a sentence end, not abbreviation
    if core.lower() in ABBREVIATIONS:
        return False
    if len(core) == 1 and core.isalpha():
        return False  # an initial like "John D." or "Mr. X."
    return True


def build_units(
    segments: list[dict],
    max_segments: int = 4,
    max_duration_s: float = 14.0,
    max_gap_s: float = 1.0,
    pause_split_s: float = 0.5,
) -> list[dict]:
    """Group segments in order into translation units (pure)."""
    units: list[dict] = []
    current_seg_ids: list[int] = []
    current_start = None
    current_end = None

    def close(end_seg) -> None:
        nonlocal current_seg_ids, current_start, current_end
        if not current_seg_ids:
            return
        texts = [seg_by_id[s]["text"] for s in current_seg_ids]
        units.append(
            {
                "unit_id": len(units),
                "seg_ids": list(current_seg_ids),
                "text": " ".join(t.strip() for t in texts if t and t.strip()),
                "start": current_start,
                "end": current_end,
            }
        )
        current_seg_ids = []
        current_start = None
        current_end = None

    seg_by_id = {seg["id"]: seg for seg in segments}
    for index, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            log.warning("units: segment id=%s has empty text; skipped from units", seg["id"])
            close(seg)
            continue
        if not current_seg_ids:
            current_start = float(seg["start"])
        current_seg_ids.append(seg["id"])
        current_end = float(seg["end"])
        close_now = False
        if index == len(segments) - 1:
            close_now = True
        else:
            gap = float(segments[index + 1]["start"]) - float(seg["end"])
            duration = current_end - current_start
            if ends_with_sentence(text):
                close_now = True
            elif pause_split_s > 0 and gap >= pause_split_s and not ends_with_comma(text):
                # AGU8 4.4: a real pause closes the unit unless the previous
                # segment ends with a comma (continuation).
                close_now = True
            elif gap > max_gap_s:
                close_now = True
            elif duration >= max_duration_s:
                close_now = True
            elif len(current_seg_ids) >= max_segments:
                close_now = True
        if close_now:
            close(seg)
    return units


# ------------------------------------------------------- split translation

_PUNCT_TAIL = re.compile(r"[.,;:?!…]+$")


def _word_tokens(vi_text: str) -> list[str]:
    return vi_text.split()


def _char_offset(words: list[str], j: int) -> int:
    """Char position in the re-joined text after the first j words."""
    return sum(len(w) for w in words[:j]) + max(j - 1, 0)


def _ends_with_punct(word: str) -> bool:
    return bool(_PUNCT_TAIL.search(word))


def split_translation(vi_text: str, segments: list[dict]) -> list[str]:
    """Divide a unit's Vietnamese into exactly len(segments) parts.

    The target share of each part is its segment's share of the unit's total
    duration. Cuts only at word boundaries; punctuation cuts win when close
    to the ideal position. Re-joining with single spaces reproduces the
    input. When the Vietnamese has fewer words than segments, the fallback
    repeats the last word so no part is empty (logged as a degradation).
    """
    count = len(segments)
    words = _word_tokens(vi_text or "")
    if count <= 1:
        return [vi_text]
    if not words or not (vi_text or "").strip():
        return [""] * count  # nothing to say at all; caller handles this

    if len(words) < count:
        log.warning(
            "split_translation: %d Vietnamese words for %d segments; "
            "repeating the last word so no part is empty",
            len(words), count,
        )
        parts = []
        for i in range(count):
            parts.append(words[i] if i < len(words) else words[-1])
        return parts

    durations = [max(float(s["end"]) - float(s["start"]), 0.01) for s in segments]
    total = sum(durations)
    text_len = _char_offset(words, len(words))
    cuts: list[int] = []
    prev = 0
    for index in range(count - 1):
        ideal = text_len * (sum(durations[: index + 1]) / total)
        best_word = None
        best_dist = None
        best_punct = None
        best_punct_dist = None
        for j in range(prev + 1, len(words) - (count - 2 - index)):
            offset = _char_offset(words, j)
            dist = abs(offset - ideal)
            if best_dist is None or dist < best_dist:
                best_word, best_dist = j, dist
            if _ends_with_punct(words[j - 1]) and (best_punct is None or dist < best_punct_dist):
                best_punct, best_punct_dist = j, dist
        if best_punct is not None and best_punct_dist <= best_dist:
            cuts.append(best_punct)
        else:
            cuts.append(best_word)
        prev = cuts[-1]

    parts: list[str] = []
    start = 0
    for cut in [*cuts, len(words)]:
        parts.append(" ".join(words[start:cut]))
        start = cut
    return parts
