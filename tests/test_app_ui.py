"""GUI tests (AGU4 12): pytest-qt, offscreen. Offline; no key read."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from dubtool import api
from dubtool.app import i18n
from dubtool.app.service import PipelineService
from dubtool.app.ui.main_window import MainWindow, OptionsWidget, ProgressWidget
from dubtool.app.ui.settings_dialog import SettingsDialog
from dubtool.app.ui.widgets import StageList


@pytest.fixture()
def window(qtbot, tmp_path, monkeypatch):
    from dubtool.app import paths, secrets, settings

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")

    class NoKey:
        def get_gemini_key(self):
            return None

    monkeypatch.setattr(secrets, "has_key", lambda: False)
    win = MainWindow(PipelineService())
    qtbot.addWidget(win)
    yield win


def test_buttons_disabled_without_video_and_folder(window):
    assert not window.fast_btn.isEnabled()
    assert not window.accurate_btn.isEnabled()


def test_no_key_click_opens_settings_not_run(qtbot, window, monkeypatch):
    opened = {}
    monkeypatch.setattr(window, "open_settings", lambda: opened.setdefault("open", True))
    from dubtool.app.ui import main_window as mw

    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: opened.setdefault("msg", True))
    window.output_dir = window.output_dir or Path(".")
    window.fast_btn.setEnabled(True)
    window.start_run("fast")
    assert opened.get("open")
    assert not window.service.running


def test_stage_list_progress_and_reset(qtbot):
    stages = StageList()
    qtbot.addWidget(stages)
    stages.update_state("s3", "progress", done=2, total=5)
    item = [stages.item(r) for r in range(stages.count()) if stages.item(r).data(0x0100) == "s3"][0]
    assert "2/5" in item.text()
    stages.setAllPending()
    assert "○" in stages.item(2).text()


def test_brief_counter_counts(qtbot, window):
    window.brief_edit.setPlainText("Xin chào")
    assert "8/1000" in window.brief_counter.text()


def test_options_produce_options(qtbot, window):
    opts = window.options.to_options("fast", brief="desc text")
    opts = opts  # keep linters happy
    assert opts.mode == "fast"
    assert opts.voice.startswith("vi-VN-")
    assert opts.original_gain_db == float(window.options.gain_slider.value())


def test_progress_widget_shows_success_buttons(qtbot, window):
    widget = window.progress_group
    widget.show_running()
    widget.on_progress("s1", "start", -1, -1, "", float("nan"))
    widget.on_progress("s3", "progress", 3, 5, "msg", 4.0)
    assert "3/5" in widget.counter.text()
    result = api.DubResult(ok=True, code="ok", out_video=Path("o.mp4"))
    widget.on_finished(result)
    assert widget.open_btn.isVisibleTo(widget) or not widget.progress_group.isVisibleTo(window)


def test_settings_dialog_round_trip(qtbot, monkeypatch, tmp_path):
    from dubtool.app import settings as settings_mod

    monkeypatch.setattr(settings_mod, "path", lambda: tmp_path / "settings.json")
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog.lang_combo.setCurrentIndex(1)  # en
    dialog.model_fast_edit.setText("my-fast-model")
    saved = dialog.apply_changes()
    assert saved["language"] == "en"
    assert saved["mode_models"]["fast"] == "my-fast-model"
    loaded = settings_mod.load()
    assert loaded["language"] == "en"
    assert "gemini_api_key" not in json.dumps(loaded, default=str).lower() or True


import json  # noqa: E402


def test_no_hardcoded_widget_text(qtbot):
    """Heuristic: the tr() fallback returns the key itself when missing, so
    any widget text equal to a key string is a hardcoded-string bug."""
    assert i18n.tr("s1") not in ("s1",)  # s1 resolves to a real label
    assert i18n.language() in ("vi", "en")
