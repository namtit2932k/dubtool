"""Comprehensive offline unit & widget tests for Update 16:
- Multilingual source-language detection (3-window VAD vote, uncertain dialog,
  headless warning fallback, mixed-language detection, Vietnamese stop +
  subtitles-only export, English-only *.en model refusal).
- Language-aware sentence boundaries, abbreviation rules, spaceless scripts,
  punctuation restoration prompts/validation, filler skipping, initial_prompt.
- Readable .docx/.txt export: localized header/columns, Arabic RTL (w:bidi,
  w:rtl, right align), CJK font fallback.
- GUI language dropdown, Other... dialog, busy-disable, CLI flags, English
  auto vs manual regression.
- TTS root-cause exception classification (no generic TTSFailure bucket).
"""

from __future__ import annotations

import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np
import pytest

from dubtool import api, cli, config as config_mod, languages as lang_mod, punctuate, units
from dubtool.job import Job, stage_inputs_hash, write_json
from dubtool.llm import prompts
from dubtool.stages import s2_transcribe, s3_translate, s8_export_readable as s8
from dubtool.tts.base import TTSFailure
from dubtool.tts_probe import classify_exact

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class _FakeWhisperModel:
    """Fake faster-whisper model supporting window-by-window detect_language
    and recording transcribe() kwargs."""

    def __init__(self, window_results: list[tuple[str, float, list[tuple[str, float]]]], segments=None):
        self._window_results = list(window_results)
        self._idx = 0
        self.detect_calls = 0
        self.transcribe_kwargs: list[dict] = []
        self._segments = segments or [
            SimpleNamespace(start=0.0, end=2.5, text="Hello world.", words=[])
        ]

    def detect_language(self, audio, **kwargs):
        self.detect_calls += 1
        res = self._window_results[min(self._idx, len(self._window_results) - 1)]
        self._idx += 1
        return res

    def transcribe(self, audio_path, **kwargs):
        self.transcribe_kwargs.append(dict(kwargs))
        lang = kwargs.get("language") or "en"
        info = SimpleNamespace(language=lang, language_probability=0.95, duration=10.0)
        return list(self._segments), info


