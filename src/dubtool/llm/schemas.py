"""Structured-output schemas for the stages.

A provider's structured output guarantees the REPLY SHAPE (an object),
never correctness — the shared parser still validates ids and word
sequences. Stages that need a JSON array wrap it: the strict-schema root
must be an object, so the array travels inside `items` and the shared
parser unwraps it. Providers that cannot accept a schema never receive
one (the piped JSON prompt stays as before).
"""

from __future__ import annotations


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


def _string_array(max_items: int | None = None) -> dict:
    return {"type": "array", "items": {"type": "string"}}


# ----------------------------------------------------------- translation
TRANSLATION_NAME = "translation_batch"


def translate_item_schema() -> dict:
    return _obj({"id": {"type": "integer"}, "vi": {"type": "string"}})


def translate_schema() -> dict:
    """Strict root object wrapping the array ({\"items\": [...]}) for
    providers that only accept an object root."""
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": translate_item_schema()}},
        "required": ["items"],
        "additionalProperties": False,
    }


def translate_schema_array() -> dict:
    """The form for providers that accept an array root directly."""
    return {"type": "array", "items": translate_item_schema()}


# ------------------------------------------------------------- shortening
SHORTEN_NAME = "shortened"


def shorten_schema() -> dict:
    return _obj({"vi": {"type": "string"}})


SHORTEN_BATCH_NAME = "shortened_batch"


def shorten_batch_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": _obj({"id": {"type": "integer"}, "vi": {"type": "string"}}),
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def unwrap_items(obj) -> list | None:
    """Pull the stage's list back out of the wrapped reply: a dict with
    `items` returns its list; anything list-like passes through."""
    if isinstance(obj, dict):
        items = obj.get("items")
        if isinstance(items, list):
            return items
        return None
    if isinstance(obj, list):
        return obj
    return None
