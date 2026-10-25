"""Stage 7 helpers: SRT cue timing, wrapping, rendering, font selection.

AGENT_UPDATE_3.md section 2: libass computes FontSize/MarginV/Outline for SRT
on a virtual 288-px-high frame and scales by video_height/288, so stage 7
decides intended *pixel* sizes first and converts with px_to_ass_units.
Cues longer than 2 x max_line_chars are split into consecutive cues with
proportional timing. UTF-8 with Vietnamese diacritics intact throughout.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("dubtool")

MAX_LINE_CHARS = 42
PORTRAIT_LINE_CHARS = 28
MAX_LINES = 2
NEXT_CUE_GAP_S = 0.05
MIN_CUE_S = 1.0
CUE_TAIL_S = 0.2

CUT_PUNCTUATION = ".,;:?!…"

# Family -> font files, in fallback order. The family name passed to libass
# must match the family of the file actually found (never mix 2.2).
FONT_FILES: list[tuple[str, list[str]]] = [
    ("Arial", ["arial.ttf", "arialbd.ttf"]),
    ("Segoe UI", ["segoeui.ttf", "segoeuib.ttf"]),
    ("Tahoma", ["tahoma.ttf"]),
    ("Times New Roman", ["times.ttf"]),
    ("DejaVu Sans", ["DejaVuSans.ttf"]),
]


def px_to_ass_units(px: float, video_height: int) -> float:
    """Convert real-pixel sizes to libass SRT units (virtual 288px frame)."""
    return float(px) * LIBASS_SRT_PLAYRES_Y / float(video_height)


LIBASS_SRT_PLAYRES_Y = 288


def timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis = 999
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def compute_cue_end(
    start: float,
    end: float,
    clip_duration: float,
    next_start: float | None,
) -> float:
    """Pure timing rule (AGENT_UPDATE_2.md 3.2), used when no schedule exists."""
    lead_end = start + max(clip_duration, 0.0)
    cue_end = max(end, lead_end)
    upper = None if next_start is None else next_start - NEXT_CUE_GAP_S
    if upper is not None:
        cue_end = min(cue_end, upper)
    min_end = start + MIN_CUE_S
    if cue_end < min_end and (upper is None or min_end <= upper):
        cue_end = min_end
    return max(cue_end, start)


def cue_window(
    play_start: float,
    play_end: float,
    next_play_start: float | None,
) -> tuple[float, float]:
    """Cue timing from the schedule (AGENT_UPDATE_3.md 4.2): start at
    play_start, end at play_end + tail, capped before the next cue, min 1 s
    where the next cue allows it."""
    start = float(play_start)
    end = float(play_end) + CUE_TAIL_S
    upper = None if next_play_start is None else next_play_start - NEXT_CUE_GAP_S
    if upper is not None:
        end = min(end, upper)
    min_end = start + MIN_CUE_S
    if end < min_end and (upper is None or min_end <= upper):
        end = min_end
    return start, max(end, start)


def split_cue_text(text: str, max_line_chars: int) -> list[str]:
    """Split long text into parts each fitting one cue (max 2 lines).

    Cuts at the punctuation closest to the limit, else at a word boundary;
    never inside a word. Each part is non-empty.
    """
    text = " ".join(text.split())
    limit = max(int(2 * max_line_chars), 1)
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        # Prefer the punctuation closest to the limit that ends a word
        # (a following space or the end of the window):
        punct_cut = None
        best_dist = None
        for i, ch in enumerate(window):
            if ch in CUT_PUNCTUATION and (i + 1 >= len(window) or window[i + 1].isspace()):
                dist = abs(i + 1 - limit)
                if best_dist is None or dist < best_dist:
                    best_dist, punct_cut = dist, i + 1
        if punct_cut is not None:
            cut = punct_cut
        else:
            cut = window.rfind(" ")
            if cut <= 0:
                cut = limit + 1
        head = remaining[:cut].strip()
        remaining = remaining[cut:].strip()
        if head:
            parts.append(head)
        if not remaining:
            break
    if remaining:
        parts.append(remaining)
    return parts or [text]


def split_cue_times(char_counts: list[int], start: float, end: float) -> list[float]:
    """Distribute a cue's time span over its parts by character counts.

    Each part gets at least 1.0 s when the span allows it; the deficit is
    taken proportionally from the longer parts so the span stays intact.
    """
    n = len(char_counts)
    span = max(end - start, 0.0)
    total = max(sum(char_counts), 1)
    durations = [max(span * c / total, 0.0) for c in char_counts]
    if n == 0:
        return []
    if span >= n * MIN_CUE_S:
        # Boost short parts to MIN_CUE_S, pulling the deficit out of parts
        # that still have spare time above MIN_CUE_S.
        for _ in range(n * 4):
            short = [i for i, d in enumerate(durations) if d < MIN_CUE_S - 1e-9]
            if not short:
                break
            spare_parts = [i for i, d in enumerate(durations) if d > (MIN_CUE_S + 1e-9) and i not in short]
            if not spare_parts:
                break
            spare_total = sum(durations[i] - MIN_CUE_S for i in spare_parts)
            need = sum(MIN_CUE_S - durations[i] for i in short)
            take = min(need, spare_total)
            if take <= 0:
                break
            for i in spare_parts:
                give = take * (durations[i] - MIN_CUE_S) / spare_total
                durations[i] -= give
            for i in short:
                gain = take * ((MIN_CUE_S - durations[i]) / max(sum(MIN_CUE_S - durations[j] for j in short), 1e-9))
                durations[i] += gain
        # Round-off safety: keep within the span.
        drift = sum(durations) - span
        if drift > 0 and durations:
            durations[durations.index(max(durations))] -= drift
    return durations


def wrap_text(text: str, max_line_chars: int = MAX_LINE_CHARS) -> list[str]:
    """Wrap to at most 2 lines of <= max_line_chars, words kept intact."""
    text = " ".join(text.split())
    if len(text) <= max_line_chars:
        return [text]
    lines: list[str] = []
    remaining = text
    while len(remaining) > max_line_chars and len(lines) < MAX_LINES - 1:
        head = remaining[: max_line_chars + 1]
        cut = head.rfind(" ")
        if cut <= 0:
            cut = max_line_chars
        lines.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    lines.append(remaining)
    # Very long text: take the most balanced 2-line split available,
    # preferring one where both lines stay within the soft overflow bound.
    if len(lines) > MAX_LINES or max(len(x) for x in lines) > max_line_chars + 10:
        words = text.split()
        candidates: list[tuple[int, int, list[str]]] = []
        for i in range(1, len(words)):
            left = " ".join(words[:i])
            right = " ".join(words[i:])
            candidates.append((max(len(left), len(right)), len(left) + len(right), [left, right]))
        if candidates:
            bounded = [c for c in candidates if c[0] <= max_line_chars + 10]
            pool = bounded if bounded else candidates
            return min(pool, key=lambda c: c[0])[2]
    return lines[:MAX_LINES]


def default_line_chars(width: int, height: int, configured: int | None = None) -> int:
    """Configured value wins; portrait video defaults to 28, else 42."""
    if configured:
        return int(configured)
    if height > width:
        return PORTRAIT_LINE_CHARS
    return MAX_LINE_CHARS


def render_blocks(
    cues: list[tuple[float, float, str]],
    max_line_chars: int = MAX_LINE_CHARS,
) -> str:
    """Turn (start, end, text) cues into SRT text, splitting long cues."""
    blocks: list[str] = []
    for start, end, text in cues:
        text = " ".join((text or "").split())
        if not text:
            continue
        if len(text) > 2 * max_line_chars:
            parts = split_cue_text(text, max_line_chars)
            durations = split_cue_times([len(p) for p in parts], start, end)
        else:
            parts, durations = [text], [end - start]
        pos = start
        for part, dur in zip(parts, durations):
            blocks.append(
                f"{len(blocks) + 1}\n"
                f"{timestamp(pos)} --> {timestamp(pos + dur)}\n"
                + "\n".join(wrap_text(part, max_line_chars))
                + "\n"
            )
            pos += dur
    return "\n".join(blocks)


def build_srt(
    entries: list[dict],
    fitted_dir: Path | None = None,
    schedule: list[dict] | None = None,
    max_line_chars: int = MAX_LINE_CHARS,
) -> str:
    """Render the SRT from translated entries.

    Timing: with a schedule (AGENT_UPDATE_3.md 4.2) cues follow play_start /
    play_end; without one the AGENT_UPDATE_2.md rule (entry times + fitted
    clip duration) is used. Empty text never participates in next_* caps.
    """
    cues: list[tuple[float, float, str]] = []
    if schedule:
        sched = sorted((s for s in schedule if _vi_for(schedule, entries, s)), key=lambda s: s["play_start"])
        for index, s in enumerate(sched):
            entry = _entry_by_id(entries, s["id"])
            text = (entry.get("vi") or "").strip()
            if not text:
                continue
            next_start = (
                float(sched[index + 1]["play_start"]) if index + 1 < len(sched) else None
            )
            start, end = cue_window(s["play_start"], s["play_end"], next_start)
            cues.append((start, end, text))
    else:
        ordered = sorted(entries, key=lambda e: e["start"])
        for index, entry in enumerate(ordered):
            text = (entry.get("vi") or "").strip()
            if not text:
                continue
            start = float(entry["start"])
            end = float(entry["end"])
            clip_duration = 0.0
            if fitted_dir is not None:
                fitted = fitted_dir / f"{entry['id']}.wav"
                if fitted.exists():
                    from dubtool import audio_utils

                    try:
                        clip_duration = audio_utils.probe_duration(fitted)
                    except RuntimeError:
                        clip_duration = 0.0
            next_start = (
                float(ordered[index + 1]["start"])
                if index + 1 < len(ordered)
                else None
            )
            cues.append((start, compute_cue_end(start, end, clip_duration, next_start), text))
    return render_blocks(cues, max_line_chars)


def _entry_by_id(entries: list[dict], seg_id) -> dict:
    for entry in entries:
        if entry["id"] == seg_id:
            return entry
    return {"id": seg_id, "vi": None}


def _vi_for(schedule: list[dict], entries: list[dict], s: dict) -> bool:
    entry = _entry_by_id(entries, s["id"])
    return bool((entry.get("vi") or "").strip())


def filter_cue_for_diacritics(entries: list[dict]) -> dict | None:
    """Pick the first cue carrying conspicuous Vietnamese diacritics."""
    wanted = set("ắằẵẳặữềộỵỗốở")
    for entry in sorted(entries, key=lambda e: e["start"]):
        if set(entry.get("vi", "")) & wanted:
            return entry
    return None


# ------------------------------------------------------------ font lookup


def font_search_dirs() -> list[Path]:
    return [
        Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts",
        Path("C:\\Windows\\Fonts"),
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path("/Library/Fonts"),
    ]


def find_system_font(
    preferred: str = "Arial", search_dirs: list[Path] | None = None
) -> tuple[Path, str] | None:
    """Find a Vietnamese-capable system font via an explicit family->file map.

    Honors `preferred`: its files are tried first (recursively), then the
    fallback families in order. The returned family name matches the file
    actually found, never another family's file. Returns None when nothing
    is found.
    """
    families = list(FONT_FILES)
    families.sort(key=lambda item: item[0] != preferred)
    dirs = search_dirs if search_dirs is not None else font_search_dirs()
    for dirname in dirs:
        if not dirname.is_dir():
            continue
        for family, file_names in families:
            for file_name in file_names:
                matches = sorted(dirname.rglob(file_name))
                if matches:
                    return matches[0], family
    return None
