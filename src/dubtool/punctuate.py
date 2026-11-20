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

from dubtool import languages as lang_mod

log = logging.getLogger("dubtool")

MAX_SEGMENT_S = 12.0
MIN_SEGMENT_S = 0.8
ABBREV_TAIL = re.compile(r"(\w\w+)\.+$")  # last word carries the punctuation
ABBREV = set(lang_mod.ENGLISH_ABBREVIATIONS)

_WORD_RE = re.compile(r"[\w'’]+", re.UNICODE)


def word_tokens(text: str) -> list[str]:
    """Lowercased word tokens, punctuation removed."""
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def fingerprint(text: str, lang: str = "en") -> list[str] | str:
    """Token sequence for punctuation-only validation. For scripts without
    spaces (zh, ja, th, ...), compare the sequence of non-punctuation chars."""
    if lang_mod.is_spaceless(lang):
        return lang_mod.non_punct_chars(text)
    return word_tokens(text)


def ends_sentence(text: str, lang: str = "en") -> bool:
    return lang_mod.ends_sentence(text, lang=lang)


def unpunct_stats(segments: list[dict[str, Any]], lang: str = "en") -> dict:
    """Share of segments without sentence-final punctuation and the longest
    run of consecutive unpunctuated segments."""
    flags = [not ends_sentence((s.get("text") or "").strip(), lang=lang) for s in segments]
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
    lang: str = "en",
) -> bool:
    """`punctuate.enabled: auto` trigger (AGU8 4.3). Disabled for scripts
    without sentence punctuation (Thai, Lao, Khmer)."""
    if lang_mod.is_unpunctuated_script(lang):
        return False
    st = unpunct_stats(segments, lang=lang)
    return st["ratio"] > max_missing_ratio or (st["longest_run"] > max_unpunct_run)


PUNCTUATE_SYSTEM = (
    "You restore punctuation and capitalization in English transcripts. "
    "Add punctuation, capitalization, and sentence breaks only. Never add, "
    "remove, reorder, or change any word. Reply with a JSON object "
    '{\"lines\": [...]} containing exactly one corrected line per input line '
    "in the same order."
)


def punctuate_system_prompt(lang: str = "en") -> str:
    if lang_mod.is_english(lang):
        return PUNCTUATE_SYSTEM
    name = lang_mod.english_name(lang)
    return (
        f"You restore punctuation in {name} transcripts. "
        "Add punctuation and sentence breaks only (plus capitalization where "
        "applicable to the script). Never add, remove, reorder, or change any "
        'word or character. Reply with a JSON object {"lines": [...]} '
        "containing exactly one corrected line per input line in the same order."
    )


def _window_user(texts: list[str], lang: str = "en") -> str:
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    name = lang_mod.english_name(lang)
    return (
        f"Restore punctuation in each numbered {name} line. Keep every word "
        "and character exactly as written, same order.\n\n" + lines
    )


def _validate_window(texts: list[str], lang: str = "en") -> Callable[[str], list[str]]:
    """Validator factory: parse {"lines": [...]}, require one line per input
    segment and the same word/character sequences; raise LLMError on mismatch."""

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
            if fingerprint(fixed, lang=lang) != fingerprint(original, lang=lang):
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


def _is_service_unavailable(exc: Exception) -> bool:
    """True when the failure looks like a provider/server outage (503/504/429/timeout/network)
    rather than a reply-format mismatch on one window."""
    from dubtool.llm.base import (
        NetworkLLMError,
        RateLimitError,
        RateLimitExhausted,
        ServerErrorLLMError,
        TimeoutLLMError,
        _walk,
    )

    for e in _walk(exc):
        if isinstance(e, (ServerErrorLLMError, TimeoutLLMError, NetworkLLMError, RateLimitError, RateLimitExhausted)):
            return True
    msg = str(exc).lower()
    return any(tok in msg for tok in ("503", "504", "unavailable", "rate limit", "timed out", "timeout"))


