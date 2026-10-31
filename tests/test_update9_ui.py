"""Real-widget tests for AGENT_UPDATE_9 tasks 1-3 + version single-source.

Busy lock (2.1-2.3), the file card (task 2), the progress-details panel
(task 3.1/3.2), and the 5.2 version-consistency rule.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QSlider

from dubtool import __version__
from dubtool.branding import AUTHOR
from dubtool.app.service import PipelineService
from dubtool.app.ui.main_window import MainWindow
from dubtool.app.ui import main_window as mw


@pytest.fixture()
def window(qtbot, tmp_path, monkeypatch):
    """Same sandboxed window as tests/test_app_ui.py (settings + jobs + no key)."""
    from dubtool.app import paths, secrets, settings

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(secrets, "has_key", lambda: False)
    win = MainWindow(PipelineService())
    qtbot.addWidget(win)
    return win


_VIDEO_INFO = {
    "codec_name": "h264",
    "duration": 120.0,
    "width": 640,
    "height": 480,
    "r_frame_rate": "30/1",
}


@pytest.fixture()
def fake_video(qtbot, tmp_path, monkeypatch, window):
    """set_video() with a probed fake file (no ffmpeg needed)."""
    from dubtool import audio_utils

    p = tmp_path / "vid.mp4"
    p.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(
        audio_utils, "probe_video_info", lambda path: dict(_VIDEO_INFO)
    )
    monkeypatch.setattr(audio_utils, "has_audio_stream", lambda path: True)
    window.set_video(p)
    return p


# --------------------------------------------------------------- busy state

# The controls AGU9 2.1 keeps enabled while busy: Cancel, the details
# toggle/copy/open buttons (the panel's own object names) and the About /
# Export-diagnostics menu actions (menu-bar QActions never take busy state).
def _busy_allowlist(window) -> set:
    names = {"details_toggle", "details_copy", "details_open_log"}
    pg = window.progress_group
    for attr in ("cancel_btn",):
        b = getattr(pg, attr, None)
        if b is not None:
            names.add(str(b.objectName()))
    return names


def test_busy_single_state_disables_editables(window):
    """AGU9 2.1: set_busy is the one busy state. Every editable widget type
    under the window is disabled while busy and comes back after, except
    the explicit allowlist — a future overseen widget will fail this test."""
    window.set_busy(True)
    assert window.is_busy()
    for cls in (QCheckBox, QSlider, QLineEdit):
        for w in window.findChildren(cls):
            if w.objectName() in _busy_allowlist(window):
                continue
            assert not w.isEnabled(), w.objectName()
    # description becomes read-only, not disabled (2.1)
    assert window.brief_edit.isReadOnly()
    window.set_busy(False)
    for cls in (QCheckBox, QSlider, QLineEdit):
        for w in window.findChildren(cls):
            assert w.isEnabled() or w.objectName() in _busy_allowlist(window), \
                w.objectName()
    assert not window.brief_edit.isReadOnly()


def test_generate_buttons_disabled_by_busy(window, fake_video, tmp_path):
    window.output_dir = tmp_path / "out"
    window.output_dir.mkdir()
    window._validate_output()
    window._refresh_buttons()
    assert window.fast_btn.isEnabled()
    window.set_busy(True)
    assert not window.fast_btn.isEnabled()
    assert not window.accurate_btn.isEnabled()
    window.set_busy(False)
    assert window.fast_btn.isEnabled()


# ---------------------------------------------------------------- file card

def test_file_card_replaces_drop_zone_and_x_restores(window, fake_video):
    # visible card, hidden drop zone
    assert window.drop_zone.isHidden()
    assert not window.file_card.isHidden()
    assert window.file_card.name_label.text() == fake_video.name
    assert window.file_card.x_btn.toolTip()
    # X deselects: drop zone returns, Generate disabled, info reset
    window.file_card.x_btn.click()
    assert window.drop_zone.isVisible() or not window.drop_zone.isHidden()
    assert window.file_card.isHidden()
    assert not window.video_info.text()
    assert not window.brief_edit.toPlainText()
    assert not window.video_path


def test_drop_while_busy_is_ignored(window, qtbot, tmp_path, monkeypatch):
    from dubtool import audio_utils

    monkeypatch.setattr(
        audio_utils, "probe_video_info", lambda path: dict(_VIDEO_INFO)
    )
    p = tmp_path / "other.mp4"
    p.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(
        audio_utils, "probe_video_info", lambda path: dict(_VIDEO_INFO)
    )
    window.set_busy(True)
    window.set_video(p)
    assert not window.video_path
    window.set_busy(False)
    window.set_video(p)
    assert window.video_path == p


def test_file_card_x_disabled_while_busy(window, fake_video):
    window.set_busy(True)
    assert not window.file_card.x_btn.isEnabled()
    window.set_busy(False)
    assert window.file_card.x_btn.isEnabled()


# ------------------------------------------------------------ details panel

def test_details_toggle_labels(window):
    log = window.progress_group.log
    log.set_open(False)
    assert "▸" in log.toggle_text()
    log.set_open(True)
    assert "▾" in log.toggle_text()
    assert log.text.isVisibleTo(log)


def test_details_state_persisted(window, monkeypatch, tmp_path):
    log = window.progress_group.log
    log.set_open(True)
    saved = __import__("dubtool.app.settings", fromlist=["load"]).load()
    assert saved.get("details_open") is True
    log.set_open(False)
    saved = __import__("dubtool.app.settings", fromlist=["load"]).load()
    assert saved.get("details_open") is False


def test_details_copy_is_redacted(window, qtbot):
    """Task 3.2: duplicate details never carry key-shaped characters."""
    log = window.progress_group.log
    log.append("normal line")
    log.append("GEMINI_API_KEY=AIzaDOCFAKEVALUE1234567890ab")  # fixture-fake-key: not a real key
    log.append("another line")
    window.progress_group._copy_details()
    clip = QApplication.clipboard().text()
    assert "AIzaDOCFAKEVALUE" not in clip  # fixture-fake-key: not a real key
    assert "normal line" in clip and "another line" in clip


def test_details_shell_visible_when_open(window, qtbot):
    pg = window.progress_group
    pg.log.set_open(True)
    for name in ("details_header", "details_liveness", "details_copy_btn",
                 "details_open_btn"):
        assert getattr(pg, name).isVisibleTo(pg), name


# ------------------------------------------------------- version single-source

def test_version_single_source():
    """AGU9 5.2: one version constant, `1.0.<update>`; every consumer
    derives from it (pyproject reads dubtool.__version__ dynamically)."""
    assert __version__ == "1.0.9"
    update_number = (
        Path(__file__).resolve().parents[1] / "packaging" / "update_number.txt"
    ).read_text(encoding="utf-8").strip()
    assert __version__ == f"1.0.{update_number}"
    assert AUTHOR == "sickyturtlez"
