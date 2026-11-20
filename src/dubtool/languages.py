"""Source-language catalog and language-aware helpers for DubTool.

Target language is always Vietnamese; source language defaults to ``auto``
(detected in stage 2) or any language code supported by faster-whisper.
"""

from __future__ import annotations

import unicodedata

# Curated languages shown in the main "Ngôn ngữ video" dropdown (native + Vietnamese).
# Each entry: (code, english_name, native_name, vietnamese_short_name)
CURATED_LANGUAGES: tuple[tuple[str, str, str, str], ...] = (
    ("en", "English", "English", "Tiếng Anh"),
    ("ja", "Japanese", "日本語", "Nhật"),
    ("zh", "Chinese", "中文", "Trung"),
    ("ko", "Korean", "한국어", "Hàn"),
    ("es", "Spanish", "Español", "Tây Ban Nha"),
    ("fr", "French", "Français", "Pháp"),
    ("de", "German", "Deutsch", "Đức"),
    ("ru", "Russian", "Русский", "Nga"),
    ("pt", "Portuguese", "Português", "Bồ Đào Nha"),
    ("it", "Italian", "Italiano", "Ý"),
    ("th", "Thai", "ภาษาไทย", "Thái"),
    ("hi", "Hindi", "हिन्दी", "Hindi"),
    ("ar", "Arabic", "العربية", "Ả Rập"),
    ("id", "Indonesian", "Bahasa Indonesia", "Indonesia"),
)

# Full Whisper language code -> English name table (covers all faster-whisper 1.2.1 codes).
WHISPER_LANGUAGES: dict[str, str] = {
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "as": "Assamese",
    "az": "Azerbaijani",
    "ba": "Bashkir",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bo": "Tibetan",
    "br": "Breton",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fo": "Faroese",
    "fr": "French",
    "gl": "Galician",
    "gu": "Gujarati",
    "ha": "Hausa",
    "haw": "Hawaiian",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "ht": "Haitian Creole",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "iw": "Hebrew",
    "ja": "Japanese",
    "jw": "Javanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "la": "Latin",
    "lb": "Luxembourgish",
    "ln": "Lingala",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mg": "Malagasy",
    "mi": "Maori",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "mt": "Maltese",
    "my": "Myanmar",
    "ne": "Nepali",
    "nl": "Dutch",
    "nn": "Nynorsk",
    "no": "Norwegian",
    "oc": "Occitan",
    "pa": "Punjabi",
    "pl": "Polish",
    "ps": "Pashto",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sa": "Sanskrit",
    "sd": "Sindhi",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sn": "Shona",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "su": "Sundanese",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "tg": "Tajik",
    "th": "Thai",
    "tk": "Turkmen",
    "tl": "Tagalog",
    "tr": "Turkish",
    "tt": "Tatar",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "yi": "Yiddish",
    "yo": "Yoruba",
    "yue": "Cantonese",
    "zh": "Chinese",
}

_VI_NAMES: dict[str, str] = {
    "en": "Tiếng Anh",
    "vi": "Tiếng Việt",
    "ja": "Tiếng Nhật",
    "zh": "Tiếng Trung",
    "yue": "Tiếng Quảng Đông",
    "ko": "Tiếng Hàn",
    "es": "Tiếng Tây Ban Nha",
    "fr": "Tiếng Pháp",
    "de": "Tiếng Đức",
    "ru": "Tiếng Nga",
    "pt": "Tiếng Bồ Đào Nha",
    "it": "Tiếng Ý",
    "th": "Tiếng Thái",
    "hi": "Tiếng Hindi",
    "ar": "Tiếng Ả Rập",
    "id": "Tiếng Indonesia",
    "ms": "Tiếng Mã Lai",
    "nl": "Tiếng Hà Lan",
    "pl": "Tiếng Ba Lan",
    "tr": "Tiếng Thổ Nhĩ Kỳ",
    "uk": "Tiếng Ukraina",
    "sv": "Tiếng Thụy Điển",
    "he": "Tiếng Do Thái",
    "iw": "Tiếng Do Thái",
    "fa": "Tiếng Ba Tư",
    "ur": "Tiếng Urdu",
    "lo": "Tiếng Lào",
    "km": "Tiếng Khmer",
    "my": "Tiếng Miến Điện",
    "tl": "Tiếng Tagalog",
}

_NATIVE_BY_CODE: dict[str, str] = {
    code: native for code, _en, native, _vi in CURATED_LANGUAGES
}
_NATIVE_BY_CODE["vi"] = "Tiếng Việt"

_VI_SHORT_BY_CODE: dict[str, str] = {
    code: vi_short for code, _en, _native, vi_short in CURATED_LANGUAGES
}

# Scripts without word spaces.
SPACELESS_LANGS: frozenset[str] = frozenset({"zh", "yue", "ja", "th", "lo", "km", "my"})

# Scripts that routinely lack sentence-final punctuation marks (split by pauses/caps).
UNPUNCTUATED_SCRIPTS: frozenset[str] = frozenset({"th", "lo", "km"})

# Right-to-left scripts for readable .docx formatting.
RTL_LANGS: frozenset[str] = frozenset({"ar", "he", "iw", "fa", "ur", "ps", "sd", "yi"})

