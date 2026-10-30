"""AGENT_UPDATE_8 task 5.2 tests: truncation/shrink detector + re-translation
batch rendering, and the s3 completeness pass wiring (pure + offline)."""

from types import SimpleNamespace

import json

import pytest

from dubtool import complete
from dubtool.stages import s3_translate as s3


# --------------------------------------------------------------------- flags
def test_flag_incomplete_en_punct_vi_not():
    assert complete.flag_unit("Are you serious?", "Cậu nghiêm túc á") == "incomplete"


def test_flag_dangling_connector():
    # EN with no sentence-final punctuation: only the connector rule can fire.
    assert complete.flag_unit("He left", "Anh ấy rời đi nhưng") == "dangling"


def test_flag_shrunk_long_unit():
    en = "x" * 60
    assert complete.flag_unit(en, "ngắn") == "shrunk"


def test_ok_translation_not_flagged():
    assert complete.flag_unit("Are you serious?", "Cậu nghiêm túc á hả?") is None


def test_empty_or_missing_not_flagged():
    assert complete.flag_unit("", "gì đó") is None
    assert complete.flag_unit("Hello.", "") is None


def test_connectors_override():
    # "này" is not in the default list.
    assert complete.flag_unit("Ok", "Xong rồi này") is None
    assert complete.flag_unit("Ok", "Xong rồi này", {"này"}) == "dangling"


def test_flagged_entries_reports_id_and_reason():
    entries = [
        {"id": 1, "text": "Ok, done", "vi": "Xong rồi nhưng", "max_chars": 30},
        {"id": 2, "text": "Hi.", "vi": "Chào.", "max_chars": 10},
        {"id": 3, "text": "", "vi": "trống", "max_chars": 10},  # no text -> skip
    ]
    flags = complete.flagged_entries(entries)
    assert [f["id"] for f in flags] == [1]
    assert flags[0]["reason"] == "dangling"
    assert flags[0]["vi"] == "Xong rồi nhưng"


def test_reviewed_entries_are_not_reported():
    """An entry the user marked as reviewed must not be re-flagged."""
    entries = [{"id": 1, "text": "Ok, done", "vi": "Xong rồi nhưng", "reviewed": True}]
    assert complete.flagged_entries(entries) == []


# ------------------------------------------------------------ re-translate
def test_retranslate_batch_dims_and_allowance():
    items = [{"id": 5, "text": "Where did she go?", "vi": "Cậu đi", "max_chars": 20}]
    user = complete.render_retranslate_batch(items)
    assert '"max_chars": 26' in user  # 20 * 1.3
    assert "Where did she go?" in user
    assert "5" in user


def test_retranslate_system_mentions_json_only():
    assert "JSON" in complete.RETRANSLATE_SYSTEM


# ------------------------------------------------------------- s3 wiring
from dubtool.stages import s8_export_readable as s8

def _job_stub(tmp_path):
    return SimpleNamespace(dir=tmp_path, translated_path=tmp_path / "translated.json")
class _CompletenessProvider:
    name = "fake"
    model = "fake-model"


class _CompletenessClient:
    """Fake LLMClient that answers the re-translation batch with a
    completed text and records the request it saw."""

    total_input_tokens = 0
    total_output_tokens = 0

    def __init__(self, provider=None):
        self.systems_seen = []

    def shorten_batch(self, items, policy=None):
        return []

    def _call_and_validate(self, system, user, validate, error_note_builder):
        self.systems_seen.append(system)
        parse = lambda raw: validate(raw)  # noqa: E731
        return parse('[{"id": 0, "vi": "Cậu đi đâu thế?"}]')


