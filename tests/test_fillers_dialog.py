"""AGENT_UPDATE_8 tasks 4c/2 tests: the real-widget dialogs (filler
suggestions with clicks; the Gemini overload three-button dialog) and the
GUI-thread bridge. Offline; QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog

from dubtool.app.ui.fillers_dialog import (
    DialogBridge,
    FillersDialog,
    OverloadDialog,
)


SUGGESTIONS = [
    {
        "token": "girl",
        "count": 14,
        "examples": [{"seg_id": 0, "time": 1.0, "text": "Girl, I support..."}],
    },
    {"token": "like", "count": 9, "examples": []},
]


def test_fillers_dialog_rows_and_choices(qtbot):
    dlg = FillersDialog(SUGGESTIONS, defaults={"girl": "drop", "like": "keep"})
    qtbot.addWidget(dlg)
    assert len(dlg._rows) == 2
    girl_combo, like_combo = dlg._rows[0][1], dlg._rows[1][1]
    assert girl_combo.currentIndex() == 0          # shipped default: drop
    assert like_combo.currentIndex() == 1          # shipped default: keep

    # Bỏ / Giữ / Dịch thành… exist as choices.
    assert girl_combo.itemText(2) != ""

    girl_combo.setCurrentIndex(2)                     # Dịch thành…
    target = dlg._rows[0][2]
    target.setFocus()
    # Real keyboard events (ASCII: non-ASCII keyboard synthesis hangs on the
    # offscreen platform; the value passthrough with Unicode text is covered
    # by the stage-level tests).
    qtbot.keyClicks(target, "chi em oi")
    dlg.accept()
    choices = dlg.result_choices()
    assert choices == {
        "girl": "translate_as: chi em oi",
        "like": "keep",
    }


def test_fillers_dialog_use_defaults_returns_empty(qtbot):
    dlg = FillersDialog(SUGGESTIONS)
    qtbot.addWidget(dlg)
    dlg._rows[0][1].setCurrentIndex(2)               # user changed something
    dlg.reject()                                     # "Dùng mặc định lần này"
    assert dlg.result_choices() == {}


def test_translate_as_without_text_falls_back_to_drop(qtbot):
    dlg = FillersDialog([{"token": "girl", "count": 5}])
    qtbot.addWidget(dlg)
    dlg._rows[0][1].setCurrentIndex(2)
    dlg.accept()
    assert dlg.result_choices() == {"girl": "drop"}


def test_overload_dialog_three_buttons(qtbot):
    dlg = OverloadDialog(model="gemini-x")
    qtbot.addWidget(dlg)
    lay = dlg.layout()
    btns = [
        lay.itemAt(i).widget() for i in range(lay.count())
        if lay.itemAt(i).widget() is not None and hasattr(lay.itemAt(i).widget(), "clicked")
    ]
    assert len(btns) == 3
    # Emitted clicks (the offscreen-safe equivalent of pressing each button).
    btns[0].clicked.emit()                            # Thử lại sau
    assert dlg.choice == "wait"
    btns[1].clicked.emit()                            # Dùng model Nhanh
    assert dlg.choice == "fallback_fast"
    btns[2].clicked.emit()                            # Hủy
    assert dlg.choice == "cancel"
    assert dlg.windowTitle() == "DubTool"


def test_dialog_bridge_blocks_worker_thread(qtbot):
    """call() from another thread marshals to the GUI thread and returns the
    dialog result; from the GUI thread it runs directly."""
    bridge = DialogBridge()
    # Direct call on the owning (main) thread:
    assert bridge.call(lambda: "direct") == "direct"

    out: list = []
    done = threading.Event()

    def worker():
        out.append(bridge.call(lambda: 41 + 1))
        done.set()

    t = threading.Thread(target=worker, name="bridge-worker", daemon=True)
    t.start()
    # The queued call only runs while the GUI thread spins the event loop.
    deadline = 5.0
    while not done.is_set() and deadline > 0:
        qtbot.wait(50)
        deadline -= 0.05
    assert done.is_set(), "the worker thread was never answered by the GUI thread"
    assert out == [42]
    t.join(2)
    assert not t.is_alive()
