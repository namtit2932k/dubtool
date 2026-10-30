"""Maps run results and exceptions to short localized messages (AGU4 12).

The full technical text (error + repr, WITHOUT any secrets) is carried in
`detail` for the dialog's "Copy details" button and Export diagnostics.
"""

from __future__ import annotations

from dubtool.app import i18n

# DubResult.code -> i18n key
CODE_KEYS = {
    "invalid_key": "error.no_key",
    "invalid_key_value": "error.invalid_key",
    "quota": "error.quota",
    "model_not_found": "error.model_not_found",
    "network": "error.network",
    "tts_unavailable": "error.tts_unavailable",   # AGU8 2.4
    "timeout": "error.timeout",
    "internal": "error.internal",
    "disk_full": "error.disk_full",
    "ffmpeg_missing": "error.ffmpeg_missing",
    "invalid": "error.generic",
    "error": "error.generic",
    "internal": "error.internal",
    "cancelled": "error.cancelled",
    "output_exists": "error.generic",  # the UI asks about the conflict directly
}


def message_for_code(code: str) -> str:
    return i18n.tr(CODE_KEYS.get(code, "error.generic"))


def detail_text(error: str | None, detail: str | None) -> str:
    parts = []
    if error:
        parts.append(f"error: {error}")
    if detail:
        parts.append(f"detail: {detail}")
    return "\n".join(parts) or "(no details)"
