"""Test --ids parsing and clear prerequisite errors (no stack traces)."""

from __future__ import annotations

import pytest

from dubtool.cli import parse_ids
from dubtool.audio_utils import FFmpegMissing


def test_parse_ids_basic():
    assert parse_ids("3,7,12") == [3, 7, 12]


def test_parse_ids_spaces_and_ranges():
    assert parse_ids("1, 3-5, 9") == [1, 3, 4, 5, 9]


def test_parse_ids_empty():
    assert parse_ids("") == []


def test_missing_ffmpeg_message_is_clear(monkeypatch):
    monkeypatch.delenv("PATH", raising=False)
    from dubtool import audio_utils

    with pytest.raises(FFmpegMissing, match="install FFmpeg"):
        audio_utils.ffmpeg_bin()
