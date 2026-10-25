"""Unit tests for the data contracts (AGENT.md section 4) and atomic writes."""

from __future__ import annotations

import json

import pytest

from dubtool.job import Job, write_json, read_json
from dubtool.stages.s2_transcribe import _finalize, cleanup_segments
from dubtool.stages.s3_translate import build_translated, merge_translations


def test_finalize_transcript_contract_shape(tmp_path):
    raw = [{"start": 0.52, "end": 4.10, "text": "Welcome back to the channel."}]
    out = _finalize(raw)
    assert out[0] == {
        "id": 0,
        "start": 0.52,
        "end": 4.10,
        "text": "Welcome back to the channel.",
        "speaker": None,
    }


def test_build_translated_matches_contract(tmp_path):
    seg = {"id": 0, "start": 0.52, "end": 4.10, "text": "Welcome back to the channel."}
    out = build_translated([seg], chars_per_sec=14)
    e = out[0]
    # (4.10 - 0.52) * 14 = 50.12 -> 50
    assert e == {
        "id": 0,
        "start": 0.52,
        "end": 4.10,
        "text": "Welcome back to the channel.",
        "vi": None,
        "max_chars": 50,
        "reviewed": False,
        "tts_duration": None,
        "fit_status": "pending",
    }


def test_merge_translations_sets_vi_only_for_requested_ids():
    entries = build_translated(
        [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "Hello"},
            {"id": 1, "start": 2.0, "end": 4.0, "text": "World"},
        ],
        14,
    )
    entries[0]["vi"] = "Xin chàxxxxo"  # simulate prior run value
    merge_translations(entries, [{"id": 0, "vi": "Xin chào"}, {"id": 1, "vi": "Thế giới"}])
    assert entries[0]["vi"] == "Xin chào"
    assert entries[1]["vi"] == "Thế giới"


def test_merge_translations_never_overwrites_reviewed():
    entries = build_translated(
        [{"id": 0, "start": 0.0, "end": 2.0, "text": "Hello"}], 14
    )
    entries[0]["reviewed"] = True
    entries[0]["vi"] = "Bản do người dùng sửa"
    merge_translations(entries, [{"id": 0, "vi": "Bản mới từ LLM"}])
    assert entries[0]["vi"] == "Bản do người dùng sửa"


def test_write_json_preserves_vietnamese_utf8_indent(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, {"vi": "Chào mừng bạn quay lại kênh."})
    text = path.read_text(encoding="utf-8")
    assert "Chào mừng bạn quay lại kênh." in text  # ensure_ascii=False
    assert json.loads(text)["vi"] == "Chào mừng bạn quay lại kênh."
    assert "\n  \"vi\"" in text or '\n  "vi"' in text  # indent=2


def test_write_json_is_atomic_no_tmp_leftovers(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, {"a": 1})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_job_state_roundtrip_and_stage_flags(tmp_path):
    job = Job(tmp_path / "job1")
    assert job.stage_status("s1") == "pending"
    job.start_stage("s3")
    assert job.stage_status("s3") == "running"
    job.finish_stage("s3")
    assert job.is_done("s3")
    job.finish_stage("s4", status="failed", error="boom")
    assert job.stage_status("s4") == "failed"
    assert job.state["last_error"] == "s4: boom"

    reloaded = Job(tmp_path / "job1")
    assert reloaded.is_done("s3")
    assert reloaded.stage_status("s4") == "failed"


def test_cleanup_segments_reindexes_ids_sequentially():
    raw = [
        {"start": 0.0, "end": 0.4, "text": "Hi"},           # too short -> merges
        {"start": 0.4, "end": 3.0, "text": "there friend."},
        {"start": 3.0, "end": 13.0, "text": ("A very long sentence. " * 8).strip()},  # >12s -> split
    ]
    out, removals = cleanup_segments(raw)
    assert [s["id"] for s in out] == list(range(len(out)))
    assert out[0]["end"] - out[0]["start"] == pytest.approx(3.0)  # short segment merged
    assert out[1]["start"] == out[0]["end"]  # split keeps continuity
    # The merged line contains the short text.
    assert out[0]["text"].startswith("Hi")
