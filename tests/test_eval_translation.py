"""AGENT_UPDATE_8 task 9 tests: the eval-translation harness and the
regression fixtures.

Structure only — nothing here asserts a translation is better. The fixtures
are the English of test2 docx row 1 and its broken Vietnamese (spec
findings 8–9), stored as short text excerpts in tests/fixtures/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8").strip()


def test_row1_regression_fixture_matches_punctuate_fixture():
    """The row-1 English in tests/fixtures equals the ROW1 used by
    test_punctuate (AGU8 task 9: regression fixtures are the excerpt only)."""
    import tests.test_punctuate as tp

    assert _fixture("test2_row1_en.txt") == tp.ROW1


def test_broken_vi_from_findings_is_flagged():
    """The broken Vietnamese of findings 8–9 is flagged structurally: the
    run-on source row is unpunctuated, and the fragment that ends on the
    connector 'luôn' (the mid-sentence break of finding 9) is dangling."""
    from dubtool.complete import flag_unit
    from dubtool.eval_translation import unit_flags

    en = _fixture("test2_row1_en.txt")
    vi = _fixture("test2_row1_vi_broken.txt")
    flags = unit_flags(en, vi)
    assert "unpunctuated_source" in flags["flags"]
    assert "run_on" in flags["flags"]
    # The broken fragment itself: '… vì tôi luôn' is an incomplete clause.
    reason = flag_unit("I support trans woman because I'm", "Tôi ủng hộ phụ nữ chuyển giới vì tôi luôn")
    assert reason == "dangling"


def test_complete_sentence_not_flagged():
    from dubtool.eval_translation import unit_flags

    flags = unit_flags(
        "I like pizza.", "Tôi thích pizza.",
        {"girl's girl": "người luôn đứng về phía phụ nữ"},
    )
    assert "dangling" not in flags["flags"]
    assert "shrunk" not in flags["flags"]
    assert "incomplete" not in flags["flags"]


def test_vocative_detected_and_protected_phrase_not():
    from dubtool.eval_translation import unit_flags
    from dubtool.fillers import tag_fillers

    # "Girl, I support…" — vocative span detected.
    spans = tag_fillers("Girl, I support trans women.", None)
    kinds = [s["kind"] for s in spans]
    assert "vocative" in kinds
    # "I'm a girl's girl" — protected, never a filler.
    assert tag_fillers("I'm a girl's girl, always.", None) == []
    # "trans girls in sports" / "the girls" — ordinary nouns.
    assert tag_fillers("I defend trans girls in sports.", None) == []
    assert tag_fillers("I watch over the girls.", None) == []
    # And through the unit_flags pipeline the filler spans ride along.
    flags = unit_flags("Girl, I support trans women.", "Tôi ủng hộ phụ nữ chuyển giới.")
    assert any(s.get("kind") == "vocative" for s in flags["fillers"])


def test_glossary_hits_and_idioms():
    from dubtool.eval_translation import glossary_hits, idioms_matched

    glossary = {
        "_comment": "never matched",
        "girl's girl": "người luôn đứng về phía phụ nữ",
        "trans girl": "cô gái chuyển giới",
        "idioms": {"girl power": "sức mạnh của phụ nữ"},
    }
    en = "I am a girl's girl. Girl power! I defend trans girls."
    assert "girl's girl" in glossary_hits(en, glossary)
    assert "_comment" not in glossary_hits(en, glossary)
    assert idioms_matched(en, glossary) == ["girl power"]
    assert idioms_matched("nothing here", glossary) == []


class _FakeJob:
    def __init__(self, job_dir: Path):
        self.dir = job_dir
        self.translated_path = job_dir / "translated.json"


class _FakeConfig:
    chars_per_sec = 12.5
    glossary = {}
    units = {}
    llm = {}
    llm_provider = "gemini"


def _write_job_files(job_dir: Path, rows: list[dict]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    import json

    (job_dir / "translated.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (job_dir / "units.json").write_text(
        json.dumps(
            [
                {"unit_id": r["id"], "seg_ids": [r["id"]], "start": float(r["id"]),
                 "end": float(r["id"]) + 3.0, "max_chars": 50}
                for r in rows
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_run_eval_writes_markdown_without_llm(tmp_path):
    """variants=current needs no network and reuses the job's units."""
    from dubtool.eval_translation import run_eval

    job_dir = tmp_path / "jobs" / "job-x"
    rows = [
        {"id": 0, "text": "Girl, I support trans women because I'm a girl's girl.",
         "vi": "Tôi ủng hộ phụ nữ chuyển giới vì tôi luôn."},
        {"id": 1, "text": "Understand that when they attack trans girls in sports.",
         "vi": "Hiểu rằng khi họ tấn công các cô gái chuyển giới trong thể thao."},
    ]
    _write_job_files(job_dir, rows)
    out = run_eval(_FakeJob(job_dir), _FakeConfig(), variants="current", out_dir=tmp_path / "eval")
    md = out.read_text(encoding="utf-8")
    assert "never scores or ranks" in md
    assert "unit 0" in md and "unit 1" in md
    assert "- current: Tôi ủng hộ phụ nữ chuyển giới vì tôi luôn." in md
    assert "- en: Girl, I support" in md
    assert "vocative" in md or "filler:" in md  # the detector findings are listed


def test_run_eval_flags_information(tmp_path):
    from dubtool.eval_translation import run_eval

    job_dir = tmp_path / "jobs" / "job-y"
    rows = [
        {"id": 0, "text": _fixture("test2_row1_en.txt"),
         "vi": _fixture("test2_row1_vi_broken.txt")},
    ]
    _write_job_files(job_dir, rows)
    md = (run_eval(_FakeJob(job_dir), _FakeConfig(), out_dir=tmp_path / "eval")).read_text(encoding="utf-8")
    assert "unpunctuated_source" in md
    assert "shrink_ratio" in md


def test_variant_choices_validated():
    from dubtool.eval_translation import _variant_choices

    assert _variant_choices(None) == ["current"]
    assert _variant_choices("current,faithful") == ["current", "faithful"]
    assert _variant_choices("bogus") == ["current"]


def test_row1_fixture_pair_is_short():
    """Fixtures are short text excerpts only (AGU8 rule 6), never media."""
    for name in ("test2_row1_en.txt", "test2_row1_vi_broken.txt"):
        p = FIXTURES / name
        assert p.exists()
        assert p.stat().st_size < 1000
