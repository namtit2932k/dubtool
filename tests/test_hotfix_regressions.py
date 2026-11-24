"""Regression tests for the 3 post-v1.0.17 field bugs:

1. Narrowing MainWindow clipped the right edge of ProgressWidget because combo
   boxes with long labels pushed page.minimumSizeHint().width() past the
   QScrollArea viewport.
2. Changing fallback_model in Settings altered S3's mechanical-step model
   (fast_model_id read fallback_model) and opening Settings could reset the
   primary model_combo selection.
3. In windowed PyInstaller builds (sys.stdout/sys.stderr is None), VieNeu/HF
   model initialization crashed with AttributeError("'NoneType' object has no
   attribute 'write'"), and Stage 4 retried every remaining segment instead of
   failing fast when engine initialization failed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def test_narrow_window_never_clips_right_edge_of_progress(qtbot, monkeypatch, tmp_path):
    """Resizing MainWindow down to its minimum width (and 760x600 / 900x700)
    must keep page.width() <= scroll.viewport().width() with zero layout
    violations, even while running with progress details expanded."""
    from PySide6.QtCore import QSize

    from dubtool.app import layout_check, paths, secrets, settings
    from dubtool.app.ui.main_window import MainWindow

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(secrets, "has_key", lambda *a, **k: True)
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()

    try:
        for width in (w.minimumWidth(), 760, 900):
            w.resize(QSize(width, 600))
            qtbot.wait(20)
            layout_check.apply_state(w, "running")
            w.progress_group.log.set_open(True)
            w.progress_group.on_progress(
                "s3", "progress", 0, 0,
                "Đang gọi gemini-3.5-flash-lite — complete (lần 1/3)…", 0.0,
            )
            qtbot.wait(20)
            assert w.page.width() <= w.scroll.viewport().width() + layout_check.OVERLAP_EPS
            assert layout_check.violations(w) == []
    finally:
        w.service.running = False


def test_fast_model_id_ignores_fallback_model_and_uses_primary(monkeypatch):
    """Changing fallback_model in Settings must not override S3's primary model."""
    from dubtool.app import settings as settings_mod
    from dubtool.stages import s3_translate as s3

    monkeypatch.setattr(
        settings_mod, "load",
        lambda: {"fallback_model": "gemini-3.6-flash", "model_id": "gemini-3.5-flash-lite"},
    )
    cfg = SimpleNamespace(llm_model="gemini-3.5-flash-lite")
    assert s3.fast_model_id(cfg) == "gemini-3.5-flash-lite"

    cfg_other = SimpleNamespace(llm_model="gpt-5-mini")
    assert s3.fast_model_id(cfg_other) == "gpt-5-mini"

    cfg_none = SimpleNamespace(llm_model=None)
    assert s3.fast_model_id(cfg_none) != "gemini-3.6-flash"


def test_primary_model_selection_preserved_when_settings_closed(qtbot, monkeypatch, tmp_path):
    """Selecting a primary model in MainWindow and then changing fallback_model
    in Settings must preserve the user's primary model selection."""
    from PySide6.QtCore import Qt
    from dubtool.app import paths, secrets, settings
    from dubtool.app.ui.main_window import MainWindow

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(secrets, "has_key", lambda *a, **k: True)

    w = MainWindow()
    qtbot.addWidget(w)

    # Pick any non-default selectable model in model_combo.
    target_idx = -1
    target_id = ""
    for i in range(w.model_combo.count()):
        item = w.model_combo.model().item(i)
        mid = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if mid and mid != w._selected_model_id():
            target_idx = i
            target_id = str(mid)
            break
    assert target_idx >= 0
    w.model_combo.setCurrentIndex(target_idx)
    assert w._selected_model_id() == target_id

    # Simulate changing fallback_model in Settings and refreshing MainWindow.
    s = settings.load()
    s["fallback_model"] = "gemini-3.6-flash"
    settings.save(s)
    w._update_mode_tips(preserve_current=True)
    assert w._selected_model_id() == target_id


