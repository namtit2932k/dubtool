"""Update 23 regression tests:
1. Sentence-mode completeness pass inspects whole dub_units (never sliced
   segments), preventing false-positive dangling rewrites & spliced periods.
2. Completeness pass rejects untranslated CJK characters.
3. Default dub_gain_db is +2.5 dB in config.yaml and MIX_DEFAULTS.
4. MainWindow auto-scrolls to progress_group in 1-column stacked mode.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dubtool import config as config_mod
from dubtool.llm.base import LLMError
from dubtool.stages import s3_translate as s3


def _job_stub(tmp_path: Path):
    return SimpleNamespace(
        dir=tmp_path,
        transcript_path=tmp_path / "transcript.json",
        translated_path=tmp_path / "translated.json",
        dub_units_path=tmp_path / "dub_units.json",
        detected_language_path=tmp_path / "detected_language.json",
        translate_chunks_path=tmp_path / "translate_chunks.json",
        state={},
        persist=lambda: None,
    )


def test_sentence_mode_completeness_runs_on_whole_units_not_sliced_segments(
    tmp_path: Path, monkeypatch
):
    """When a whole sentence unit is split across 3 segments in translated.json,
    a mid-sentence slice may end on a connector word like 'của' (flagging
    `dangling` at the segment level). Update 23 checks whole dub_units so no
    false-positive re-translation splices periods or lowercase fragments back
    into dub_units.json."""
    transcript = [
        {"id": 0, "start": 0.0, "end": 1.2, "text": "可她的衣着气度"},
        {"id": 1, "start": 1.2, "end": 2.4, "text": "周身风华"},
        {"id": 2, "start": 2.4, "end": 3.8, "text": "绝不是寻常世家子弟能比"},
    ]
    job = _job_stub(tmp_path)
    job.transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")

    # Complete Vietnamese sentence whose proportional split across segs 0..2
    # would otherwise leave seg[0] ending in a connector ("của").
    whole_vi = "Nhưng y phục và khí độ của nàng lộng lẫy tuyệt đối không phải người thường."

    class _FakeProvider:
        name = "fake"
        model = "fake-model"

    calls: list[str] = []

    class _FakeClient:
        total_input_tokens = 0
        total_output_tokens = 0
        total_calls = 1
        provider = _FakeProvider()

        def __init__(self, *a, **k):
            pass

        def translate_batch(self, payloads, *_args, **_kwargs):
            calls.append("translate_batch")
            return [{"id": p["id"], "vi": whole_vi} for p in payloads]

        def _call_and_validate(self, system, user, validate, error_note_builder):
            calls.append("completeness_retranslate")
            raise AssertionError("Whole sentence unit is complete; completeness pass must not fire")

    monkeypatch.setattr(s3, "get_provider", lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(s3, "LLMClient", _FakeClient)
    monkeypatch.setattr(s3, "_prepare_sentences", lambda *a, **k: None)
    monkeypatch.setattr(
        s3, "_filler_state", lambda *a, **k: {"policy": None, "count": 0, "enabled": False}
    )

    cfg = SimpleNamespace(
        llm_provider="gemini",
        llm_model="fake-model",
        llm_fallback_model="",
        chars_per_sec=18.0,
        llm={},
        translate={"unit_mode": "sentence", "concurrency": 1},
        units={"max_segments": 4, "max_duration_s": 14.0, "max_gap_s": 1.0, "pause_split_s": 0.5},
        asr={"language": "zh"},
        glossary=None,
        brief=None,
        chunk_size=lambda _pid=None: 20,
    )
    s3.run(job, cfg)

    assert calls == ["translate_batch"]
    dub_units = json.loads(job.dub_units_path.read_text(encoding="utf-8"))
    assert len(dub_units) == 1
    assert dub_units[0]["vi"] == whole_vi


def test_sentence_mode_completeness_propagates_unit_repair_to_segments(
    tmp_path: Path, monkeypatch
):
    """When a whole unit genuinely ends on a dangling connector, completeness
    pass rewrites the whole unit and cleanly redistributes it to segments."""
    transcript = [
        {"id": 0, "start": 0.0, "end": 1.5, "text": "She looked at him"},
        {"id": 1, "start": 1.5, "end": 3.0, "text": "and spoke quietly."},
    ]
    job = _job_stub(tmp_path)
    job.transcript_path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")

    class _FakeProvider:
        name = "fake"
        model = "fake-model"

    class _FakeClient:
        total_input_tokens = 0
        total_output_tokens = 0
        total_calls = 2
        provider = _FakeProvider()

        def __init__(self, *a, **k):
            pass

        def translate_batch(self, payloads, *_args, **_kwargs):
            # Intentionally cut off on connector "và" (no terminal punctuation).
            return [{"id": p["id"], "vi": "Nàng nhìn hắn và"} for p in payloads]

        def _call_and_validate(self, system, user, validate, error_note_builder):
            return validate('[{"id": 0, "vi": "Nàng nhìn hắn rồi khẽ cất lời."}]')

    monkeypatch.setattr(s3, "get_provider", lambda *a, **k: _FakeProvider())
    monkeypatch.setattr(s3, "LLMClient", _FakeClient)
    monkeypatch.setattr(s3, "_prepare_sentences", lambda *a, **k: None)
    monkeypatch.setattr(
        s3, "_filler_state", lambda *a, **k: {"policy": None, "count": 0, "enabled": False}
    )

    cfg = SimpleNamespace(
        llm_provider="gemini",
        llm_model="fake-model",
        llm_fallback_model="",
        chars_per_sec=18.0,
        llm={},
        translate={"unit_mode": "sentence", "concurrency": 1},
        units={"max_segments": 4, "max_duration_s": 14.0, "max_gap_s": 1.0, "pause_split_s": 0.5},
        asr={"language": "en"},
        glossary=None,
        brief=None,
        chunk_size=lambda _pid=None: 20,
    )
    s3.run(job, cfg)

    dub_units = json.loads(job.dub_units_path.read_text(encoding="utf-8"))
    assert dub_units[0]["vi"] == "Nàng nhìn hắn rồi khẽ cất lời."
    assert "uncertain" not in dub_units[0]

    translated = json.loads(job.translated_path.read_text(encoding="utf-8"))
    joined = " ".join(seg["vi"] for seg in translated)
    assert joined == "Nàng nhìn hắn rồi khẽ cất lời."


def test_completeness_pass_rejects_untranslated_cjk(tmp_path: Path):
    """Validator inside _completeness_pass must reject replies leaking Hanzi."""
    entries = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "他说什么？",
         "vi": "Hắn nói và", "max_chars": 40, "reviewed": False},
    ]
    job = _job_stub(tmp_path)

    class _LeakyClient:
        def _call_and_validate(self, system, user, validate, error_note_builder):
            with pytest.raises(LLMError):
                validate('[{"id": 0, "vi": "Hắn nói 什么 thế?"}]')
            return validate('[{"id": 0, "vi": "Hắn nói gì thế?"}]')

    flagged = s3._completeness_pass(
        job, entries, SimpleNamespace(translate={}), _LeakyClient(), None
    )
    assert flagged == []
    assert entries[0]["vi"] == "Hắn nói gì thế?"


def test_dub_gain_default_is_plus_2_5_db():
    assert config_mod.MIX_DEFAULTS["dub_gain_db"] == pytest.approx(2.5)
    cfg = config_mod.load(Path("."))
    assert cfg.mix["dub_gain_db"] == pytest.approx(2.5)


def test_main_window_auto_scrolls_to_progress_in_stacked_layout(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from dubtool.app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("DUBTOOL_HOME", str(tmp_path))

    win = MainWindow()
    try:
        # Stacked (single-column) mode: progress sits below left_box.
        win.resize(760, 520)
        win.show()
        win.progress_group.show_running()
        app.processEvents()
        assert win._split_state == "stack"
        win.scroll.verticalScrollBar().setValue(0)
        win._scroll_to_progress()
        app.processEvents()
        if win.scroll.verticalScrollBar().maximum() > 0:
            assert win.scroll.verticalScrollBar().value() > 0

        # Two-column split mode: _scroll_to_progress must be a no-op.
        win.resize(1160, 720)
        app.processEvents()
        assert win._split_state == "split"
        win.scroll.verticalScrollBar().setValue(0)
        win._scroll_to_progress()
        assert win.scroll.verticalScrollBar().value() == 0
    finally:
        win.close()
