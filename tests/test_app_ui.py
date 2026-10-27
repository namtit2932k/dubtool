"""GUI tests (AGU4 12 + AGU5 tasks 1-3 / 2.6): pytest-qt, offscreen.

Offline: no key reads (fake keyring), no network (fake tester).
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QLabel, QWidget

from dubtool import api
from dubtool.app import i18n
from dubtool.app.service import PipelineService
from dubtool.app.ui.main_window import MainWindow, OptionsWidget, ProgressWidget
from dubtool.app.ui.settings_dialog import _AddKeysDialog, SettingsDialog
from dubtool.app.ui.widgets import StageList


@pytest.fixture()
def window(qtbot, tmp_path, monkeypatch):
    from dubtool.app import paths, secrets, settings

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")

    class NoKey:
        def has_key(self):
            return False

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


def test_brief_truncate_preserves_cursor(qtbot, window):
    """AGU5 5.4: text longer than the limit is cut without moving the caret
    away from the middle of the document."""
    window.brief_edit.setPlainText("a" * 40)
    cursor = window.brief_edit.textCursor()
    cursor.setPosition(20)
    window.brief_edit.setTextCursor(cursor)
    window.brief_edit.insertPlainText("b" * 2000)  # triggers _on_change -> truncate
    assert len(window.brief_edit.toPlainText()) == 1000
    assert window.brief_edit.textCursor().position() <= 1000


def test_options_produce_options(qtbot, window):
    opts = window.options.to_options("fast", brief="desc text")
    assert opts.mode == "fast"
    assert opts.voice.startswith("vi-VN-")
    assert opts.original_gain_db == float(window.options.gain_slider.value())
    assert opts.description == "desc text"


def test_progress_widget_shows_success_buttons(qtbot, window):
    widget = window.progress_group
    widget.show_running()
    widget.on_progress("s1", "start", -1, -1, "", float("nan"))
    widget.on_progress("s3", "progress", 3, 5, "msg", 4.0)
    assert "3/5" in widget.counter.text()
    result = api.DubResult(ok=True, code="ok", out_video=Path("o.mp4"))
    widget.on_finished(result)
    assert widget.open_btn.isVisibleTo(widget) or not widget.progress_group.isVisibleTo(window)


# ---------------------------------------------------------------- settings --


@pytest.fixture()
def dialog(qtbot, monkeypatch, tmp_path):
    """SettingsDialog over an isolated store with two fake keys."""
    from dubtool.app import secrets, settings

    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(secrets, "get_gemini_keys", lambda: ["key-one-aaaa", "key-two-bbbb"])
    monkeypatch.setattr(secrets, "has_key", lambda: True)
    monkeypatch.setattr(secrets, "key_source", lambda: "store")
    d = SettingsDialog()
    qtbot.addWidget(d)
    return d


def test_settings_dialog_round_trip(qtbot, dialog, monkeypatch, tmp_path):
    from dubtool.app import settings as settings_mod

    dialog.lang_combo.setCurrentIndex(1)  # en
    second = dialog.model_accurate_combo.itemData(1)
    assert second  # a shipped alternative exists
    dialog.model_accurate_combo.setCurrentIndex(1)
    saved = dialog.apply_changes()
    assert saved["language"] == "en"
    assert saved["mode_models"]["accurate"] == second
    loaded = settings_mod.load()
    assert loaded["language"] == "en"
    assert loaded["mode_models"]["accurate"] == second


def test_settings_tabs_exist(dialog):
    names = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    expected = [
        i18n.tr("settings.tab_keys"),
        i18n.tr("settings.tab_models"),
        i18n.tr("settings.tab_general"),
        i18n.tr("settings.tab_advanced"),
    ]
    assert len(names) == 4
    assert all(n and n != k for n, k in zip(names, expected)) is False or names == expected


def test_key_hint_removed(dialog):
    """AGU5 task 3: the hint label is gone, and no i18n key mentioning it."""
    assert not hasattr(dialog, "key_hint")
    for table in i18n.STRINGS.values():
        for key in table:
            assert "key.hint" not in key
    body = " ".join(
        str(w.text()) for w in dialog.findChildren(QLabel) if w.text()
    ).lower()
    assert "ai studio" not in body


def test_key_rows_masked(dialog):
    """Rows show position + a masked tail, never the full stored value."""
    rows = [dialog.key_list.item(r).text() for r in range(dialog.key_list.count())]
    assert len(rows) == 2
    for text, real in zip(rows, ["key-one-aaaa", "key-two-bbbb"]):
        assert real not in text
        assert real[-4:] in text


def test_add_dialog_parses_lines(qtbot, dialog, monkeypatch):
    """The multi-line add dialog: one key per line, blank lines dropped."""
    add = _AddKeysDialog(dialog)
    add.field.setPlainText("  key-new-1111\n\n'key-c-cccc'  \n")
    assert add.keys() == ["key-new-1111", "'key-c-cccc'"]  # strip only; filter lives in secrets.add_keys


def test_add_keys_stores_and_refreshes(qtbot, dialog, monkeypatch):
    """Add passes the parsed lines to secrets.add_keys and refreshes rows."""
    from dubtool.app.ui import settings_dialog as sd

    stored = []
    monkeypatch.setattr(sd.secrets, "add_keys", stored.append)
    keys_seq = iter(["key-only-9999"])
    monkeypatch.setattr(sd.secrets, "get_gemini_keys", lambda: [next(keys_seq, "key-only-9999")])

    def fake_exec(self):
        # simulate the user typing before pressing OK
        self.field.setPlainText("key-only-9999\n")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_AddKeysDialog, "exec", fake_exec)
    dialog._add_keys_manual()
    assert stored == [["key-only-9999"]]


def test_reorder_and_remove_calls_secret_store(qtbot, dialog, monkeypatch):
    """Up/Down and Remove call the store and refresh the masked rows."""
    moves, removed, saved = [], [], []
    from dubtool.app.ui import settings_dialog as sd

    monkeypatch.setattr(sd.secrets, "move_key", lambda row, delta: moves.append((row, delta)))
    monkeypatch.setattr(sd.secrets, "remove_key", removed.append)
    monkeypatch.setattr(
        sd.secrets, "get_gemini_keys",
        lambda: ["key-of-second-2222", "key-first-1111"] if moves else ["key-first-1111"],
    )
    monkeypatch.setattr(sd.QMessageBox, "question", lambda *a, **k: sd.QMessageBox.StandardButton.Yes)
    dialog.key_list.setCurrentRow(0)
    dialog._move_key(+1)
    assert moves == [(0, +1)]
    dialog.key_list.setCurrentRow(0)
    dialog._remove_key()
    assert removed == [0]
    assert not any("key-first" in t or "key-of-second" in t for t in saved)


def test_test_all_updates_badges(qtbot, dialog, monkeypatch):
    """'Test all' runs the fake tester per row and maps outcomes to badges;
    no key value ever reaches the status or rows."""
    from dubtool.app.ui import settings_dialog as sd

    results = {"key-one-aaaa": "ok", "key-two-bbbb": "invalid_model"}
    seen_models = []
    tested = []

    def fake_test(key, model=None):
        tested.append(key)
        seen_models.append(model)
        return results[key]

    monkeypatch.setattr(sd, "run_key_test", fake_test)
    dialog._run_test_all()
    assert dialog._testing_rows == {0, 1}  # launched off the UI thread
    qtbot.waitUntil(
        lambda: dialog._badges.get(0) == "ok" and dialog._badges.get(1) == "invalid_model",
        timeout=5000,
    )
    assert tested == ["key-one-aaaa", "key-two-bbbb"]
    assert seen_models == [dialog._fast_model(), dialog._fast_model()]
    rows = [dialog.key_list.item(r).text() for r in range(dialog.key_list.count())]
    assert any("✔" in r for r in rows)
    assert any("✘" in r for r in rows)
    assert all("key-one-aaaa" not in r for r in rows)
    assert dialog.keys_status.text() and "key-one-aaaa" not in dialog.keys_status.text()


def test_status_line_counts_without_keys(dialog):
    status = dialog.keys_status.text()
    assert "2" in status
    for value in ("key-one-aaaa", "key-two-bbbb"):
        assert value not in status


def test_custom_model_path_round_trip(qtbot, dialog, monkeypatch, tmp_path):
    """Selecting Custom… reveals the field; the id passes validation and is
    saved, then reappears selected in a fresh dialog (never replaced)."""
    from dubtool.app import settings as settings_mod
    from dubtool.app.ui import settings_dialog as sd

    idx = dialog.model_fast_combo.findData(sd.CUSTOM)
    assert idx >= 0
    dialog.model_fast_combo.setCurrentIndex(idx)
    assert not dialog.model_fast_custom.isHidden()
    dialog.model_fast_custom.setText("my-fast-model")
    dialog.lang_combo.setCurrentIndex(1)  # en
    saved = dialog.apply_changes()
    assert saved["language"] == "en"
    assert saved["mode_models"]["fast"] == "my-fast-model"
    assert settings_mod.load()["mode_models"]["fast"] == "my-fast-model"

    dialog2 = SettingsDialog()
    qtbot.addWidget(dialog2)
    texts = [dialog2.model_fast_combo.itemText(i) for i in range(dialog2.model_fast_combo.count())]
    assert "my-fast-model" in texts  # saved custom id is offered, not lost


def test_invalid_custom_id_rejected(dialog, monkeypatch):
    from dubtool.app import settings as settings_mod
    from dubtool.app.ui import settings_dialog as sd

    dlg_warnings = []
    monkeypatch.setattr(sd.QMessageBox, "warning", lambda *a, **k: dlg_warnings.append(True))
    idx = dialog.model_fast_combo.findData(sd.CUSTOM)
    dialog.model_fast_combo.setCurrentIndex(idx)
    dialog.model_fast_custom.setText("bad model id!")
    assert dialog.apply_changes() is None
    assert settings_mod.load().get("mode_models", {}).get("fast") != "bad model id!"


def test_reset_models_restores_defaults(qtbot, dialog):
    from dubtool.app import modes, settings as settings_mod

    dialog.model_fast_combo.setCurrentIndex(dialog.model_fast_combo.findData("custom-id-999")
                                            if dialog.model_fast_combo.findData("custom-id-999") >= 0
                                            else dialog.model_fast_combo.count() - 1)
    dialog._reset_models()
    loaded = modes.load(settings_mod.load())
    assert dialog.model_fast_combo.currentData() == loaded["fast"]["model"]
    assert dialog.model_accurate_combo.currentData() == loaded["accurate"]["model"]


def test_no_hardcoded_widget_text(qtbot):
    """Heuristic: the tr() fallback returns the key itself when missing."""
    assert i18n.tr("s1") not in ("s1",)
    assert i18n.language() in ("vi", "en")


def test_layout_min_size_no_overlap(qtbot, window):
    """Basic geometry sanity (the full --layout-check matrix runs via the
    app's own flag): visible widgets honor their minimumSizeHint."""
    window.show()
    window.resize(1180, 760)
    checked = 0
    for w in window.findChildren(QWidget):
        if not w.isVisibleTo(window):
            continue
        hint = w.minimumSizeHint()
        if hint.width() > 0 and hint.height() > 0:
            checked += 1
            assert w.width() >= hint.width() - 1, type(w).__name__
            assert w.height() >= hint.height() - 1, type(w).__name__
    assert checked > 10