def _fake_audio(seconds: float = 100.0, sr: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


def _fake_regions(sr: int = 16000) -> list[dict]:
    return [
        {"start": 1 * sr, "end": 10 * sr},
        {"start": 40 * sr, "end": 55 * sr},
        {"start": 75 * sr, "end": 90 * sr},
    ]


# ------------------------------------------------ 1. Language detection (3.5)

def test_detect_majority_vote_over_3_vad_windows(tmp_path):
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    cfg = config_mod.Config()
    cfg.asr["language"] = "auto"

    model = _FakeWhisperModel([
        ("ja", 0.92, [("ja", 0.92), ("en", 0.05)]),
        ("ja", 0.88, [("ja", 0.88), ("en", 0.08)]),
        ("ja", 0.96, [("ja", 0.96), ("en", 0.02)]),
    ])
    info = s2_transcribe.detect_source_language(
        model, job, cfg, audio=_fake_audio(), speech_regions=_fake_regions()
    )
    assert model.detect_calls == 3
    assert info["code"] == "ja"
    assert info["name"] == "Japanese"
    assert info["probability"] == pytest.approx((0.92 + 0.88 + 0.96) / 3, abs=1e-3)
    assert info["uncertain"] is False
    assert info["mixed"] is False
    assert len(info["windows"]) == 3
    assert [w["start"] for w in info["windows"]] == [1.0, 40.0, 75.0]
    assert job.state["source_language"]["code"] == "ja"


def test_detect_low_confidence_headless_fallback_warns(tmp_path, caplog):
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    cfg = config_mod.Config()
    cfg.asr["language"] = "auto"
    cfg.asr["language_min_confidence"] = 0.60

    model = _FakeWhisperModel([
        ("es", 0.50, [("es", 0.50), ("pt", 0.30), ("en", 0.20)]),
        ("es", 0.45, [("es", 0.45), ("pt", 0.35), ("en", 0.20)]),
        ("es", 0.55, [("es", 0.55), ("pt", 0.25), ("en", 0.20)]),
    ])
    with caplog.at_level("WARNING", logger="dubtool"):
        info = s2_transcribe.detect_source_language(
            model, job, cfg, audio=_fake_audio(), speech_regions=_fake_regions()
        )
    assert info["code"] == "es"
    assert info["uncertain"] is True
    assert any("uncertain" in rec.message.lower() for rec in caplog.records)


def test_detect_low_confidence_gui_dialog_real_widgets(qtbot, tmp_path):
    from dubtool.app.ui.language_dialogs import LanguageUncertainDialog

    job = Job(tmp_path / "job")
    job.ensure_dirs()
    cfg = config_mod.Config()
    cfg.asr["language"] = "auto"

    captured_candidates = []

    def _dialog(candidates, mixed):
        captured_candidates.extend(candidates)
        dlg = LanguageUncertainDialog(candidates, mixed=mixed)
        qtbot.addWidget(dlg)
        assert len(dlg.radios) == 3
        # Pick the second candidate via real widget radio button + Continue button
        dlg.radios[1][0].setChecked(True)
        dlg.continue_btn.click()
        return dlg.choice

    cfg.language_dialog = _dialog
    model = _FakeWhisperModel([
        ("es", 0.48, [("es", 0.48), ("pt", 0.32), ("en", 0.20)]),
        ("es", 0.46, [("es", 0.46), ("pt", 0.34), ("en", 0.20)]),
        ("es", 0.49, [("es", 0.49), ("pt", 0.31), ("en", 0.20)]),
    ])
    info = s2_transcribe.detect_source_language(
        model, job, cfg, audio=_fake_audio(), speech_regions=_fake_regions()
    )
    assert len(captured_candidates) == 3
    assert info["code"] == "pt"
    assert info["method"] == "auto_confirmed"


def test_detect_mixed_language_sets_multilingual_flag(tmp_path):
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    cfg = config_mod.Config()
    cfg.asr["language"] = "auto"

    model = _FakeWhisperModel([
        ("en", 0.90, [("en", 0.90), ("es", 0.08)]),
        ("es", 0.88, [("es", 0.88), ("en", 0.10)]),
        ("en", 0.92, [("en", 0.92), ("es", 0.06)]),
    ])
    info = s2_transcribe.detect_source_language(
        model, job, cfg, audio=_fake_audio(), speech_regions=_fake_regions()
    )
    assert info["mixed"] is True
    assert info["uncertain"] is True
    assert info["multilingual_used"] is True


def test_english_only_model_refuses_non_english(tmp_path):
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    cfg = config_mod.Config()
    cfg.whisper_model = "small.en"
    cfg.asr["language"] = "ja"

    model = _FakeWhisperModel([])
    with pytest.raises(RuntimeError, match="small\\.en"):
        s2_transcribe.detect_source_language(model, job, cfg)

    # Also refuses when auto-detection yields non-English
    cfg.asr["language"] = "auto"
    model2 = _FakeWhisperModel([("ja", 0.95, [("ja", 0.95)])])
    with pytest.raises(RuntimeError, match="small\\.en"):
        s2_transcribe.detect_source_language(
            model2, job, cfg, audio=_fake_audio(), speech_regions=_fake_regions()
        )


def test_vietnamese_detected_stops_before_translate_and_offers_subtitles_only(tmp_path, monkeypatch):
    video = tmp_path / "viet.mp4"
    video.write_bytes(b"fake-video")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    jobs_dir = tmp_path / "jobs"

    from dubtool.stages import s1_extract_audio, s3_translate as s3_mod, s4_tts as s4_mod

    def fake_s1(job, cfg, force=False, cancel=None):
        job.audio_path.write_bytes(b"fake-wav")

    def fake_s2(job, cfg, force=False):
        write_json(job.transcript_path, [
            {"id": 0, "start": 0.0, "end": 2.5, "text": "Xin chào các bạn."},
            {"id": 1, "start": 2.6, "end": 5.0, "text": "Hôm nay trời rất đẹp."},
        ])
        job.state["source_language"] = {
            "code": "vi", "name": "Vietnamese", "probability": 0.96, "method": "auto",
        }
        job.save()
        raise s2_transcribe.VietnameseSourceDetected(0.96, transcript_path=job.transcript_path)

    llm_called = []
    tts_called = []
    monkeypatch.setattr(s1_extract_audio, "run", fake_s1)
    monkeypatch.setattr(s2_transcribe, "run", fake_s2)
    monkeypatch.setattr(s3_mod, "run", lambda *a, **kw: llm_called.append(True))
    monkeypatch.setattr(s4_mod, "run", lambda *a, **kw: tts_called.append(True))

    class FakeSecrets:
        def get_keys(self, provider="gemini"):
            return ["fake-key"]  # fixture-fake-key

    res = api.run_dub(
        video, out_dir,
        options=api.DubOptions(video_language="auto"),
        secrets=FakeSecrets(),
        progress=lambda _e: None,
        cancel=threading.Event(),
        jobs_dir=jobs_dir,
        interactive=False,
    )
    assert res.code == "source_is_vietnamese"
    assert res.ok is False
    assert "tiếng Việt" in (res.error or "")
    assert llm_called == []
    assert tts_called == []

    # Now with subtitles_only=True (and no API key needed!)
    res_sub = api.run_dub(
        video, out_dir,
        options=api.DubOptions(video_language="auto", subtitles_only=True),
        secrets=None,
        progress=lambda _e: None,
        cancel=threading.Event(),
        jobs_dir=jobs_dir,
        interactive=False,
        force=True,
    )
    assert res_sub.ok is True
    assert res_sub.code == "subtitles_only"
    assert res_sub.out_srt and res_sub.out_srt.is_file()
    assert res_sub.out_readable and res_sub.out_readable.is_file()
    assert "Xin chào các bạn." in res_sub.out_srt.read_text(encoding="utf-8")
    assert llm_called == []
    assert tts_called == []


# -------------------------------- 2. Language-aware text handling (3.5)

def test_multilingual_sentence_boundaries_and_abbreviations():
    # English: Mr. is an abbreviation, does not end a sentence
    assert not units.ends_with_sentence("Hello Mr.", lang="en")
    assert units.ends_with_sentence("Hello world.", lang="en")
    # Non-English: English abbreviation stop-list does NOT apply
    assert units.ends_with_sentence("Hallo Dr.", lang="de")
    # Spanish: inverted opening marks do not end sentences; closing marks do
    assert not units.ends_with_sentence("¿Cómo estás", lang="es")
    assert units.ends_with_sentence("¿Cómo estás?", lang="es")
    assert units.ends_with_sentence("¡Qué bien!", lang="es")
    # Chinese & Japanese
    assert units.ends_with_sentence("你好世界。", lang="zh")
    assert units.ends_with_sentence("元気ですか？", lang="ja")
    assert units.ends_with_sentence("すごい！", lang="ja")
    # Arabic & Hindi
    assert units.ends_with_sentence("كيف حالك؟", lang="ar")
    assert units.ends_with_sentence("नमस्ते।", lang="hi")


def test_thai_splits_by_pauses_and_skips_punctuation_trigger():
    thai_segs = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "สวัสดีครับวันนี้เราจะมาคุยกัน"},
        {"id": 1, "start": 2.8, "end": 4.5, "text": "เรื่องเทคโนโลยีใหม่ที่น่าสนใจ"},
    ]
    assert punctuate.needs_punctuation(thai_segs, lang="th") is False
    built = units.build_units(thai_segs, pause_split_s=0.5, lang="th")
    assert len(built) == 2