def restore_punctuation(
    segments: list[dict[str, Any]], client, cfg: dict | None = None,
    lang: str = "en", cancel=None,
) -> tuple[int, int]:
    """AGU8 4.3: punctuate in windows of about `window_segments` segments.

    Returns (windows_accepted, windows_rejected); segments are updated in
    place for accepted windows. The client is a Fast-model LLMClient; every
    window counts one request against llm.max_requests_per_run.
    """
    from dubtool.job import StageCancelled

    cfg = cfg or {}
    window = max(5, int(cfg.get("window_segments", 40)))
    max_consec_outage = max(1, int(cfg.get("max_consecutive_server_errors", 2)))
    cancel_ev = cancel if cancel is not None else getattr(client, "cancel", None)
    total = len(segments)
    accepted = rejected = 0
    consec_outage = 0
    sys_prompt = punctuate_system_prompt(lang)
    for start in range(0, total, window):
        if cancel_ev is not None and cancel_ev.is_set():
            raise StageCancelled("punctuate: cancelled by user")
        chunk = segments[start : start + window]
        texts = [(s.get("text") or "").strip() for s in chunk]
        validate = _validate_window(texts, lang=lang)
        try:
            corrected = client._call_and_validate(
                sys_prompt, _window_user(texts, lang=lang), validate, _note
            )
        except StageCancelled:
            raise
        except Exception as exc:
            if cancel_ev is not None and cancel_ev.is_set():
                raise StageCancelled("punctuate: cancelled by user") from exc
            log.warning(
                "punctuate: window %d-%d rejected (%s) — keeping original text",
                start, start + len(chunk), exc,
            )
            rejected += 1
            if _is_service_unavailable(exc):
                consec_outage += 1
                if consec_outage >= max_consec_outage:
                    remaining_windows = max(0, ((total - (start + len(chunk))) + window - 1) // window)
                    if remaining_windows > 0:
                        log.warning(
                            "punctuate: %d consecutive service/server failures — skipping %d remaining window(s) and keeping original text",
                            consec_outage, remaining_windows,
                        )
                        rejected += remaining_windows
                    break
            else:
                consec_outage = 0
            continue
        consec_outage = 0
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


def _slice_text_by_word_groups(
    text: str, groups: list[list[dict]], *, spaceless: bool
) -> list[str]:
    """Slice ``text`` into one substring per word group, preserving punctuation."""
    joiner = "" if spaceless else " "
    fallback = [
        joiner.join((w.get("w") or "").strip() for w in g).strip()
        for g in groups
    ]
    raw = (text or "").strip()
    if not raw or len(groups) <= 1:
        return [raw] if raw else fallback

    if spaceless:
        targets = [
            len(lang_mod.non_punct_chars("".join((w.get("w") or "") for w in g)))
            for g in groups
        ]
        total_need = sum(targets)
        if total_need == 0 or total_need != len(lang_mod.non_punct_chars(raw)):
            return fallback
        parts: list[str] = []
        pos = 0
        for g_idx, need in enumerate(targets):
            if g_idx == len(targets) - 1:
                parts.append(raw[pos:].strip())
                break
            start_pos = pos
            seen = 0
            while pos < len(raw) and seen < need:
                if lang_mod.non_punct_chars(raw[pos]):
                    seen += 1
                pos += 1
            # Attach trailing punctuation belonging to this clause.
            while pos < len(raw) and not lang_mod.non_punct_chars(raw[pos]):
                pos += 1
            parts.append(raw[start_pos:pos].strip())
        if all(parts):
            return parts
        return fallback

    # Space-delimited languages: match by word token counts.
    token_matches = list(_WORD_RE.finditer(raw))
    counts = [
        sum(len(_WORD_RE.findall(w.get("w") or "")) for w in g)
        for g in groups
    ]
    if sum(counts) != len(token_matches) or any(c <= 0 for c in counts):
        return fallback
    parts = []
    tok_idx = 0
    char_pos = 0
    for g_idx, cnt in enumerate(counts):
        if g_idx == len(counts) - 1:
            parts.append(raw[char_pos:].strip())
            break
        end_tok = token_matches[tok_idx + cnt - 1]
        cut = end_tok.end()
        while cut < len(raw) and not raw[cut].isspace():
            cut += 1
        parts.append(raw[char_pos:cut].strip())
        char_pos = cut
        tok_idx += cnt
    return parts if all(parts) else fallback


def repair_word_timings(
    segments: list[dict[str, Any]],
    words: list[dict] | None,
    *,
    orphan_gap_s: float = 1.2,
    split_gap_s: float = 1.5,
    lang: str = "en",
) -> tuple[list[dict[str, Any]], list[dict], bool]:
    """Repair Whisper word-timestamp glitches that cause multi-second dub drift.

    Fixes two common faster-whisper artifacts:
      1. Leading orphan word: the first 1-2 short tokens of a new utterance get
         pinned to the end of the previous utterance before a long silence
         (e.g. a 6-19 s gap), shifting the entire dubbed sentence early. Those
         leading tokens are snapped right before the main utterance body.
      2. Internal long pauses: two utterances separated by >= ``split_gap_s``
         of silence inside a single segment are split into separate segments
         at the pause boundary so each utterance starts when the speaker talks.
    """
    if not segments or not words:
        return [dict(s) for s in segments], list(words or []), False

    spaceless = lang_mod.is_spaceless(lang)
    repaired_words = [dict(w) for w in words]
    out: list[dict[str, Any]] = []
    changed = False
    prev_end = 0.0

    for seg in segments:
        s_copy = dict(seg)
        sw = _segment_words(s_copy, repaired_words)
        if len(sw) < 2:
            out.append(s_copy)
            prev_end = max(prev_end, float(s_copy["end"]))
            continue

        # 1. Leading orphan word snap (1-2 very short tokens before a long gap).
        max_head_idx = 1 if (spaceless and len(sw) >= 4) else 0
        for k in range(max_head_idx, -1, -1):
            if len(sw) - (k + 1) < 2:
                continue
            gap = float(sw[k + 1]["start"]) - float(sw[k]["end"])
            if gap < orphan_gap_s:
                continue
            head_txt = "".join((w.get("w") or "").strip() for w in sw[: k + 1])
            short_enough = (len(head_txt) <= 3) if spaceless else (k == 0)
            if not short_enough:
                continue
            anchor = float(sw[k + 1]["start"])
            cursor = anchor
            for idx in range(k, -1, -1):
                orig_dur = max(0.08, min(0.30, float(sw[idx]["end"]) - float(sw[idx]["start"])))
                new_end = cursor
                new_start = max(prev_end, new_end - orig_dur)
                sw[idx]["start"] = round(new_start, 3)
                sw[idx]["end"] = round(new_end, 3)
                cursor = new_start
            s_copy["start"] = float(sw[0]["start"])
            changed = True
            log.info(
                "timing: snapped leading orphan token %r forward by %.2fs to %.2fs",
                head_txt, gap, s_copy["start"],
            )
            break

        # 2. Split on remaining internal pauses >= split_gap_s.
        groups: list[list[dict]] = []
        cur: list[dict] = [sw[0]]
        for w in sw[1:]:
            if float(w["start"]) - float(cur[-1]["end"]) >= split_gap_s:
                groups.append(cur)
                cur = [w]
            else:
                cur.append(w)
        if cur:
            groups.append(cur)

        if len(groups) > 1:
            texts = _slice_text_by_word_groups(
                str(s_copy.get("text") or ""), groups, spaceless=spaceless
            )
            for grp, txt in zip(groups, texts):
                if not txt:
                    continue
                piece = dict(s_copy)
                piece["start"] = float(grp[0]["start"])
                piece["end"] = float(grp[-1]["end"])
                piece["text"] = txt
                out.append(piece)
                prev_end = max(prev_end, piece["end"])
            changed = True
        else:
            w_start = float(sw[0]["start"])
            w_end = float(sw[-1]["end"])
            if w_start - float(s_copy["start"]) >= 1.0:
                s_copy["start"] = w_start
                changed = True
            if float(s_copy["end"]) - w_end >= 1.0:
                s_copy["end"] = w_end
                changed = True
            out.append(s_copy)
            prev_end = max(prev_end, float(s_copy["end"]))

    for i, seg in enumerate(out):
        seg["id"] = i
    return out, repaired_words, changed


def resegment_by_sentences(
    segments: list[dict[str, Any]], words: list[dict],
    max_s: float = MAX_SEGMENT_S, min_s: float = MIN_SEGMENT_S,
    lang: str = "en",
) -> list[dict[str, Any]] | None:
    """AGU8 4.2 / Update 16: cut segments into sentences at sentence-final marks
    using word/token timestamps. Returns the new segment list (ids renumbered
    sequentially), or None when the word times cannot cover the text."""

    if not words or not segments:
        return None
    spaceless = lang_mod.is_spaceless(lang)
    joiner = "" if spaceless else " "
    new: list[dict[str, Any]] = []
    covered = 0
    for seg in segments:
        seg_words = _segment_words(seg, words)
        if spaceless:
            joined_w = lang_mod.non_punct_chars("".join((w.get("w") or "") for w in seg_words))
            expected_w = lang_mod.non_punct_chars(seg.get("text") or "")
            if not seg_words or joined_w != expected_w:
                log.info(
                    "s2: resegment skipped (spaceless token alignment lost) near %.1fs",
                    float(seg["start"]),
                )
                return None
        else:
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
        # Sentence boundaries after a token that ends a sentence.
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for w in seg_words:
            current.append(w)
            token = (w.get("w") or "").strip()
            if token and _ends_sentence_token(token, lang=lang):
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
            text = joiner.join((w.get("w") or "").strip() for w in piece).strip()
            start = float(piece[0]["start"])
            end = float(piece[-1]["end"])
            if new and end - start < min_s and (start - float(new[-1]["end"])) <= 0.5:
                new[-1]["text"] = (new[-1]["text"] + joiner + text).strip()
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


def _ends_sentence_token(token: str, lang: str = "en") -> bool:
    s = lang_mod.strip_closers(token)
    if not s or s[-1] not in lang_mod.SENTENCE_FINAL_CHARS:
        return False
    if lang_mod.is_english(lang):
        if len(s) < 2:
            return False
        m = ABBREV_TAIL.search(s)
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
