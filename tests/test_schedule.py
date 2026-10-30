"""Task 3 tests (AGENT_UPDATE_3.md 4): scheduling + SRT/schedule consistency."""

from __future__ import annotations

from pathlib import Path

from dubtool import schedule as sched
from dubtool.subtitles import build_srt


def test_faithful_priority_raises_shift_cap():
    """AGENT_UPDATE_8 6.3: `faithful` raises max_shift to at least 4 s; the
    other presets keep the configured cap, and a larger config value wins."""
    from types import SimpleNamespace

    from dubtool.stages.s6_mix import effective_max_shift

    cfg = SimpleNamespace(translate={"priority": "faithful"})
    assert effective_max_shift(cfg, {}) == 4.0
    assert effective_max_shift(cfg, {"max_shift_s": 2.0}) == 4.0
    assert effective_max_shift(cfg, {"max_shift_s": 5.0}) == 5.0
    for prio in (None, "balanced", "timing"):
        cfg = SimpleNamespace(translate={} if prio is None else {"priority": prio})
        assert effective_max_shift(cfg, {"max_shift_s": 2.0}) == 2.0



def test_no_overlap_when_clips_fit():
    out = sched.schedule_clips([(0, 0.0, 1.0), (1, 1.5, 1.0)])
    assert out[0]["play_start"] == 0.0
    assert out[1]["play_start"] == 1.5
    assert out[1]["shift"] == 0.0
    assert out[1]["overlap"] is False


def test_shift_when_clip_overruns():
    # Segment 0 runs 0.0-1.2; segment 1 starts at 1.0 (too early).
    out = sched.schedule_clips([(0, 0.0, 1.2), (1, 1.0, 1.0)])
    # play_start of seg 1 = 1.2 + 0.08 gap
    assert out[1]["play_start"] == 1.28
    assert abs(out[1]["shift"] - 0.28) < 1e-9
    assert out[1]["overlap"] is False


def test_cap_marks_overlap_true():
    out = sched.schedule_clips([(0, 0.0, 10.0), (1, 0.5, 1.0)], max_shift_s=2.0)
    # uncapped play_start would be > 2 s later than orig
    assert out[1]["play_start"] == 0.5 + 2.0
    assert out[1]["shift"] == 2.0
    assert out[1]["overlap"] is True


def test_cumulative_shifts():
    items = [
        (0, 0.0, 2.0),
        (1, 0.5, 2.0),   # should shift to 2.08
        (2, 1.0, 2.0),   # to 4.16
    ]
    out = sched.schedule_clips(items)
    # seg 1 shifts to 2.08; seg 2's uncapped start would be 4.16 (shift 3.16)
    # so it is capped at orig + 2.0 and marked overlap.
    assert [round(s["play_start"], 2) for s in out] == [0.0, 2.08, 3.0]
    assert out[2]["overlap"] is True
    assert sum(s["shift"] for s in out) > 0


def test_schedule_stats_counts_overlaps():
    out = sched.schedule_clips([(0, 0.0, 10.0), (1, 0.2, 1.0)])
    stats = sched.schedule_stats(out)
    assert stats["shifted"] == 1
    assert stats["overlap_ids"] == [1]
    assert stats["max_shift"] == 2.0


def test_srt_cues_follow_schedule_times():
    entries = [
        {"id": 0, "vi": "xin chào"},
        {"id": 1, "vi": "tạm biệt"},
    ]
    schedule = [
        {"id": 0, "play_start": 0.52, "play_end": 3.0},
        {"id": 1, "play_start": 3.5, "play_end": 4.31},
    ]
    srt = build_srt(entries, fitted_dir=None, schedule=schedule)
    # cue 0: start 0.52, end min(3.0+0.2, 3.5-0.05) = 3.2
    assert "00:00:00,520 --> 00:00:03,200" in srt
    # cue 1: start 3.5, end 4.31 + 0.2 = 4.51 (no next cue)
    assert "00:00:03,500 --> 00:00:04,510" in srt


def test_srt_min_duration_when_next_cue_allows():
    entries = [{"id": 1, "vi": "okay"}]
    schedule = [{"id": 1, "play_start": 10.0, "play_end": 10.2}]
    srt = build_srt(entries, fitted_dir=None, schedule=schedule)
    # No next cue: min 1.0 s applies over the raw 0.4 s (10.2 + 0.2 tail < 1.0).
    assert "00:00:10,000 --> 00:00:11,000" in srt


def test_srt_skips_empty_vi_in_next_start(tmp_path: Path):
    entries = [
        {"id": 0, "vi": "phrase one"},
        {"id": 1, "vi": ""},  # empty text: takes no part
        {"id": 2, "vi": "phrase two"},
    ]
    schedule = [
        {"id": 0, "play_start": 0.0, "play_end": 1.0},
        {"id": 1, "play_start": 2.0, "play_end": 3.0},
        {"id": 2, "play_start": 3.3, "play_end": 4.3},
    ]
    srt = build_srt(entries, fitted_dir=None, schedule=schedule)
    assert "00:00:02,000" not in srt
    # cue 0 ends at play_end + tail (1.2), capped well below cue 2 (3.25).
    assert "00:00:00,000 --> 00:00:01,200" in srt