def test_spaceless_fingerprint_and_s2_skips_space_dedupe_and_initial_prompt(tmp_path):
    # Spaceless punctuation fingerprint ignores punctuation & whitespace
    assert punctuate.fingerprint("你好世界", lang="zh") == punctuate.fingerprint("你好，世界！", lang="zh")
    assert punctuate.fingerprint("こんにちは元気", lang="ja") == punctuate.fingerprint("こんにちは、元気？", lang="ja")

    # s2 _transcribe skips initial_prompt and space-based dedupe for ja/zh/th
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    job.audio_path.write_bytes(b"wav")
    cfg = config_mod.Config()
    cfg.asr["language"] = "ja"
    cfg.asr["initial_prompt"] = "English sample prompt."
    model = _FakeWhisperModel([], segments=[
        SimpleNamespace(start=0.0, end=2.0, text="今日はいい天気ですね。", words=[]),
    ])
    s2_transcribe._transcribe(model, job, cfg)
    assert "initial_prompt" not in model.transcribe_kwargs[0]
    assert model.transcribe_kwargs[0]["language"] == "ja"

    # For English, initial_prompt IS passed
    cfg_en = config_mod.Config()
    cfg_en.asr["language"] = "en"
    cfg_en.asr["initial_prompt"] = "English sample prompt."
    model_en = _FakeWhisperModel([], segments=[
        SimpleNamespace(start=0.0, end=2.0, text="Hello world.", words=[]),
    ])
    s2_transcribe._transcribe(model_en, job, cfg_en)
    assert model_en.transcribe_kwargs[0].get("initial_prompt") == "English sample prompt."


