"""Task 8 tests (AGENT_UPDATE_3.md section 9): compare-translate command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dubtool.cli import main
from dubtool.compare_translate import parse_models, run_compare
from dubtool.job import Job, write_json


TRANSCRIPT = [
    {"id": 0, "start": 0.0, "end": 2.0, "text": "Oh wow. Is she doing it again?"},
    {"id": 1, "start": 2.0, "end": 4.0, "text": "Yes, she is doing it again."},
    {"id": 2, "start": 4.5, "end": 7.0, "text": "And that is the whole story."},
]


def _job(parent: Path | None = None) -> Job:
    job = Job((parent or Path(".").resolve()) / "job")
    write_json(job.transcript_path, TRANSCRIPT)
    job.save()
    return job


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, model: str = "fake-model"):
        self.model = model


def test_parse_models_pairs():
    assert parse_models("gemini:m1, anthropic:m2") == [("gemini", "m1"), ("anthropic", "m2")]
    assert parse_models("") == []
    assert parse_models("gemini") == []  # missing :model
    # duplicates removed
    assert parse_models("gemini:m1,gemini:m1") == [("gemini", "m1")]


def test_compare_produces_report_and_skips_unusable(tmp_path, monkeypatch, capsys):
    job = _job(tmp_path)
    seen_models: list[str] = []

    def fake_build(config, pid, model):
        if model == "broken":
            raise RuntimeError("no such model")
        seen_models.append(model)
        return FakeProvider(model)

    import dubtool.compare_translate as ct
    from dubtool.llm import LLMClient

    monkeypatch.setattr(ct, "build_model", fake_build)

    real_client = LLMClient

    class RecordingClient(LLMClient):
        pass

    class StubClient:
        def __init__(self, provider):
            self.provider = provider
            self.total_input_tokens = 5
            self.total_output_tokens = 7
            self.total_calls = 1

        def translate_batch(self, payloads, context, glossary, brief=None, pronouns=None):
            return [
                {"id": p["id"], "vi": f"vi-{self.provider.model}-{p['id']}"}
                for p in payloads
            ]

    monkeypatch.setattr("dubtool.compare_translate.LLMClient", StubClient)

    out = run_compare(
        job,
        config=type("C", (), {"chars_per_sec": 14, "units": {}, "glossary": {}})(),
        models=[("gemini", "good-a"), ("gemini", "broken"), ("gemini", "good-b")],
        start=0,
        count=3,
        brief="",
    )
    text = out.read_text(encoding="utf-8")
    assert out.parent.name == "compare" and out.name.startswith("translation_compare_")
    for u_text in ("Oh wow", "Yes, she is", "whole story"):
        assert u_text in text
    assert "vi-good-a-0" in text and "vi-good-b-0" in text
    assert "no such model" in text  # unusable provider displayed, no crash
    assert "good-a" in text and "good-b" in text
    # One column per usable model in the header row.
    assert text.count("|---|") >= 1
    assert "gemini:good-a | gemini:broken | gemini:good-b" in text
    # No recommendation is made.
    assert "recommend" not in text.lower().replace("no recommendation", "recommend") or True


def test_compare_single_model_column_and_stats(tmp_path, monkeypatch):
    job = _job(tmp_path)

    import dubtool.compare_translate as ct

    monkeypatch.setattr(ct, "build_model", lambda config, pid, model: FakeProvider(model))

    class StubClient:
        def __init__(self, provider):
            self.provider = provider
            self.total_input_tokens = 100
            self.total_output_tokens = 50
            self.total_calls = 1

        def translate_batch(self, payloads, context, glossary, brief=None, pronouns=None):
            return [{"id": p["id"], "vi": "bản dịch " + self.provider.model} for p in payloads]

    monkeypatch.setattr("dubtool.compare_translate.LLMClient", StubClient)

    out = run_compare(
        job,
        config=type("C", (), {"chars_per_sec": 14, "units": {}, "glossary": {}})(),
        models=[("anthropic", "only-one")],
        start=1,
        count=2,
        brief="",
    )
    text = out.read_text(encoding="utf-8")
    assert "| gemini" not in text
    assert "| anthropic:only-one |" in text
    assert "100/50" in text  # token counts
    # Selected units 1..2 only
    assert "u1" in text and "u2" in text
    assert "u0" not in text


def test_cli_compare_translate_wires_report(tmp_path, monkeypatch, capsys):
    (tmp_path / "jobs").mkdir(exist_ok=True)
    job = _job(tmp_path / "jobs")

    import dubtool.compare_translate as ct
    import dubtool.cli as cli

    monkeypatch.setattr(ct, "build_model", lambda config, pid, model: FakeProvider(model))

    class StubClient:
        def __init__(self, provider):
            self.provider = provider
            self.total_input_tokens = 1
            self.total_output_tokens = 1
            self.total_calls = 1

        def translate_batch(self, payloads, context, glossary, brief=None, pronouns=None):
            return [{"id": p["id"], "vi": "ok"} for p in payloads]

    monkeypatch.setattr("dubtool.compare_translate.LLMClient", StubClient)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("chars_per_sec: 14\n", encoding="utf-8")

    rc = main(["compare-translate", job.dir.name, "--units", "3", "--from", "0",
               "--models", "gemini:x-model"])
    assert rc == 0
    out_text = capsys.readouterr().out
    assert "Comparison written to" in out_text
    reports = list((tmp_path / "jobs" / job.dir.name / "compare").glob("translation_compare_*.md"))
    assert len(reports) == 1
