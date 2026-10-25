"""Unit tests for LLM response parsing (AGENT.md section 9).

No network, no API key: only the pure parsing/validation functions.
"""

from __future__ import annotations

import json

import pytest

from dubtool.llm import LLMError, parse_json_array, parse_shortened, strip_code_fences


def test_strip_code_fences_json_block():
    text = "```json\n[{\"id\": 0, \"vi\": \"Xin chào\"}]\n```"
    assert strip_code_fences(text) == '[{"id": 0, "vi": "Xin chào"}]'


def test_strip_code_fences_bare_fence():
    text = "```\nplain\n```"
    assert strip_code_fences(text) == "plain"


def test_strip_code_fences_no_fence():
    assert strip_code_fences('  [1,2]  ') == "[1,2]"


def test_parse_json_array_valid():
    out = parse_json_array('[{"id": 3, "vi": "Thế giới"}]', [3])
    assert out == [{"id": 3, "vi": "Thế giới"}]


def test_parse_json_array_fenced():
    out = parse_json_array('```json\n[{"id": 0, "vi": "Xin chào"}]\n```', [0])
    assert out == [{"id": 0, "vi": "Xin chào"}]


def test_parse_json_array_prose_around_json():
    text = 'Here you go:\n[{"id": 1, "vi": "ok"}]\nHope that helps!'
    out = parse_json_array(text, [1])
    assert out == [{"id": 1, "vi": "ok"}]


def test_parse_json_array_invalid_json_raises():
    with pytest.raises(LLMError, match="invalid JSON"):
        parse_json_array("not json at all", [0])


def test_parse_json_array_empty_raises():
    with pytest.raises(LLMError, match="empty"):
        parse_json_array("", [0])


def test_parse_json_array_wrong_ids_raises():
    with pytest.raises(LLMError, match="expected ids"):
        parse_json_array('[{"id": 5, "vi": "x"}]', [0, 1])


def test_parse_json_array_missing_id_raises():
    with pytest.raises(LLMError):
        parse_json_array('[{"id": 0, "vi": "x"}, {"id": 2, "vi": "y"}]', [0, 1])


def test_parse_json_array_empty_string_raises():
    with pytest.raises(LLMError):
        parse_json_array('[{"id": 0, "vi": "   "}]', [0])


def test_parse_json_array_not_a_list_raises():
    with pytest.raises(LLMError, match="JSON array"):
        parse_json_array('{"id": 0, "vi": "x"}', [0])


def test_parse_shortened_plain_and_fenced():
    assert parse_shortened('{"vi": "ngắn gọn"}') == "ngắn gọn"
    assert parse_shortened('```json\n{"vi": "ngắn gọn"}\n```') == "ngắn gọn"


def test_parse_shortened_invalid_raises():
    with pytest.raises(LLMError):
        parse_shortened("sure, here you are")


def test_parse_shortened_empty_vi_raises():
    with pytest.raises(LLMError):
        parse_shortened('{"vi": ""}')
