"""Subtitle timing, wrapping, and SRT rendering tests (AGENT_UPDATE_2.md 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dubtool.subtitles import (
    MAX_LINE_CHARS,
    build_srt,
    compute_cue_end,
    timestamp,
    wrap_text,
)


def test_timestamp_format():
    assert timestamp(0) == "00:00:00,000"
    assert timestamp(3.5) == "00:00:03,500"
    assert timestamp(61.234) == "00:01:01,234"
    assert timestamp(3671.5) == "01:01:11,500"


def test_cue_covers_clip_longer_than_end():
    # Clip runs past the original end -> cue extends to clip duration.
    end = compute_cue_end(10.0, 12.0, 3.5, next_start=20.0)
    assert end == pytest.approx(13.5)


def test_cue_never_overlaps_next_cue():
    end = compute_cue_end(10.0, 11.9, clip_duration=4.0, next_start=12.3)
    assert end <= 12.3 - 0.05


def test_minimum_cue_duration_when_next_cue_allows():
    end = compute_cue_end(10.0, 10.2, clip_duration=0.3, next_start=12.5)
    assert end == pytest.approx(11.0)


def test_minimum_cue_duration_not_forced_when_next_cue_too_soon():
    # End keeps max(end, clip) but is capped before the next cue's start;
    # the 1.0 s minimum is NOT forced (the next cue forbids it).
    end = compute_cue_end(10.0, 10.2, clip_duration=0.3, next_start=10.5)
    assert end == pytest.approx(10.3)


def test_last_cue_unbounded_by_next():
    end = compute_cue_end(10.0, 11.0, clip_duration=5.0, next_start=None)
    assert end == pytest.approx(15.0)


def test_wrap_two_lines_at_42_chars():
    text = "gây bão mạng khi xúi người nghe thẳng thừng ly hôn đi giữa buổi phát sóng quay trực tiếp"
    lines = wrap_text(text)
    assert len(lines) <= 2
    assert all(len(line) <= MAX_LINE_CHARS + 10 for line in lines)
    assert " ".join(lines).split() == text.split()


def test_wrap_short_text_single_line():
    assert wrap_text("Bối cảnh") == ["Bối cảnh"]


def test_srt_rendering_preserves_diacritics_and_format(tmp_path: Path):
    entries = [
        {"id": 0, "start": 0.0, "end": 3.5, "vi": "Trời ơi, lại thế nữa à?"},
        {"id": 1, "start": 4.0, "end": 5.5, "vi": "Bối cảnh"},
    ]
    srt = build_srt(entries, tmp_path)
    assert "Trời ơi, lại thế nữa à?" in srt  # UTF-8 preserved, no mojibake
    assert srt.splitlines()[1] == "00:00:00,000 --> 00:00:03,500"
    assert "00:00:04,000 --> 00:00:05,500" in srt
    # Numbering is sequential over rendered cues.
    assert "1\n" in srt and "2\n" in srt


def test_srt_uses_fitted_duration_when_clip_longer(tmp_path: Path):
    import wave
    import contextlib

    # Fake a fitted clip of 2.0 s for id 0 (start 1.0, end 2.0, so cue ends 3.0).
    with contextlib.closing(wave.open(str(tmp_path / "0.wav"), "wb")) as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 8000 * 2)
    entries = [{"id": 0, "start": 1.0, "end": 2.0, "vi": "Chuẩn xác motto"}]
    srt = build_srt(entries, tmp_path)
    assert "00:00:01,000 --> 00:00:03,000" in srt