# Sentence-final punctuation across Latin, Spanish (closing only), CJK, Arabic, Devanagari.
# Spanish inverted opening marks (¿ ¡) are intentionally NOT in this set.
SENTENCE_FINAL_CHARS: frozenset[str] = frozenset(".!?…。！？؟۔।")
COMMA_CHARS: frozenset[str] = frozenset(",，、،")
CLOSING_QUOTES_BRACKETS: frozenset[str] = frozenset("\"'”’)】]»」』》〉")

# English-only abbreviations (applied ONLY when source language is English).
ENGLISH_ABBREVIATIONS: frozenset[str] = frozenset({
    "mr", "mrs", "ms", "dr", "vs", "eg", "ie", "prof", "sr", "jr", "st",
    "etc", "no", "approx", "fig", "al", "inc", "ltd", "co",
})


def normalize_code(code: str | None, default: str = "en") -> str:
    c = (code or "").strip().lower()
    if not c or c == "auto":
        return default
    return c


def is_english(code: str | None) -> bool:
    return normalize_code(code, default="en") == "en"


def is_spaceless(code: str | None) -> bool:
    return normalize_code(code, default="en") in SPACELESS_LANGS


def is_unpunctuated_script(code: str | None) -> bool:
    return normalize_code(code, default="en") in UNPUNCTUATED_SCRIPTS


def is_rtl(code: str | None) -> bool:
    return normalize_code(code, default="en") in RTL_LANGS


def english_name(code: str | None) -> str:
    c = normalize_code(code, default="en")
    return WHISPER_LANGUAGES.get(c, c)


def vietnamese_name(code: str | None) -> str:
    c = normalize_code(code, default="en")
    return _VI_NAMES.get(c) or english_name(c)


def native_name(code: str | None) -> str:
    c = normalize_code(code, default="en")
    return _NATIVE_BY_CODE.get(c) or english_name(c)


def dropdown_label(code: str, ui_lang: str = "vi") -> str:
    c = normalize_code(code, default="en")
    nat = native_name(c)
    if ui_lang == "vi":
        vi_s = _VI_SHORT_BY_CODE.get(c)
        if vi_s and c not in ("en", "id"):
            return f"{nat} ({vi_s})"
        if c == "en":
            return "English"
        if c == "id":
            return "Bahasa Indonesia"
        vn = _VI_NAMES.get(c)
        if vn and vn != nat:
            return f"{nat} ({vn})"
        return nat
    en = english_name(c)
    if nat != en:
        return f"{nat} ({en})"
    return en


def display_name(code: str | None, ui_lang: str = "vi") -> str:
    """Human-readable language name for stage rows, summaries, and readable docs."""
    c = normalize_code(code, default="en")
    if ui_lang == "vi":
        return vietnamese_name(c)
    return english_name(c)


def supported_language_codes() -> list[str]:
    """Return every language code supported by the installed faster-whisper model."""
    codes: list[str] = []
    try:
        from faster_whisper import tokenizer as fw_tok

        raw = getattr(fw_tok, "_LANGUAGE_CODES", None)
        if raw:
            codes = [str(c).lower() for c in raw]
    except Exception:
        pass
    if not codes:
        codes = sorted(WHISPER_LANGUAGES.keys())
    return codes


def is_english_only_whisper_model(model_name: str | None) -> bool:
    m = (model_name or "").strip().lower()
    return m.endswith(".en")


def docx_font_for_language(code: str | None) -> str:
    """Windows-installed font fallback appropriate for the source script."""
    c = normalize_code(code, default="en")
    if c in ("zh", "yue"):
        return "Microsoft YaHei"
    if c == "ja":
        return "Yu Gothic UI"
    if c == "ko":
        return "Malgun Gothic"
    if c == "th":
        return "Leelawadee UI"
    if c in ("hi", "mr", "ne", "sa", "bn", "gu", "kn", "ml", "pa", "ta", "te", "si"):
        return "Nirmala UI"
    if c in ("ar", "fa", "ur", "he", "iw", "ps", "sd"):
        return "Segoe UI"
    return "Arial"


def strip_closers(text: str) -> str:
    s = (text or "").rstrip()
    while s and s[-1] in CLOSING_QUOTES_BRACKETS:
        s = s[:-1].rstrip()
    return s


def ends_sentence(text: str, lang: str = "en") -> bool:
    """Language-aware sentence-final punctuation check."""
    code = normalize_code(lang, default="en")
    if code in UNPUNCTUATED_SCRIPTS:
        # Thai/Lao/Khmer rarely use sentence-final marks; only treat explicit
        # sentence marks if present.
        pass
    s = strip_closers(text)
    if not s:
        return False
    if s[-1] not in SENTENCE_FINAL_CHARS:
        return False
    if is_english(code):
        tokens = s.split()
        if not tokens:
            return False
        last = strip_closers(tokens[-1])
        if last.endswith(".") or last.endswith("…"):
            core = last.rstrip(".…").rstrip()
            if not core:
                return False  # bare "..." without a word before it
            if core.lower() in ENGLISH_ABBREVIATIONS:
                return False
            if len(core) == 1 and core.isalpha():
                return False  # initial like "John D."
    return True


def ends_comma(text: str, lang: str = "en") -> bool:
    s = strip_closers(text)
    return bool(s) and s[-1] in COMMA_CHARS


def non_punct_chars(text: str) -> str:
    """Sequence of lowercased non-punctuation, non-whitespace characters (for
    validating punctuation restoration on spaceless scripts)."""
    out: list[str] = []
    for ch in (text or ""):
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("P"):
            continue
        out.append(ch.lower())
    return "".join(out)