def test_ensure_stdio_and_vieneu_survive_none_stdout_stderr(monkeypatch, tmp_path):
    """Windowed PyInstaller builds have sys.stdout=None and sys.stderr=None;
    both _ensure_stdio() and VieNeuTTSProvider must survive print() calls."""
    from dubtool.app import main as app_main
    from dubtool.tts.vieneu import VieNeuTTSProvider

    # 1. VieNeuTTSProvider protects itself even if called when stdio is None.
    class _NoisyEngine:
        sample_rate = 48000

        def __init__(self, **_kw):
            print("loading vieneu weights to stdout")
            print("progress bar to stderr", file=sys.stderr)

        def infer(self, text, **_kw):
            return np.zeros(4800, dtype=np.float32)

        def close(self):
            pass

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    provider = VieNeuTTSProvider(
        mode="v3turbo_int8",
        cache_dir=tmp_path / "hf_cache",
        engine_factory=_NoisyEngine,
    )
    out = tmp_path / "clip.wav"
    provider.synthesize("Xin chào thế giới.", "Trúc Ly", out)
    assert out.exists() and out.stat().st_size > 400
    provider.close()

    # 2. _ensure_stdio restores usable streams when launched without a console.
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    app_main._ensure_stdio()
    assert sys.stdout is not None and sys.stderr is not None
    print("ok stdout")
    print("ok stderr", file=sys.stderr)


