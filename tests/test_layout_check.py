"""AGU5 task 4: automated layout invariants at sizes × states (pytest).

The flag `--layout-check [--scale S]` runs the same sweep from the CLI and
is exercised separately (scale passes must be separate processes and need
a font-bearing platform, so they live outside this file)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from dubtool.app import paths, secrets, settings
from dubtool.app.layout_check import SIZES, STATES, apply_state, violations


@pytest.fixture()
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")

    class NoKey:
        def has_key(self):
            return False

    monkeypatch.setattr(secrets, "has_key", lambda: False)
    from dubtool.app.service import PipelineService
    from dubtool.app.ui.main_window import MainWindow

    win = MainWindow(PipelineService())
    qtbot.addWidget(win)
    win.show()
    yield win
    window_cleanup(win)


def window_cleanup(win):
    _flush()
    win.hide()
    _flush()


def _flush():
    for _ in range(3):
        app = QApplication.instance()
        app.processEvents()


def test_layout_invariants_every_size_and_state(qtbot, window):
    """AGU5 task 4 checks 1-4 at all five required sizes and states."""
    report: list[str] = []
    for width, height in SIZES:
        window.resize(QSize(width, height))
        _flush()
        for state in STATES:
            apply_state(window, state)
            _flush()
            problems = violations(window)
            report.append(f"{width}x{height}/{state}: " + ("PASS" if not problems else "; ".join(problems)))
    Path(__file__).with_name("lc_violations.txt").write_text("\n".join(report), encoding="utf-8")
    assert all(line.endswith("PASS") for line in report)


def test_split_flip_at_split_width(qtbot, window):
    """Two columns at >= 1000 px, stacked below — reachable in both."""
    from dubtool.app.ui.main_window import MainWindow

    window.resize(QSize(1100, 800))
    _flush()
    assert window._split_state == "split"
    window.resize(QSize(900, 700))
    _flush()
    assert window._split_state == "stack"
    del MainWindow


def test_settings_dialog_fits_768_high(qtbot):
    """The tabbed Settings dialog must fit a 768-pixel-high screen."""
    from dubtool.app.ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg.show()
    dlg.adjustSize()
    _flush()
    problems = violations(dlg)
    assert not problems, "; ".join(problems)
    assert dlg.minimumSizeHint().height() <= 768