def test_s3_completeness_pass_rewrites_flagged_unit(tmp_path, monkeypatch):
    entries = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "Where did she go?",
         "vi": "Cậu đi", "max_chars": 40, "reviewed": False},
    ]
    (tmp_path / "translated.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    client = _CompletenessClient()
    flagged = s3._completeness_pass(
        _job_stub(tmp_path), entries, type("Cfg", (), {"translate": {}})(), client, None
    )
    # Flagged once before the re-translation, clean afterwards.
    assert flagged == []
    assert entries[0]["vi"] == "Cậu đi đâu thế?"
    assert client.systems_seen == [complete.RETRANSLATE_SYSTEM]


def test_s3_completeness_pass_keeps_still_flagged_and_writes_report(
    tmp_path, monkeypatch
):
    entries = [
        {"id": 0, "start": 1.0, "end": 2.0, "text": "Where did she go",
         "vi": "Cậu đi mà", "max_chars": 40, "reviewed": False},
    ]
    # Answer with the same dangling text: stays flagged, entry untouched.

    class _SameClient(_CompletenessClient):
        def _call_and_validate(self, system, user, validate, error_note_builder):
            return [{"id": 0, "vi": "Cậu đi mà"}]

    flagged = s3._completeness_pass(
        _job_stub(tmp_path), entries, type("Cfg", (), {"translate": {}})(), _SameClient(), None
    )
    assert len(flagged) == 1
    assert flagged[0]["reason"] == "dangling"
    report = json.loads((tmp_path / "quality_report.json").read_text(encoding="utf-8"))
    assert report[0]["time"] == 1.0
    assert report[0]["en"] == "Where did she go"
    assert report[0]["vi"] == "Cậu đi mà"
    assert report[0]["reason"] == flagged[0]["reason"]


def test_s3_marks_still_flagged_entries_and_s8_remarks_rows(tmp_path):
    """AGU8 6.2 end-to-end (pure): the still-flagged entry carries `uncertain`
    into translated.json, and the readable row gets "(cần kiểm tra)" when
    readable.mark_uncertain is enabled (off by default)."""
    entries = [
        {"id": 0, "start": 1.0, "end": 2.0, "text": "Where did she go",
         "vi": "Cậu đi mà", "max_chars": 40, "reviewed": False},
        {"id": 1, "start": 2.0, "end": 3.0, "text": "She is fine.",
         "vi": "Cô ấy ổn.", "max_chars": 30, "reviewed": False},
    ]

    class _SameClient(_CompletenessClient):
        def _call_and_validate(self, system, user, validate, error_note_builder):
            return [{"id": 0, "vi": "Cậu đi mà"}]

    job = _job_stub(tmp_path)
    s3._completeness_pass(job, entries, type("Cfg", (), {"translate": {}})(), _SameClient(), None)
    assert entries[0]["uncertain"] is True
    assert "uncertain" not in entries[1]

    # Fresh translation file with the marked entries, no units.json/schedule.
    s3.write_json(job.translated_path, entries)
    rows_plain = s8.build_rows(tmp_path)
    rows_marked = s8.build_rows(tmp_path, mark_uncertain=True)
    assert not any("kiểm tra" in r["vi"] or "needs review" in r["vi"] for r in rows_plain)
    marked = [r for r in rows_marked if "kiểm tra" in r["vi"] or "needs review" in r["vi"]]
    assert len(marked) == 1
    assert "Where did she go" in marked[0]["en"]  # unit-joined English

    entries = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "Where did she go?",
         "vi": "Cậu đi", "max_chars": 40, "reviewed": False},
    ]

    class _BoomClient:
        total_input_tokens = 0
        total_output_tokens = 0

        def _call_and_validate(self, *a, **k):
            raise RuntimeError("provider exploded")

    flagged = s3._completeness_pass(
        _job_stub(tmp_path), entries, type("Cfg", (), {"translate": {}})(), _BoomClient(), None
    )
    assert len(flagged) == 1
    assert entries[0]["vi"] == "Cậu đi"  # untouched
    report = json.loads((tmp_path / "quality_report.json").read_text(encoding="utf-8"))
    assert report[0]["vi"] == "Cậu đi"
