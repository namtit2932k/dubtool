"""AGENT_UPDATE_8 task 5.2: truncation/shrink detector (pure functions).

A translated unit is flagged when the Vietnamese is likely incomplete:
- the English ends with sentence-final punctuation but the Vietnamese does
  not ("incomplete");
- the Vietnamese ends on a connector/function word ("dangling");
- the Vietnamese is less than half the English length for long units
  ("shrunk").

Nothing here claims the translation is wrong — the flags go to the
re-translation pass and to the user-visible quality report.
"""

from __future__ import annotations

import re

# AGU8 6.2: the configurable connector list (config translate.connectors).
DEFAULT_CONNECTORS = {
    "luôn", "và", "mà", "nhưng", "vì", "nếu", "rằng", "là", "của", "để",
    "một", "những", "các", "thì", "với", "cho", "khi", "như", "hay",
    "hoặc", "bởi",
}

_SHRINK_RATIO = 0.5
_SHRINK_MIN_LEN = 40

_WORD = re.compile(r"[\w'\u2019]+", re.UNICODE)

FINAL_PUNCT = (".", "!", "?", "…", ":::")


def _ends_sentence(text: str) -> bool:
    return (text or "").rstrip().endswith(FINAL_PUNCT)


def _last_word(text: str) -> str:
    words = _WORD.findall(text or "")
    return words[-1].lower() if words else ""


def flag_unit(en: str, vi: str, connectors: set[str] | None = None) -> str | None:
    """One reason string or None. connectors defaults to DEFAULT_CONNECTORS."""
    en = (en or "").strip()
    vi = (vi or "").strip()
    if not en or not vi:
        return None
    conn = connectors if connectors is not None else DEFAULT_CONNECTORS
    if _ends_sentence(en) and not _ends_sentence(vi):
        return "incomplete"
    if _last_word(vi) in conn:
        return "dangling"
    if len(en) > _SHRINK_MIN_LEN and len(vi) < _SHRINK_RATIO * len(en):
        return "shrunk"
    return None


def flagged_entries(
    entries: list[dict], connectors: set[str] | None = None
) -> list[dict]:
    """Every translated entry with a flag: [{id, en, vi, reason, max_chars}]."""
    out: list[dict] = []
    for e in entries or []:
        en = str(e.get("text") or "")
        vi = str(e.get("vi") or "")
        if not vi.strip() or e.get("reviewed"):
            continue  # empty or already user-approved: never re-flagged
        reason = flag_unit(en, vi, connectors)
        if reason:
            out.append({
                "id": e["id"],
                "en": en,
                "vi": vi,
                "reason": reason,
                "max_chars": e.get("max_chars"),
            })
    return out


# AGU8 6.2: the re-translation instruction (one batch, +30% allowance).
RETRANSLATE_SYSTEM = (
    "You complete Vietnamese subtitle translations that came back "
    "incomplete. The previous output was cut short: reproduce the FULL "
    "meaning of the English, keep it one complete sentence, keep the "
    "pronouns already used, never add information the English does not "
    "have. Reply with ONLY a JSON array of "
    "{\"id\": <int>, \"vi\": \"<complete text>\"}, one per input id, "
    "exactly the same ids."
)

RETRANSLATE_USER = """The previous Vietnamese for each fragment was incomplete ({reason}). Write a COMPLETE new Vietnamese translation that keeps all of the English meaning, a complete sentence ending with the same sentence punctuation as the English. Character allowance: up to {chars} characters (30% more than before).

Fragments:
{segments}

Reply with ONLY a JSON array: [{{\"id\": <int>, \"vi\": \"<str>\"}}, ...] with exactly the ids provided, in the same order."""


def render_retranslate_batch(items: list[dict]) -> str:
    """items: [{"id", "text", "vi", "max_chars"}] — flagged entries."""
    segments = [
        {
            "id": it["id"],
            "en": it["text"],
            "prev_vi": it["vi"],
            "max_chars": round(float(it.get("max_chars") or 0) * 1.3),
        }
        for it in items
    ]
    return RETRANSLATE_USER.format(
        reason="cắt ngắn/mất nghĩa",
        chars=max(round(float(items[0].get("max_chars") or 0) * 1.3), 1)
        if items else 30,
        segments=__import__("json").dumps(segments, ensure_ascii=False, indent=2),
    )
