"""AGENT_UPDATE_8 task 3: punctuation restoration and sentence re-segmentation.

Pure helpers plus one LLM step:

- `needs_punctuation` decides when the transcript is unpunctuated enough to
  bother (`punctuate.enabled: auto` triggers on >30% unpunctuated segments or
  a run of >25 words without a sentence-final mark).
- `restore_punctuation` sends ~40-segment windows to the Fast model with
  strict instructions (punctuation/capitalization only, unchanged words) and
  rejects any window whose word sequence changed (lowercased, punctuation
  removed). Rejected windows keep the original text.
- `resegment_by_sentences` cuts the segments at sentence-final marks using
  the word timestamps from `words.json`, so a segment is a sentence (or a
  clause capped at MAX_SEGMENT_S via commas or pauses). Ids are renumbered
  sequentially; the caller must re-run merge/split/dedupe rules on the same
  pipeline (s2 cleanup helpers are reused).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

log = logging.getLogger("dubtool")

MAX_SEGMENT_S = 12.0
MIN_SEGMENT_S = 0.8
ABBREV_TAIL = re.compile(r"(\w\w+)\.+$")  # last word carries the punctuation
ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg",
    "ie", "no", "approx", "fig", "al", "inc", "ltd", "co",
}

_WORD_RE = re.compile(r"[\w'’]+", re.UNICODE)


def word_tokens(text: str) -> list[str]:
    """Lowercased word tokens, punctuation removed."""
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def fingerprint(text: str) -> list[str]:
    return word_tokens(text)


def ends_sentence(text: str) -> bool:
    stripped = (text or "").rstrip()
    if not stripped or stripped[-1] not in ".!?…":
        return False
    m = ABBREV_TAIL.search(stripped)
    if m and m.group(1).lower() in ABBREV:
        return False
    core = m.group(1).lower() if m else ""
    if core and len(core) == 1 and core.isalpha():
        return False  # an initial: "John D."
    return True


def unpunct_stats(segments: list[dict[str, Any]]) -> dict:
    """Share of segments without sentence-final punctuation and the longest
    run of consecutive unpunctuated segments."""
    flags = [not ends_sentence((s.get("text") or "").strip()) for s in segments]
    longest = 0
    run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return {
        "segments": len(segments),
        "unpunctuated": sum(flags),
        "ratio": (sum(flags) / len(flags)) if flags else 0.0,
        "longest_run": longest,
    }


def needs_punctuation(
    segments: list[dict[str, Any]],
    max_missing_ratio: float = 0.30,
    max_unpunct_run: int = 25,
) -> bool:
    """`punctuate.enabled: auto` trigger (AGU8 4.3)."""
    st = unpunct_stats(segments)
    return st["ratio"] > max_missing_ratio or (st["longest_run"] > max_unpunct_run)


PUNCTUATE_SYSTEM = (
    "You restore punctuation and capitalization in English transcripts. "
    "Add punctuation, capitalization, and sentence breaks only. Never add, "
    "remove, reorder, or change any word. Reply with a JSON object "
    '{\"lines\": [...]} containing exactly one corrected line per input line '
    "in the same order."
)


def _window_user(texts: list[str]) -> str:
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    return (
        "Restore punctuation in each numbered line. Keep every word "
        "exactly as written, same order.\n\n" + lines
    )


def _validate_window(texts: list[str]) -> Callable[[str], list[str]]:
    """Validator factory: parse {"lines": [...]}, require one line per input
    segment and the same word sequences; raise LLMError on any mismatch."""

    from dubtool.llm.base import LLMError

    def validate(reply: str) -> list[str]:
        data = json.loads(_extract_json_object(reply))
        lines = data.get("lines") if isinstance(data, dict) else None
        if not isinstance(lines, list) or len(lines) != len(texts):
            raise LLMError("reply misses one line per segment")
        out: list[str] = []
        for original, fixed in zip(texts, lines):
            if not isinstance(fixed, str) or not fixed.strip():
                raise LLMError("empty corrected line")
            if fingerprint(fixed) != fingerprint(original):
                from dubtool.llm.base import LLMError as _E

                raise _E(f"word sequence changed: {fixed!r}")
            out.append(fixed.strip())
        return out

    return validate


def _extract_json_object(reply: str) -> str:
    """Pull the first {...} block out of a possibly chatty reply."""
    text = (reply or "").strip()
    if text.startswith("{"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def restore_punctuation(
    segments: list[dict[str, Any]], client, cfg: dict | None = None
) -> tuple[int, int]:
    """AGU8 4.3: punctuate in windows of about `window_segments` segments.

    Returns (windows_accepted, windows_rejected); segments are updated in
    place for accepted windows. The client is a Fast-model LLMClient; every
    window counts one request against llm.max_requests_per_run.
    """
    cfg = cfg or {}
    window = max(5, int(cfg.get("window_segments", 40)))
    total = len(segments)
    accepted = rejected = 0
    for start in range(0, total, window):
        chunk = segments[start : start + window]
        texts = [(s.get("text") or "").strip() for s in chunk]
        validate = _validate_window(texts)
        try:
            corrected = client._call_and_validate(
                PUNCTUATE_SYSTEM, _window_user(texts), validate, _note
            )
        except Exception as exc:
            log.warning(
                "punctuate: window %d-%d rejected (%s) — keeping original text",
                start, start + len(chunk), exc,
            )
            rejected += 1
            continue
        for seg, fixed in zip(chunk, corrected):
            if fixed != seg.get("text"):
                seg["text"] = fixed
        accepted += 1
        log.info("punctuate: restored punctuation for %d segments", len(chunk))
    return accepted, rejected


def _note(exc: Exception) -> str:
    return (
        f"Your previous reply was invalid: {exc}. Follow the format exactly "
        "and reply again with only the JSON object."
    )


# ------------------------------------------------------------ resegmentation


def _segment_words(seg: dict[str, Any], words: list[dict]) -> list[dict]:
    """Words belonging to one segment: a word belongs where its midpoint
    falls into [start, end). Each word joins exactly one segment."""
    start, end = float(seg["start"]), float(seg["end"])
    picked = []
    for w in words:
        mid = (float(w["start"]) + float(w["end"])) / 2.0
        if start - 1e-6 <= mid < end:
            picked.append(w)
    return picked


def _split_long_sentence(
    chunk: list[dict], max_s: float = MAX_SEGMENT_S, pause_s: float = 0.3
) -> list[list[dict]]:
    """Cut a too-long sentence at commas, then at the largest pause, until
    every piece fits or nothing is splittable."""
    def dur(piece: list[dict]) -> float:
        return float(piece[-1]["end"]) - float(piece[0]["start"])

    pieces: list[list[dict]] = [chunk]
    out: list[list[dict]] = []
    while pieces:
        piece = pieces.pop(0)
        if dur(piece) <= max_s or len(piece) < 2:
            out.append(piece)
            continue
        cut = None
        # Prefer a comma close to the middle.
        mid = dur(piece) / 2 + float(piece[0]["start"])
        commas = [
            i for i, w in enumerate(piece[:-1])
            if (w.get("w") or "").strip().endswith(",")
        ]
        if commas:
            best = min(commas, key=lambda i: abs(float(piece[i]["end"]) - mid))
            if float(piece[best]["end"]) - float(piece[0]["start"]) >= MIN_SEGMENT_S \
                    and dur(piece[best + 1:]) >= MIN_SEGMENT_S:
                cut = best
        if cut is None:
            # Largest internal pause.
            gaps = [
                (float(piece[i + 1]["start"]) - float(piece[i]["end"]), i)
                for i in range(len(piece) - 1)
            ]
            gap_val, i = max(gaps)
            if gap_val >= pause_s and \
                    float(piece[i]["end"]) - float(piece[0]["start"]) >= MIN_SEGMENT_S and \
                    dur(piece[i + 1:]) >= MIN_SEGMENT_S:
                cut = i
        if cut is None:
            out.append(piece)
            continue
        pieces.insert(0, piece[cut + 1 :])
        pieces.insert(0, piece[: cut + 1])
    return out


def resegment_by_sentences(
    segments: list[dict[str, Any]], words: list[dict],
    max_s: float = MAX_SEGMENT_S, min_s: float = MIN_SEGMENT_S,
) -> list[dict[str, Any]] | None:
    """AGU8 4.2: cut segments into sentences at sentence-final marks using
    the word timestamps. Returns the new segment list (ids renumbered
    sequentially), or None when the word times cannot cover the text (then
    the caller keeps the original segments)."""

    if not words or not segments:
        return None
    new: list[dict[str, Any]] = []
    covered = 0
    for seg in segments:
        seg_words = _segment_words(seg, words)
        text_tokens = _WORD_RE.findall(seg.get("text") or "")
        if len(seg_words) != len(text_tokens):
            log.info(
                "s2: resegment skipped (word/text alignment lost) near %.1fs",
                float(seg["start"]),
            )
            return None
        covered += len(seg_words)
        if not seg_words:
            new.append({"start": seg["start"], "end": seg["end"], "text": seg["text"]})
            continue
        # Sentence boundaries after a word that ends a sentence.
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for w in seg_words:
            current.append(w)
            token = (w.get("w") or "").strip()
            if token and _ends_sentence_token(token):
                chunks.append(current)
                current = []
        if current:
            if chunks and len(current) <= 2:
                chunks[-1].extend(current)  # keep short tails with the sentence
            else:
                chunks.append(current)
        # Cap at max_s via commas or pauses.
        pieces: list[list[dict]] = []
        for chunk in chunks:
            pieces.extend(_split_long_sentence(chunk, max_s=max_s))
        # Merge pieces shorter than min_s into a neighbor with the same tail.
        for piece in pieces:
            text = " ".join((w.get("w") or "").strip() for w in piece).strip()
            start = float(piece[0]["start"])
            end = float(piece[-1]["end"])
            if new and end - start < min_s:
                new[-1]["text"] = (new[-1]["text"] + " " + text).strip()
                new[-1]["end"] = end
            else:
                new.append({"start": start, "end": end, "text": text})
    if covered == 0:
        return None
    out = [
        {"id": i, "start": seg["start"], "end": seg["end"], "text": seg["text"]}
        for i, seg in enumerate(new)
    ]
    return out


def _ends_sentence_token(token: str) -> bool:
    if len(token) < 2 or token[-1] not in ".!?…":
        return False
    m = ABBREV_TAIL.search(token)
    if m and (m.group(1).lower() in ABBREV
              or (len(m.group(1)) == 1 and m.group(1).isalpha())):
        return False
    return True


def write_words(job, words: list[dict]) -> None:
    from dubtool.job import write_json

    write_json(job.dir / "words.json", words)


def read_words(job) -> list[dict] | None:
    path = job.dir / "words.json"
    if not path.exists():
        return None
    from dubtool.job import read_json

    data = read_json(path)
    return data if isinstance(data, list) and data else None
