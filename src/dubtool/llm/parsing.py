"""Response parsing and validation shared by all providers."""

from __future__ import annotations

import json
import re
from typing import Any

from dubtool.llm.base import LLMError

# Chinese Hanzi, Japanese Kana, Korean Hangul — never allowed to leak into
# Vietnamese dubbing text (TTS cannot pronounce them), unless explicitly kept
# via glossary ("keep original").
_UNTRANSLATED_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]+")


def find_untranslated_cjk(
    items: list[dict[str, Any]],
    glossary: dict[str, str] | None = None,
) -> list[tuple[Any, list[str]]]:
    """Return ``[(id, [token, ...]), ...]`` for items whose Vietnamese text
    still contains untranslated CJK/Kana/Hangul characters (excluding terms
    explicitly marked ``keep original`` in ``glossary``)."""
    allowed: list[str] = []
    if isinstance(glossary, dict):
        for term, val in glossary.items():
            if isinstance(term, str) and term.strip():
                v = str(val or "").strip().lower()
                if v == "keep original":
                    allowed.append(term.strip())
                elif _UNTRANSLATED_CJK_RE.search(str(val or "")):
                    allowed.append(str(val).strip())

    bad: list[tuple[Any, list[str]]] = []
    for item in items:
        vi = str(item.get("vi") or "")
        cleaned = vi
        for keep in allowed:
            cleaned = cleaned.replace(keep, "")
        tokens = list(dict.fromkeys(_UNTRANSLATED_CJK_RE.findall(cleaned)))
        if tokens:
            bad.append((item.get("id"), tokens))
    return bad


def check_no_untranslated_cjk(
    items: list[dict[str, Any]],
    glossary: dict[str, str] | None = None,
) -> None:
    """Raise ``LLMError`` if any translated Vietnamese item still contains
    untranslated CJK/Kana/Hangul characters (excluding terms explicitly marked
    ``keep original`` in ``glossary``)."""
    bad = find_untranslated_cjk(items, glossary=glossary)
    if bad:
        ids_str = ", ".join(str(i) for i, _ in bad[:6])
        samples = ", ".join(f"id={i}({toks[0]})" for i, toks in bad[:4])
        raise LLMError(
            f"untranslated source-script characters left in Vietnamese for id(s) [{ids_str}] "
            f"({samples}); translate or transliterate every character into pure Vietnamese"
        )


def strip_code_fences(text: str) -> str:
    """Remove ```...``` wrapping if present."""
    fence = re.search(r"```[a-zA-Z]*\s*\n(.*?)```", text, flags=re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text.strip()


def _load_json(cleaned: str, original_hint: str) -> Any:
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # Tolerate prose around the JSON payload.
        match = re.search(r"(\[.*\]|\{.*\})", cleaned, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise LLMError(f"invalid JSON ({original_hint}): {exc}") from exc


def parse_json_items(text: str, expected_ids: list[int]) -> list[dict[str, Any]]:
    """Parse a translation batch that may arrive wrapped: structured
    output returns the object root ({"items": [...]}) while the plain
    JSON prompt returns the array directly. Both validate as the array
    parse does."""
    if not text or not text.strip():
        raise LLMError("response was empty")
    cleaned = strip_code_fences(text)
    data = _load_json(cleaned, "translation")
    if not isinstance(data, list):
        from dubtool.llm.schemas import unwrap_items

        data = unwrap_items(data)
    if not isinstance(data, list):
        raise LLMError(
            f"expected a JSON array (possibly under 'items'), got {type(data).__name__}"
        )
    import json as _json

    return parse_json_array(_json.dumps(data, ensure_ascii=False), expected_ids)


def _extract_vi_field(item: dict[str, Any]) -> Any:
    """Read the translated Vietnamese string from a reply object.

    Prefers ``vi``; when the model omits ``vi`` and mirrors the input's
    ``text`` (or ``translation``) key instead, accept that string so a valid
    translation batch is not discarded solely for key-name drift. An explicit
    empty ``vi`` value still fails validation.
    """
    if "vi" in item:
        return item.get("vi")
    for alias in ("text", "translation"):
        val = item.get(alias)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _validate_ids(data: list[Any], expected_ids: list[int]) -> None:
    seen = [item.get("id") for item in data if isinstance(item, dict)]
    if len(seen) != len(expected_ids) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in seen
    ):
        raise LLMError(f"expected ids {sorted(expected_ids)} exactly once (integers), got {seen}")
    if sorted(set(seen)) != sorted(expected_ids):
        raise LLMError(f"expected ids {sorted(expected_ids)} exactly once, got {seen}")


def parse_json_array(text: str, expected_ids: list[int]) -> list[dict[str, Any]]:
    """Parse and validate a JSON array of {"id","vi"} objects."""
    if not text or not text.strip():
        raise LLMError("response was empty")

    data = _load_json(strip_code_fences(text), "translation")
    if not isinstance(data, list):
        raise LLMError(f"expected a JSON array, got {type(data).__name__}")

    _validate_ids(data, expected_ids)

    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise LLMError("each item must be an object with id and vi")
        vi = _extract_vi_field(item)
        if not isinstance(vi, str) or not vi.strip():
            raise LLMError(f"segment id={item.get('id')} has empty or missing 'vi'")
        out.append({"id": item["id"], "vi": vi.strip()})
    return out


def parse_shortened(text: str) -> str:
    if not text or not text.strip():
        raise LLMError("response was empty")
    data = _load_json(strip_code_fences(text), "shorten")
    vi = _extract_vi_field(data) if isinstance(data, dict) else None
    if not isinstance(vi, str) or not vi.strip():
        raise LLMError("missing 'vi' in shortened response")
    return vi.strip()


def parse_batch_shortened(text: str, expected_ids: list[int]) -> list[dict[str, Any]]:
    """Batch shortening (one-time max_chars enforcement), tolerating the
    structured-object wrapper."""
    if not text or not text.strip():
        raise LLMError("response was empty")
    data = _load_json(strip_code_fences(text), "shorten batch")
    if not isinstance(data, list):
        from dubtool.llm.schemas import unwrap_items

        data = unwrap_items(data)
    if not isinstance(data, list):
        raise LLMError(f"expected a JSON array, got {type(data).__name__}")
    _validate_ids(data, expected_ids)
    out: list[dict[str, Any]] = []
    for item in data:
        vi = _extract_vi_field(item) if isinstance(item, dict) else None
        if not isinstance(vi, str) or not vi.strip():
            raise LLMError(f"segment id={item.get('id')} has empty or missing 'vi'")
        out.append({"id": item["id"], "vi": vi.strip()})
    return out
