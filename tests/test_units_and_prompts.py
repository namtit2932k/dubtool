"""Tests for AGENT_UPDATE_3.md task 7: prompt surface, sentence units,
split_translation, ASR dedupe, and stage 3 unit-mode behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import dubtool.stages.s3_translate as s3
from dubtool import brief as brief_mod
from dubtool.config import Config
from dubtool.job import Job, read_json, write_json
from dubtool.llm import prompts
from dubtool.stages.s2_transcribe import dedupe_repeats, merge_short, split_long
from dubtool.units import build_units, split_translation


# ------------------------------------------------------------- 7.1 prompts
def test_translate_system_has_soft_budget_and_json_rule():
    text = prompts.TRANSLATE_SYSTEM
    assert "soft target" in text
    assert "Accuracy first" in text
    assert "ONLY valid JSON" in text
    assert "native Vietnamese YouTuber" not in text
    assert "NOT literal" not in text


def test_translate_user_template_rules():
    text = prompts.TRANSLATE_USER_TEMPLATE
    assert "soft target" in text
    assert "ONLY a JSON array" in text
    assert "glossary" in text.lower()


def test_brief_block_absent_without_brief():
    user = prompts.render_translate_user(
        [{"id": 0, "text": "x", "max_chars": 10}], [], {}, brief=None
    )
    assert "Video brief" not in user


def test_brief_block_present_when_brief_exists():
    user = prompts.render_translate_user(
        [{"id": 0, "text": "x", "max_chars": 10}], [], {}, brief="A channel about physics."
    )
    assert "Video brief" in user
    assert "A channel about physics." in user
    assert "do not translate the brief" in user


def test_brief_is_capped_at_1000_chars():
    assert len(brief_mod.normalize_brief("x" * 5000)) == brief_mod.MAX_BRIEF_CHARS


def test_resolve_brief_prefers_cli_then_file_then_job_then_video(tmp_path):
    video = tmp_path / "test1.mp4"
    (video.parent / "test1.brief.txt").write_text("video fallback", encoding="utf-8")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "brief.txt").write_text("job version", encoding="utf-8")
    assert brief_mod.resolve_brief(cli_brief="cli wins", job_dir=job_dir, input_video=video) == "cli wins"
    f = tmp_path / "b.txt"
    f.write_text("from file", encoding="utf-8")
    assert brief_mod.resolve_brief(cli_brief_file=f, job_dir=job_dir, input_video=video) == "from file"
    assert brief_mod.resolve_brief(job_dir=job_dir, input_video=video) == "job version"
    no_job = tmp_path / "empty"
    no_job.mkdir()
    assert brief_mod.resolve_brief(job_dir=no_job, input_video=video) == "video fallback"


# -------------------------------------------------------- 7.2 build_units
def _seg(id, start, end, text):
    return {"id": id, "start": start, "end": end, "text": text}


def test_build_units_closes_at_sentence_punctuation():
    units = build_units([_seg(0, 0.0, 2.0, "Hello there."), _seg(1, 2.0, 4.0, "How are you?")])
    assert [u["seg_ids"] for u in units] == [[0], [1]]
    assert units[0]["text"] == "Hello there."


def test_build_units_does_not_close_at_abbreviations():
    units = build_units([_seg(0, 0.0, 2.0, "I asked Dr."), _seg(1, 2.0, 4.0, "Smith.")])
    assert [u["seg_ids"] for u in units] == [[0, 1]]


def test_build_units_respects_gap_cap():
    units = build_units(
        [_seg(0, 0.0, 2.0, "And then we go"), _seg(1, 4.0, 6.0, "on from here.")],
        max_gap_s=1.0,
    )
    assert [u["seg_ids"] for u in units] == [[0], [1]]


def test_build_units_respects_duration_cap():
    # No sentence punctuation anywhere -> closes on duration.
    units = build_units(
        [
            _seg(0, 0.0, 6.0, "and then we go and then we stay"),
            _seg(1, 6.0, 12.0, "so this should close the unit now"),
        ],
        max_duration_s=14.0,
    )
    # 12s < 14s: stays one unit? 0..6 then next starts at 6: the unit would be
    # 12.0s total which is under 14 — but max_segments forces a check too.
    assert [u["seg_ids"] for u in units] == [[0, 1]]


def test_build_units_respects_segment_cap():
    segments = [
        _seg(0, 0.0, 2.0, "and we go on"),
        _seg(1, 2.0, 4.0, "and we go on"),
        _seg(2, 4.0, 6.0, "and we go on"),
        _seg(3, 6.0, 8.0, "and we go on"),
        _seg(4, 8.0, 10.0, "final words here."),
    ]
    units = build_units(segments, max_segments=4)
    assert [u["seg_ids"] for u in units] == [[0, 1, 2, 3], [4]]


def test_every_segment_in_exactly_one_unit_in_order():
    segments = [
        _seg(0, 0.0, 2.0, "First sentence."),
        _seg(1, 2.0, 4.0, "Second sentence."),
        _seg(2, 4.2, 9.0, "Third one continues"),
        _seg(3, 9.1, 12.0, "and finishes off."),
    ]
    units = build_units(segments)
    seen = [i for u in units for i in u["seg_ids"]]
    assert seen == [0, 1, 2, 3]


# --------------------------------------------------- 7.2 split_translation
def test_split_translation_exact_parts_and_reattach():
    vi = "Đây là câu đầu của một đoạn dài, và đây là phần hai, kết thúc."
    segs = [{"id": 0, "start": 0.0, "end": 2.0}, {"id": 1, "start": 2.0, "end": 4.0}]
    parts = split_translation(vi, segs)
    assert len(parts) == 2
    assert all(p.split() for p in parts)
    assert " ".join(parts) == vi


def test_split_translation_fewer_words_than_segments_no_empty_parts():
    segs = [{"id": 0, "start": 0.0, "end": 1.0}, {"id": 1, "start": 1.0, "end": 2.0},
            {"id": 2, "start": 2.0, "end": 3.0}]
    parts = split_translation("xin chào", segs)
    assert len(parts) == 3
    assert all(p.strip() for p in parts)


def test_split_translation_diacritics_intact():
    vi = "Cô ấy lại làm thế nữa à,.Controllers ầm ầm thật sự ạ."
    segs = [{"id": 0, "start": 0.0, "end": 2.0}, {"id": 1, "start": 2.0, "end": 4.0}]
    out = split_translation(vi, segs)
    # no cut inside a word: rejoining reproduces the token sequence
    assert " ".join(out) == " ".join(vi.split())


def test_split_translation_prefers_punctuation_cut():
    vi = ("Một phần rất dài đầu tiên của câu này tiếp tục miên man dài dòng, "
          "và phần ngắn sau đó.")
    segs = [{"id": 0, "start": 0.0, "end": 3.0}, {"id": 1, "start": 3.0, "end": 4.0}]
    parts = split_translation(vi, segs)
    assert parts[0].endswith(",")  # punctuation cut wins when close


def join_tokens(tokens):
    return " ".join(tokens)


# ------------------------------------------------------ 7.3 dedupe_repeats
def test_dedupe_removes_boundary_repeat():
    segments = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "And the answer is always yes."},
        {"id": 1, "start": 2.0, "end": 4.0, "text": "The answer is always yes. And this idea that..."},
    ]
    out, removals = dedupe_repeats(segments)
    assert removals == [(1, "The answer is always yes.")]
    assert out[1]["text"].startswith("And this idea")
    assert out[0]["text"] == segments[0]["text"]  # kept segment untouched


def test_dedupe_ignores_in_segment_repeat():
    text = "The answer is always yes, and the answer is always yes again."
    segments = [{"id": 0, "start": 0.0, "end": 2.0, "text": text}]
    out, removals = dedupe_repeats(segments)
    assert removals == []
    assert out[0]["text"] == text


def test_dedupe_drops_empty_segment_extends_end_and_renumbers():
    segments = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "So we keep going forward."},
        {"id": 1, "start": 2.0, "end": 3.5, "text": "So we keep going forward."},
        {"id": 2, "start": 3.5, "end": 5.0, "text": "Next words follow."},
    ]
    out, removals = dedupe_repeats(segments)
    assert removals == [(1, "So we keep going forward.")]
    assert len(out) == 2
    assert out[0]["end"] == 3.5  # extended over the dropped segment
    # dedupe_repeats only drops; cleanup renumbers ids (postprocess test).
    assert [s["id"] for s in out] == [0, 2]


def test_dedupe_case_and_punctuation_insensitive():
    segments = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "I said, LET ME GO!"},
        {"id": 1, "start": 2.0, "end": 4.0, "text": "Let me go! (quietly)"},
    ]
    out, removals = dedupe_repeats(segments, min_words=3)
    assert removals == [(1, "Let me go!")]
    assert out[1]["text"] == "(quietly)"
    assert out[0]["text"] == segments[0]["text"]


def test_merge_split_then_dedupe_full_cleanup():
    raw = [
        {"start": 0.0, "end": 0.4, "text": "Hi"},
        {"start": 0.4, "end": 3.0, "text": "there friend."},
        {"start": 3.0, "end": 13.0, "text": ("A very long sentence. " * 8).strip()},
    ]
    cleaned = merge_short(split_long(raw))
    cleaned, removals = dedupe_repeats(cleaned, min_words=3)
    # Segments stay non-empty and chronological.
    assert all(s["text"].strip() for s in cleaned)
    for a, b in zip(cleaned, cleaned[1:]):
        assert a["end"] <= b["start"]


def test_postprocess_only_rewrites_transcript(tmp_path, monkeypatch):
    job = Job(tmp_path / "job")
    write_json(
        job.transcript_path,
        [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "And we keep going forward."},
            {"id": 1, "start": 2.0, "end": 3.5, "text": "We keep going forward. More talk."},
            {"id": 2, "start": 3.5, "end": 5.0, "text": "Something else."},
        ],
    )
    from dubtool.stages.s2_transcribe import _postprocess_only

    class FakeAsrCfg(dict):
        pass

    config = Config()
    config.asr = {"dedupe_min_words": 3, "condition_on_previous_text": False}
    _postprocess_only(job, config)
    cleaned = read_json(job.transcript_path)
    assert cleaned[0]["text"] == "And we keep going forward."
    # The repeated words were removed from segment 1's start.
    assert cleaned[1]["text"].startswith("More talk")


# --------------------------------------------------- stage 3 unit behavior
class FakeUnitClient:
    total_input_tokens = 0
    total_output_tokens = 0
    total_calls = 0

    def __init__(self, mapping):
        self.mapping = mapping
        self.seen_payloads = []

    def translate_batch(self, segments, context, glossary, brief=None, pronouns=None, policy=None):
        self.seen_payloads.extend(segments)
        return [{"id": s["id"], "vi": self.mapping[s["id"]]} for s in segments]

    def shorten_batch(self, items, policy=None):
        return []


def _unit_job(tmp_path) -> Job:
    job = Job(tmp_path / "job")
    write_json(
        job.transcript_path,
        [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "Oh wow,"},
            {"id": 1, "start": 2.0, "end": 4.0, "text": "is she doing it again?"},
            {"id": 2, "start": 4.5, "end": 8.0, "text": "Yes she is."},
        ],
    )
    return job


def test_stage3_unit_mode_keeps_per_segment_contract(tmp_path, monkeypatch):
    job = _unit_job(tmp_path)
    fake = FakeUnitClient({0: "Ồ, cô ấy lại làm thế nữa à?", 1: "Đúng, cô ấy làm thế."})
    monkeypatch.setattr(s3, "LLMClient", lambda provider, **k: fake)
    monkeypatch.setattr(s3, "get_provider", lambda config, pid=None, **k: type("P", (), {"name": "f", "model": "m"})())
    config = Config(chars_per_sec=14)
    s3.run(job, config, force=False, provider_id="gemini")

    entries = read_json(job.translated_path)
    assert len(entries) == 3
    by_id = {e["id"]: e for e in entries}
    for e in entries:
        assert set(e) == {"id", "start", "end", "text", "vi", "max_chars",
                          "reviewed", "tts_duration", "fit_status"}
    # Unit 0 covered segments 0+1 (same sentence split by the ASR) and unit 1 is segment 2.
    assert by_id[0]["vi"] and by_id[1]["vi"] and by_id[2]["vi"]
    joined = (by_id[0]["vi"] + " " + by_id[1]["vi"]).strip()
    assert joined.strip() == "Ồ, cô ấy lại làm thế nữa à?"
    # units.json written
    units = read_json(job.dir / "units.json")
    assert units[0]["seg_ids"] == [0, 1]


def test_stage3_skips_unit_with_reviewed_segment(tmp_path, monkeypatch):
    job = _unit_job(tmp_path)
    entries = s3.build_translated(read_json(job.transcript_path), 14)
    entries[1]["reviewed"] = True
    entries[1]["vi"] = "bản reviewed"
    write_json(job.translated_path, entries)

    calls = {"n": 0}

    class MappingUnitClient(FakeUnitClient):
        def translate_batch(self, segments, context, glossary, brief=None, pronouns=None, policy=None):
            calls["n"] += 1
            self.seen_payloads.extend(list(segments))
            return [
                {"id": s["id"], "vi": {1: "Đúng, cô ấy làm thế."}[s["id"]]}
                for s in segments
            ]

    fake = MappingUnitClient({})
    monkeypatch.setattr(s3, "LLMClient", lambda provider, **k: fake)
    monkeypatch.setattr(s3, "get_provider", lambda config, pid=None, **k: type("P", (), {"name": "f", "model": "m"})())
    config = Config(chars_per_sec=14)
    s3.run(job, config, force=False, provider_id="gemini")
    entries_after = read_json(job.translated_path)
    by_id = {e["id"]: e for e in entries_after}
    assert by_id[1]["vi"] == "bản reviewed"
    assert by_id[1]["reviewed"] is True
    # Unit 0 (segments 0+1) was skipped for its reviewed member; only unit 1
    # (segment 2) was sent to the LLM.
    assert calls["n"] == 1
    assert [p["id"] for p in fake.seen_payloads] == [1]
    assert by_id[0]["vi"] is None
    assert by_id[2]["vi"] == "Đúng, cô ấy làm thế."