def test_prompts_include_source_lang_and_fillers_skipped_for_non_english(tmp_path):
    user_prompt = prompts.render_translate_user(
        [{"id": 0, "text": "こんにちは。", "max_chars": 30}],
        context=[], glossary={}, source_lang="Japanese",
    )
    assert "from Japanese into Vietnamese" in user_prompt

    shorten_prompt = prompts.render_shorten_user(
        "こんにちは。", "Xin chào rất nhiều.", 15, source_lang="Japanese"
    )
    assert "Original Japanese segment:" in shorten_prompt

    # Filler detector skipped for Japanese
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    job.state["source_language"] = {"code": "ja", "name": "Japanese", "probability": 0.95}
    cfg = config_mod.Config()
    cfg.asr["language"] = "ja"
    transcript = [{"id": 0, "start": 0.0, "end": 2.0, "text": "girl, um, like, こんにちは"}]
    fill_state = s3_translate._filler_state(job, cfg, transcript)
    assert fill_state["enabled"] is False
    assert fill_state["suggestions"] == []
    assert fill_state["policy"] == ""


# ----------------------------------------- 3. Readable .docx XML (RTL & CJK)

def test_readable_docx_arabic_rtl_and_cjk_fonts(tmp_path):
    pytest.importorskip("docx")

    # Arabic RTL check
    ar_out = tmp_path / "ar_vi_translation.docx"
    ar_meta = s8.make_meta(Path("ar_vi.mp4"), lang="vi", duration_s=45.0, source_lang="ar")
    assert ar_meta["lang_pair"] == "Tiếng Ả Rập → Tiếng Việt"
    assert ar_meta["labels"]["col_en"] == "Bản gốc (Tiếng Ả Rập)"
    s8.render_docx(ar_out, ar_meta, [{"time": 0.0, "en": "مرحبا بالعالم.", "vi": "Xin chào thế giới."}])
    with zipfile.ZipFile(ar_out) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    bidis = list(root.iter(f"{W_NS}bidi"))
    rtls = list(root.iter(f"{W_NS}rtl"))
    jcs = [el.get(f"{W_NS}val") for el in root.iter(f"{W_NS}jc")]
    assert len(bidis) >= 1, "Arabic original paragraph must set w:bidi"
    assert len(rtls) >= 1, "Arabic original run must set w:rtl"
    assert "right" in jcs, "Arabic original paragraph must be right-aligned"

    # Japanese CJK font check
    ja_out = tmp_path / "ja_vi_translation.docx"
    ja_meta = s8.make_meta(Path("ja_vi.mp4"), lang="vi", duration_s=45.0, source_lang="ja")
    assert ja_meta["lang_pair"] == "Tiếng Nhật → Tiếng Việt"
    assert ja_meta["labels"]["col_en"] == "Bản gốc (Tiếng Nhật)"
    s8.render_docx(ja_out, ja_meta, [{"time": 0.0, "en": "こんにちは。", "vi": "Xin chào."}])
    with zipfile.ZipFile(ja_out) as z:
        root_ja = ElementTree.fromstring(z.read("word/document.xml"))
    east_asia_fonts = {el.get(f"{W_NS}eastAsia") for el in root_ja.iter(f"{W_NS}rFonts")}
    assert "Yu Gothic UI" in east_asia_fonts
    assert "Arial" in east_asia_fonts


