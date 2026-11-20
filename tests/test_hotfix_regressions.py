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
