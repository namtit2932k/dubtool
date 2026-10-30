"""Stage 3 enforcement pass test: over-budget segments get one shorten batch."""

from __future__ import annotations

import json

import pytest

from types import SimpleNamespace

import dubtool.stages.s3_translate as s3
from dubtool.config import Config
from dubtool.job import Job, write_json


class FakeClient:
    # s3's summary line reads provider.name / provider.model.
    provider = SimpleNamespace(name="fake", model="fake-model")

    def __init__(self, translate_out, shorten_batch_out):
        self.translate_calls = 0
        self.batch_calls = 0
        self.translate_out = translate_out
        self.shorten_batch_out = shorten_batch_out
        self.seen_items = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0

    def translate_batch(self, segments, context, glossary, brief=None, pronouns=None, policy=None):
        self.translate_calls += 1
        return self.translate_out

    def shorten_batch(self, items, policy=None):
        self.batch_calls += 1
        self.seen_items = items
        return self.shorten_batch_out


@pytest.fixture
def job_with_transcript(tmp_path):
    job = Job(tmp_path / "job")
    write_json(
        job.transcript_path,
        [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "Hello there friend"},
            {"id": 1, "start": 2.0, "end": 4.0, "text": "This is fine"},
        ],
    )
    return job


def test_over_budget_single_batch_shorten(job_with_transcript, monkeypatch, capsys):
    # max_chars = 2s*14 = 28 for each; segment 0 returns 40 chars -> over 28*1.1.
    over = "một dòng rất dài dao động vượt quá giới hạn ký tự được cho phép"
    normal = "Đây là dòng hợp lệ"
    translate_out = [{"id": 0, "vi": over}, {"id": 1, "vi": normal}]
    short = "Dòng ngắn hơn"

    fake = FakeClient(translate_out, [{"id": 0, "vi": short}])
    monkeypatch.setattr(s3, "LLMClient", lambda provider, **k: fake)

    class FakeProvider:
        name = "fake"
        model = "fake-model"

    monkeypatch.setattr(s3, "get_provider", lambda config, pid=None, **k: FakeProvider())

    job = job_with_transcript
    config = Config(chars_per_sec=14)
    # This test covers the enforcement pass: old per-segment mode keeps its
    # semantics; unit mode is covered in test_units_and_prompts.py.
    config.translate = {"unit_mode": "segment", "budget_overshoot": 1.1}
    s3.run(job, config, force=False, provider_id="gemini")

    entries = {e["id"]: e for e in json.loads(job.translated_path.read_text(encoding="utf-8"))}
    assert entries[0]["vi"] == short  # enforcement applied
    assert entries[1]["vi"] == normal  # unchanged
    assert fake.batch_calls == 1  # exactly ONE enforcement batch
    assert fake.seen_items[0]["max_chars"] == entries[0]["max_chars"]
    # Summary includes provider, model, before/after counts, tokens
    summary = capsys.readouterr().out
    assert "provider=fake" in summary
    assert "over_max_chars_before=1" in summary
    assert "over_max_chars_after=" in summary