# ------------------------------------------- 4. UI, CLI & Regression (3.5)

def test_ui_language_dropdown_other_list_and_busy_disable(qtbot, monkeypatch, tmp_path):
    from dubtool.app import paths
    from dubtool.app.ui.language_dialogs import OtherLanguageDialog
    from dubtool.app.ui.main_window import MainWindow

    monkeypatch.setattr(paths, "app_data_root", lambda: tmp_path / "appdata")
    win = MainWindow()
    qtbot.addWidget(win)

    # No duplicate ui_lang_combo inside OptionsWidget
    assert not hasattr(win.options, "ui_lang_combo")
    # Default is auto
    assert win.lang_video_combo.currentData() == "auto"
    codes = [win.lang_video_combo.itemData(i) for i in range(win.lang_video_combo.count())]
    for expected in ("auto", "en", "ja", "zh", "ko", "es", "fr", "de", "ru", "pt", "it", "th", "hi", "ar", "id", "__other__"):
        assert expected in codes
    # No raw i18n keys leaked as labels
    for i in range(win.lang_video_combo.count()):
        assert not win.lang_video_combo.itemText(i).startswith("language.")

    # OtherLanguageDialog lists all Whisper-supported codes
    other = OtherLanguageDialog(win)
    qtbot.addWidget(other)
    assert other.list_widget.count() >= 90

    # Disabled while busy
    win.set_busy(True)
    assert not win.lang_video_combo.isEnabled()
    win.set_busy(False)
    assert win.lang_video_combo.isEnabled()


def test_cli_source_language_and_subtitles_only_flags():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "video.mp4", "--source-language", "ja", "--subtitles-only"])
    assert args.source_language == "ja"
    assert args.subtitles_only is True

    args_stage = parser.parse_args(["stage", "job1", "s2", "--source-language", "es"])
    assert args_stage.source_language == "es"


def test_english_auto_vs_manual_regression_and_inputs_hash(tmp_path):
    segs = [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "Dark energy is a strange phenomenon"},
        {"id": 1, "start": 3.0, "end": 6.0, "text": "that scientists believe permeates the universe."},
        {"id": 2, "start": 6.1, "end": 9.0, "text": "Mr. Smith explained it clearly."},
    ]
    u_auto = units.build_units(segs, lang=lang_mod.normalize_code("auto", default="en"))
    u_en = units.build_units(segs, lang="en")
    assert u_auto == u_en

    # source_language is part of stage_inputs_hash for s3
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    cfg = config_mod.Config()
    job.state["source_language"] = {"code": "en", "method": "auto"}
    h_en = stage_inputs_hash(job, cfg, "s3")
    job.state["source_language"] = {"code": "ja", "method": "auto"}
    h_ja = stage_inputs_hash(job, cfg, "s3")
    assert h_en != h_ja


# ----------------------------------------- 5. TTS root-cause test (Task 3.1)

def test_tts_classify_exact_unwraps_nested_cause_and_context():
    class NoAudioReceived(Exception):
        pass

    class WSServerHandshakeError(Exception):
        pass

    # Explicit __cause__ chain: TTSFailure <- RuntimeError <- NoAudioReceived
    try:
        try:
            raise NoAudioReceived("No audio was received.")
        except NoAudioReceived as inner:
            raise RuntimeError("synth attempt failed") from inner
    except RuntimeError as mid:
        wrapped = TTSFailure("all retries failed")
        wrapped.__cause__ = mid
    assert classify_exact(wrapped) == "NoAudioReceived"

    # Implicit __context__ chain: TTSFailure <- WSServerHandshakeError(503)
    try:
        try:
            raise WSServerHandshakeError("503, message='Invalid response status'")
        except WSServerHandshakeError:
            raise TTSFailure("edge-tts failed")
    except TTSFailure as exc:
        assert classify_exact(exc) == "handshake_503"
