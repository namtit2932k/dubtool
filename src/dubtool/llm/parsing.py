"""Response parsing and validation shared by all providers."""

from __future__ import annotations

import json
import re
from typing import Any

from dubtool.llm.base import LLMError


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


def parse_json_array(text: str, expected_ids: list[int]) -> list[dict[str, Any]]:
    """Parse and validate a JSON array of {"id","vi"} objects."""
    if not text or not text.strip():
        raise LLMError("response was empty")

    data = _load_json(strip_code_fences(text), "translation")
    if not isinstance(data, list):
        raise LLMError(f"expected a JSON array, got {type(data).__name__}")

    seen = [item.get("id") for item in data if isinstance(item, dict)]
    if sorted(set(seen)) != sorted(expected_ids):
        raise LLMError(f"expected ids {sorted(expected_ids)} exactly once, got {seen}")
    if len(seen) != len(expected_ids):
        raise LLMError("duplicated or missing ids in response")

    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise LLMError("each item must be an object with id and vi")
        vi = item.get("vi")
        if not isinstance(vi, str) or not vi.strip():
            raise LLMError(f"segment id={item.get('id')} has empty or missing 'vi'")
        out.append({"id": item["id"], "vi": vi.strip()})
    return out


def parse_shortened(text: str) -> str:
    if not text or not text.strip():
        raise LLMError("response was empty")
    data = _load_json(strip_code_fences(text), "shorten")
    vi = data.get("vi") if isinstance(data, dict) else None
    if not isinstance(vi, str) or not vi.strip():
        raise LLMError("missing 'vi' in shortened response")
    return vi.strip()


def parse_batch_shortened(text: str, expected_ids: list[int]) -> list[dict[str, Any]]:
    """Parse the one-time max_chars enforcement batch response."""
    if not text or not text.strip():
        raise LLMError("response was empty")
    data = _load_json(strip_code_fences(text), "shorten batch")
    if not isinstance(data, list):
        raise LLMError(f"expected a JSON array, got {type(data).__name__}")
    seen = [item.get("id") for item in data if isinstance(item, dict)]
    if sorted(set(seen)) != sorted(expected_ids):
        raise LLMError(f"expected ids {sorted(expected_ids)} exactly once, got {seen}")
    out: list[dict[str, Any]] = []
    for item in data:
        vi = item.get("vi") if isinstance(item, dict) else None
        if not isinstance(vi, str) or not vi.strip():
            raise LLMError(f"segment id={item.get('id')} has empty or missing 'vi'")
        out.append({"id": item["id"], "vi": vi.strip()})
    return out
