"""User-facing strings: vi is the default, en available."""

from __future__ import annotations

from dubtool.app.i18n.en import EN_STRINGS
from dubtool.app.i18n.vi import VI_STRINGS

STRINGS: dict[str, dict[str, str]] = {
    "vi": VI_STRINGS,
    "en": EN_STRINGS,
}

_LANG = "vi"


def set_language(lang: str) -> None:
    global _LANG
    if lang in STRINGS:
        _LANG = lang


def language() -> str:
    return _LANG


def tr(key: str, **kwargs) -> str:
    text = STRINGS.get(_LANG, STRINGS["vi"]).get(key) or STRINGS["en"].get(key)
    if text is None:
        return key
    return text.format(**kwargs) if kwargs else text


def keys() -> list[str]:
    return sorted(set(STRINGS["vi"]) | set(STRINGS["en"]))


__all__ = ["STRINGS", "VI_STRINGS", "EN_STRINGS", "set_language", "language", "tr", "keys"]
