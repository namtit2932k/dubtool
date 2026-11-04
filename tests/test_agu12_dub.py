"""Update 12 tasks 2 + 3: sentence-level dubbing.

- dub_units.json: the record contract, roundtrip, per-unit file naming;
- granularity: sentence default, segment resume of old jobs;
- the boundary-repeat collapse against word timestamps (evidence pairs:
  `the universe | universe must work` collapses; a legitimate double and
  a stop-list double stay);
- stage 7 unit cues: a long sentence becomes several consecutive cues
  with proportional times, each within 2 lines of 42 characters.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dubtool.dub_units import (
    build_dub_units,
    file_base,
    granularity_for_job,
    load_dub_units,
    set_translation,
    units_path,
    write_dub_units,
)


# ------------------------------------------------------------- unit data


def _units() -> list[dict]:
    return [
        {
            "unit_id": 0, "seg_ids": [0, 1], "start": 1.0, "end": 15.0,
            "text": "Dark energy is a strange phenomenon that scientists "
                    "believe permeates the universe.",
            "max_chars": 90,
        },
        {
            "unit_id": 1, "seg_ids": [2], "start": 15.0, "end": 20.0,
            "text": "Chết nhiệt là điều gì?",
            "max_chars": 40,
        },
    ]


def test_build_roundtrip_and_contract(tmp_path):
    records = build_dub_units(_units(), {0: {"vi": "Năng lượng tối là hiện tượng lạ.",
                                            "model": "gemini/x"},
                                        1: {"vi": "Khác biệt.", "model": None}})
    rec = records[0]
    assert set(rec) >= {"unit_id", "seg_ids", "start", "end", "text", "vi",
                        "max_chars", "model", "voice", "tts_duration",
                        "fit_status", "reviewed"}
    assert rec["vi"] == "Năng lượng tối là hiện tượng lạ."
    assert rec["voice"] is None and rec["fit_status"] == "pending"

    job = SimpleNamespace(dir=tmp_path, dub_units_path=tmp_path / "dub_units.json")
    write_dub_units(job, records)
    loaded = load_dub_units(job)
    assert loaded == records  # files written with ensure_ascii=False, UTF-8
    assert set_translation(loaded, 1, "  Câu số một.  ", "gemini/y")
    assert loaded[1]["vi"] == "Câu số một."
    assert loaded[1]["model"] == "gemini/y"
    assert set_translation(loaded, 99, "x", None) is False


def test_file_base_per_mode(tmp_path):
    assert file_base({"id": 7}, "sentence") == "u7"
    assert file_base({"id": 7}, "segment") == "7"


def test_granularity_fresh_job_defaults_sentence(tmp_path):
    job = SimpleNamespace(dir=tmp_path, translated_path=tmp_path / "t.json",
                          dub_units_path=tmp_path / "dub_units.json")
    assert granularity_for_job(job, SimpleNamespace(dub={})) == "sentence"


def test_granularity_old_job_resumes_segment(tmp_path, monkeypatch):
    # translated.json exists but no dub_units.json → the configured value,
    # defaulting to segment so an old job resumes unchanged.
    (tmp_path / "translated.json").write_text("[]", encoding="utf-8")
    job = SimpleNamespace(dir=tmp_path, translated_path=tmp_path / "translated.json",
                          dub_units_path=tmp_path / "dub_units.json")
    assert granularity_for_job(job, SimpleNamespace(dub={})) == "segment"


def test_granularity_community_unit_flag_wins(tmp_path):
    (tmp_path / "dub_units.json").write_text("[]", encoding="utf-8")
    job = SimpleNamespace(dir=tmp_path, translated_path=tmp_path / "t.json",
                          dub_units_path=tmp_path / "dub_units.json")
    # The unit-shot file wins even when the job was configured segment.
    assert granularity_for_job(job, SimpleNamespace(
        dub={"granularity": "segment"})) == "sentence"


# ------------------------------------------------------------- 3.6 collapse


def _segs(words_a: str, words_b: str) -> list[dict]:
    return [
        {"id": 0, "start": 0.0, "end": 3.0, "text": words_a},
        {"id": 1, "start": 3.0, "end": 6.0, "text": words_b},
    ]


def _word_list_for(segs: list[dict], times: dict[str, tuple[float, float]] | dict[str, list[tuple[float, float]]]) -> list[dict]:
    """One timed word entry per occurrence (the transcript's word entries
    use the `w` key). `times` maps a word to one span, or to a LIST of
    spans consumed in occurrence order."""
    out: list[dict] = []
    for seg in segs:
        for token in seg["text"].split():
            spans = times.get(token)
            if isinstance(spans, list):
                start, end = spans.pop(0) if spans else (seg["start"], seg["end"])
            elif spans:
                start, end = spans
            else:
                start, end = seg["start"], seg["end"]
            out.append({"w": token, "start": start, "end": end})
    return out


def test_boundary_repeat_collapses_with_word_times():
    from dubtool.stages.s2_transcribe import collapse_boundary_word

    segs = _segs("Until 1998, we thought that the universe",
                 "universe must work like this")
    # First occurrence ends the previous segment; the repeat starts the next.
    times = {"universe": [(2.75, 2.98), (2.99, 3.1)],
             "must": (3.2, 3.4), "work": (3.5, 3.8)}
    cleaned, removals = collapse_boundary_word(segs, _word_list_for(segs, times))
    # The removal is recorded with the id of the segment whose repeated
    # word was dropped (the second one) — occurrence order.
    assert removals == [(1, "universe")]
    assert cleaned[0]["text"].endswith("the universe")
    assert cleaned[1]["text"].startswith("must work")


def test_legit_double_and_stop_words_stay():
    from dubtool.stages.s2_transcribe import collapse_boundary_word

    segs = _segs("He told them very", "very clearly, no doubt about no")
    segs.append({"id": 2, "start": 6.0, "end": 9.0, "text": "no other choice"})
    times = {"very": (2.8, 3.05), "clearly": (3.2, 3.6)}
    cleaned, removals = collapse_boundary_word(segs, _word_list_for(segs, times))
    assert removals == []  # "very" is legitimately doubled; "no" is stop-listed
    assert cleaned[0]["text"] == segs[0]["text"]


def test_boundary_repeat_keeps_far_words():
    from dubtool.stages.s2_transcribe import collapse_boundary_word

    segs = _segs("the universe is large", "strings inside atoms")
    times = {"large": (2.0, 2.4), "strings": (3.0, 3.2)}
    cleaned, removals = collapse_boundary_word(segs, _word_list_for(segs, times))
    assert removals == []  # not near the 0.1 s window


# ------------------------------------------------------------- 3.5 cues


def test_split_unit_cues_short_text_is_one_cue():
    from dubtool.subtitles import split_unit_cues

    cues = split_unit_cues("Ngắn gọn.", 10.0, 12.0)
    assert cues == [(10.0, 12.0, "Ngắn gọn.")]


def test_split_unit_cues_long_sentence_proportional():
    from dubtool.subtitles import split_unit_cues

    text = ("Vũ trụ đang giãn nở, và tốc độ giãn nở của nó còn đang tăng "
            "nhanh đến mức các nhà khoa học phải gọi nó là năng lượng tối. "
            "Chưa ai biết nó là gì.")
    start, end = 5.0, 15.0
    cues = split_unit_cues(text, start, end, 42)
    assert len(cues) >= 2
    # The whole text survives, in order, in 2-line pieces.
    joined = " ".join(c[2] for c in cues)
    assert joined.replace(" ", "") == text.replace(" ", "")
    assert max(len(p) for p in (c[2] for c in cues)) <= 2 * 42
    # Windows tile the unit span without overlaps.
    assert cues[0][0] == start and cues[-1][1] == end
    for (s0, e0, _), (s1, _e1, _t) in zip(cues, cues[1:]):
        assert abs(s1 - e0) < 1e-6
    # Times proportional to character counts.
    chars = [len(c[2]) for c in cues]
    total = sum(chars)
    span = end - start
    for (s, e, _), n in zip(cues, chars):
        assert abs((e - s) - span * n / total) < 1e-6


def test_srt_building_uses_unit_split_for_schedule_rows():
    from dubtool.subtitles import build_srt

    entries = [
        {"id": 0, "start": 5.0, "vi": "x" * 100},   # a long unit
        {"id": 1, "start": 60.0, "vi": "ngắn"},
    ]
    schedule = [
        {"id": 0, "play_start": 5.0, "play_end": 15.0},
        {"id": 1, "play_start": 60.0, "play_end": 62.0},
    ]
    text = build_srt(entries, None, schedule=schedule, split_long_units=True)
    assert text.count("-->") >= 3  # the long unit became several cues
    assert "ngắn" in text
    # The legacy path splits a too-long segment as well (its own rule),
    # so both builds carry multiple cues here — the unit path's own
    # guarantee is the piece/cap/time behavior asserted above.
    text_seg = build_srt(entries, None, schedule=schedule)
    assert text_seg.count("-->") >= 2
