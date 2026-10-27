"""Test --ids parsing and clear prerequisite errors (no stack traces)."""

from __future__ import annotations

import pytest

from dubtool.audio_utils import FFmpegMissing
from dubtool.cli import parse_ids


def test_parse_ids_basic():
    assert parse_ids("3,7,12") == [3, 7, 12]


def test_parse_ids_spaces_and_ranges():
    assert parse_ids("1, 3-5, 9") == [1, 3, 4, 5, 9]


def test_parse_ids_empty():
    assert parse_ids("") == []


def test_missing_ffmpeg_message_is_clear(monkeypatch, tmp_path):
    # Strip PATH *and* point the bundle lookup away from the real vendor/
    # dir: the bundled ffmpeg (checked first, AGU4 6.2) is supposed to be
    # found even with PATH empty, so a clean "not found" needs both gone.
    monkeypatch.delenv("PATH", raising=False)
    import dubtool.app.paths as paths_mod

    monkeypatch.setattr(paths_mod, "vendor_dir", lambda: tmp_path / "no-vendor")
    from dubtool import audio_utils

    with pytest.raises(FFmpegMissing, match="ffmpeg not found"):
        audio_utils.ffmpeg_bin()