def test_s4_fails_fast_on_engine_init_error(monkeypatch, tmp_path):
    """When VieNeu engine initialization fails, Stage 4 must abort immediately
    after the first segment instead of retrying all remaining segments."""
    from dubtool.config import Config
    from dubtool.job import Job, write_json
    from dubtool.stages import s4_tts
    from dubtool.tts.base import TTSUnavailableError
    from dubtool.tts.vieneu import VieNeuTTSProvider

    job = Job(tmp_path / "job", "input.mp4")
    job.ensure_dirs()
    entries = [
        {"id": i, "start": float(i), "end": float(i + 1), "text": f"hi {i}", "vi": f"xin chào {i}"}
        for i in range(10)
    ]
    write_json(job.translated_path, entries)

    init_attempts = 0

    def _broken_factory(**_kw):
        nonlocal init_attempts
        init_attempts += 1
        raise RuntimeError("broken onnx runtime")

    cfg = Config()
    cfg.tts = {"mode": "v3turbo_int8", "direct_retries": 2}
    cfg.translate = {"unit_mode": "segment"}
    monkeypatch.setattr(s4_tts.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(
        s4_tts, "build_tts",
        lambda c, cache_dir, media_cache=None: s4_tts.SegmentTTS(
            VieNeuTTSProvider(
                mode="v3turbo_int8",
                cache_dir=tmp_path / "hf_cache",
                engine_factory=_broken_factory,
            ),
            cache_dir,
            "Trúc Ly",
            media_cache=media_cache,
        ),
    )

    with pytest.raises(TTSUnavailableError) as exc_info:
        s4_tts.run(job, cfg)

    # Must fail fast on segment 0 after a single init attempt (not 10 * 3 = 30!).
    assert init_attempts == 1
    assert exc_info.value.ids == [0]


def test_repair_word_timings_snaps_leading_orphan_and_splits_internal_pause():
    """Whisper's leading orphan word before a long silence must snap forward
    to the utterance body, and internal silences >= 1.5 s must split into
    separate segments while preserving punctuation."""
    from dubtool.punctuate import repair_word_timings

    segments = [
        {"id": 0, "start": 14.02, "end": 18.60, "text": "竟然是个年人精，薇薇，"},
        {"id": 1, "start": 67.80, "end": 75.45, "text": "下辈子记得长点记性。"},
        {"id": 2, "start": 727.27, "end": 750.06, "text": "令王府副将秋山拜见王妃。"},
    ]
    words = [
        # seg 0: 2.04 s internal pause between 精 (15.86) and 薇 (17.90)
        {"w": "竟", "start": 14.02, "end": 14.66},
        {"w": "然", "start": 14.66, "end": 14.92},
        {"w": "是", "start": 14.92, "end": 15.14},
        {"w": "个", "start": 15.14, "end": 15.24},
        {"w": "年", "start": 15.24, "end": 15.42},
        {"w": "人", "start": 15.42, "end": 15.66},
        {"w": "精", "start": 15.66, "end": 15.86},
        {"w": "薇", "start": 17.90, "end": 18.34},
        {"w": "薇", "start": 18.34, "end": 18.60},
        # seg 1: leading orphan 下 at 67.80, body starts at 74.03 (5.67 s gap)
        {"w": "下", "start": 67.80, "end": 68.36},
        {"w": "辈", "start": 74.03, "end": 74.21},
        {"w": "子", "start": 74.21, "end": 74.37},
        {"w": "记", "start": 74.37, "end": 74.53},
        {"w": "得", "start": 74.53, "end": 74.61},
        {"w": "长", "start": 74.61, "end": 74.81},
        {"w": "点", "start": 74.81, "end": 74.95},
        {"w": "记", "start": 74.95, "end": 75.17},
        {"w": "性", "start": 75.17, "end": 75.45},
        # seg 2: leading orphan 令 at 727.27, body starts at 746.80 (18.9 s gap)
        {"w": "令", "start": 727.27, "end": 727.89},
        {"w": "王", "start": 746.80, "end": 747.00},
        {"w": "府", "start": 747.00, "end": 747.20},
        {"w": "副", "start": 747.20, "end": 747.40},
        {"w": "将", "start": 747.40, "end": 747.58},
        {"w": "秋", "start": 747.58, "end": 747.76},
        {"w": "山", "start": 747.76, "end": 748.04},
        {"w": "拜", "start": 748.04, "end": 748.56},
        {"w": "见", "start": 748.56, "end": 748.80},
        {"w": "王", "start": 748.80, "end": 748.98},
        {"w": "妃", "start": 748.98, "end": 750.06},
    ]

    repaired, _rw, changed = repair_word_timings(segments, words, lang="zh")
    assert changed is True
    # seg 0 split into two segments at the 2.04 s pause
    assert len(repaired) == 4
    assert repaired[0]["text"] == "竟然是个年人精，"
    assert repaired[0]["start"] == pytest.approx(14.02, abs=0.05)
    assert repaired[0]["end"] == pytest.approx(15.86, abs=0.05)
    assert repaired[1]["text"] == "薇薇，"
    assert repaired[1]["start"] == pytest.approx(17.90, abs=0.05)
    # seg 1 orphan snapped from 67.80 -> ~73.73
    assert repaired[2]["start"] >= 73.5
    assert repaired[2]["end"] == pytest.approx(75.45, abs=0.05)
    # seg 2 orphan snapped from 727.27 -> ~746.50
    assert repaired[3]["start"] >= 746.0
    assert repaired[3]["end"] == pytest.approx(750.06, abs=0.05)


def test_merge_short_never_merges_across_long_silence_gap():
    """A short segment (< 0.8 s) after a silence gap > 0.5 s must stay separate."""
    from dubtool.stages.s2_transcribe import merge_short

    segs = [
        {"start": 77.37, "end": 81.06, "text": "是一定不会冲动，"},
        {"start": 87.50, "end": 88.14, "text": "薇薇，"},
    ]
    out = merge_short(segs)
    assert len(out) == 2
    assert out[0]["end"] == pytest.approx(81.06)
    assert out[1]["start"] == pytest.approx(87.50)


def test_progress_details_header_clears_stale_desc_on_stage_change_and_finish(qtbot, monkeypatch, tmp_path):
    """ProgressWidget must clear the previous stage's heartbeat text when a new
    stage starts and show 'Hoàn tất' (not 'Đang làm: ...') after completion."""
    from dubtool.api import DubResult
    from dubtool.app import i18n, paths, secrets, settings
    from dubtool.app.ui.main_window import MainWindow

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(secrets, "has_key", lambda *a, **k: True)
    i18n.set_language("vi")

    w = MainWindow()
    qtbot.addWidget(w)
    pg = w.progress_group
    pg.show_running()
    pg.log.set_open(True)

    pg.on_progress("s5", "progress", -1, -1, "Đang nạp mô hình giọng đọc cục bộ…", 0.0)
    assert "Đang nạp mô hình giọng đọc cục bộ" in pg.details_header.text()

    pg.on_progress("s7", "start", -1, -1, "", 0.0)
    assert "Đang nạp mô hình" not in pg.details_header.text()
    assert "Phụ đề & xuất" in pg.details_header.text()

    pg.on_finished(DubResult(ok=True, code="ok", out_video=tmp_path / "out.mp4"))
    assert pg.details_header.text() == i18n.tr("details.done")
    assert "Đang làm" not in pg.details_header.text()
    assert pg.details_liveness.text() == ""
    assert pg.bar.isTextVisible() is False
    assert pg.counter.text() == "100%"
    assert pg.counter.isHidden() is False


def test_settings_dialog_resizable_has_preview_and_jobs_placeholder(qtbot, monkeypatch, tmp_path):
    """SettingsDialog must be resizable down to ~480px, expose a voice preview
    button, and display the real default jobs folder as placeholder text."""
    from PySide6.QtCore import QSize

    from dubtool.app import paths, secrets, settings
    from dubtool.app.ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(secrets, "has_key", lambda *a, **k: True)
    monkeypatch.setattr(secrets, "get_keys", lambda *a, **k: [])

    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.isSizeGripEnabled() is True
    assert dlg.minimumSizeHint().width() <= 560
    assert settings.DEFAULTS["fallback_model"] == "gemini-3.1-flash-lite"
    assert dlg.fallback_combo.currentData() == "gemini-3.1-flash-lite"
    dlg.resize(QSize(720, 620))
    qtbot.wait(20)
    assert dlg.width() >= 700
    dlg.resize(QSize(540, 460))
    qtbot.wait(20)
    assert dlg.width() <= 560
    from dubtool.app import layout_check
    for tab_idx in (1, 2):
        dlg.tabs.setCurrentIndex(tab_idx)
        qtbot.wait(20)
        overflow = [v for v in layout_check.violations(dlg) if "scroll-content-overflow-x" in v]
        assert overflow == [], f"Tab {tab_idx} clipped horizontally: {overflow}"
    assert hasattr(dlg, "preview_voice_btn")
    assert str(tmp_path / "jobs") in dlg.jobs_edit.placeholderText()
    assert str(tmp_path / "jobs") in dlg.jobs_stats_label.text()


def test_voice_preview_cache_and_default_gemini_catalog(monkeypatch, tmp_path):
    """Voice preview caches synthesized WAV per (mode, voice), and the Gemini
    catalog ships 3.5-flash-lite as default alongside 3.1-flash-lite."""
    from dubtool.app import paths, voice_preview
    from dubtool.llm import catalog

    assert catalog.DEFAULT_MODEL_ID == "auto-rotate"
    items = catalog.load()
    assert "gemini-3.5-flash-lite" in items
    assert "gemini-3.1-flash-lite" in items
    assert "gemini-3-flash-preview" in items
    assert "gemini-flash-lite-latest" in items
    assert items["gemini-3.6-flash"].translate_concurrency == 1
    assert items["gemini-3.8-flash"].translate_concurrency == 1

    monkeypatch.setattr(paths, "app_data_root", lambda: tmp_path / "appdata")
    calls = 0

    class _FakeProv:
        def synthesize(self, text, voice, out_path):
            nonlocal calls
            calls += 1
            Path(out_path).write_bytes(b"RIFF" + b"\x00" * 500)

        def close(self):
            pass

    p1 = voice_preview.ensure_preview_wav("v3turbo_int8", "Trúc Ly", provider_factory=lambda _m: _FakeProv())
    p2 = voice_preview.ensure_preview_wav("v3turbo_int8", "Trúc Ly", provider_factory=lambda _m: _FakeProv())
    assert p1 == p2 and p1.is_file()
    assert calls == 1  # second call hits cache immediately


def test_settings_dialog_dedups_env_keys_and_aligns_spinboxes(monkeypatch):
    """SettingsDialog hides duplicate .env rows already saved in Credential
    Manager, keeps unimported .env rows visible, and aligns both job-limit
    spinboxes to the exact same width and right edge."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from dubtool.app import secrets
    from dubtool.app.ui.settings_dialog import SettingsDialog

    app = QApplication.instance() or QApplication([])
    stored = ["AIzaTEST1111", "AIzaTEST2222"]
    monkeypatch.setattr(secrets, "_stored_list", lambda p="gemini": list(stored) if p == "gemini" else [])
    monkeypatch.setattr(secrets, "get_keys", lambda p="gemini": list(stored) if p == "gemini" else [])
    monkeypatch.setattr(secrets, "has_key", lambda p="gemini": p in ("gemini", "auto"))
    monkeypatch.setattr(
        secrets,
        "env_key_sources",
        lambda p="gemini": [
            ("GEMINI_API_KEY", secrets.mask("AIzaTEST1111")),
            ("GEMINI_API_KEY_2", secrets.mask("AIzaTEST3333")),
        ] if p == "gemini" else [],
    )

    dlg = SettingsDialog()
    try:
        dlg.resize(480, 580)
        dlg.show()
        app.processEvents()
        rows = [dlg.key_list.item(i).text() for i in range(dlg.key_list.count())]
        assert len(rows) == 3
        assert any("1111" in r and "(từ .env)" not in r for r in rows)
        assert not any("1111" in r and "(từ .env)" in r for r in rows)
        assert any("3333" in r and "GEMINI_API_KEY_2" in r for r in rows)

        dlg.tabs.setCurrentIndex(1)
        dlg.resize(320, 440)
        for _ in range(4):
            app.processEvents()
        from dubtool.app.ui.settings_dialog import _WrapLabel

        for lbl in dlg.findChildren(_WrapLabel):
            assert lbl.height() >= lbl.heightForWidth(lbl.width()), lbl.text()

        dlg.tabs.setCurrentIndex(2)
        app.processEvents()
        assert dlg.jobs_days_spin.width() == dlg.jobs_total_mb_spin.width()
        assert dlg.jobs_days_spin.geometry().right() == dlg.jobs_total_mb_spin.geometry().right()
    finally:
        dlg.close()



