"""Settings dialog — tabbed (AGENT_UPDATE_5.md 2.2).

Tabs: API keys / Models / General / Advanced, OK/Cancel outside the tabs.
The API-keys tab lists the stored keys in priority order with masked values
(`Khóa 1  ••••ab12`) and per-row test badges; add/remove/reorder/test run
against `app.secrets` (one ordered Credential Manager entry). Keys are
NEVER displayed after saving and never written to any file; positions only.

Model dropdowns (task 1): non-editable per-mode combos, a Custom… item that
reveals a text field, "Refresh list" that asks the API (first working key)
off the UI thread, and Reset to the shipped defaults. The key hint
(AGENT_UPDATE_5.md task 3) was removed from this dialog and from i18n.
"""

from __future__ import annotations

import logging
import re
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dubtool.app import i18n, modes, secrets, settings
from dubtool.app.key_test import run_key_test


def _asr_progress() -> dict:
    """The last stage-2 measurement for the whisper-model tooltip (AGU8 7);
    imported lazily so the dialog loads on a machine without audio files."""
    from dubtool.app import asr_stats

    return asr_stats.last()

log = logging.getLogger("dubtool")

VOICE_KEYS = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")

CUSTOM = "__custom__"  # data value of the "Khác…/Custom…" dropdown item
MODEL_ID_RE = re.compile(r"[A-Za-z0-9._/\-]{1,128}")

# Types of test outcome -> i18n keys (AGU5 2.3: every outcome is mapped,
# including `invalid_model`, which earlier fell through to "unknown").
OUTCOME_KEYS = {
    "ok": "settings.key_ok",
    "invalid": "settings.key_invalid",
    "network": "settings.network",
    "quota": "settings.quota",
    "invalid_model": "settings.invalid_model",
    "unknown": "settings.error_unk",
}
BADGE_KEYS = {
    "ok": "settings.badge_valid",
    "invalid": "settings.badge_invalid",
    "network": "settings.badge_pending",
    "quota": "settings.badge_limited",
    "invalid_model": "settings.badge_invalid",
    "unknown": "settings.badge_pending",
}
BADGE_WORDS = {
    "ok": "✔",
    "invalid": "✘",
    "invalid_model": "✘",
    "quota": "⏳",
}

# Shipped fallback when modes.yaml is missing from the bundle (same ids).
_SHIPPED = {
    "fast": ("gemini-3.5-flash-lite", ["gemini-3.5-flash-lite", "gemini-3.6-flash"]),
    "accurate": ("gemini-3.8-flash", ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-argon"]),
}


def _fetch_models(key: str) -> list[str]:
    """Text-generation model ids the API offers to this key (no secrets)."""
    from dubtool.llm.gemini import build_gemini_client

    client = build_gemini_client(key, timeout_ms=15_000)
    ids: list[str] = []
    for model in client.models.list():
        actions = list(getattr(model, "supported_actions", None) or [])
        if actions and "generateContent" not in actions:
            continue
        name = str(getattr(model, "name", "") or "")
        if name.startswith("models/"):
            name = name[len("models/"):]
        if name:
            ids.append(name)
    return ids


class _WorkResult(QObject):
    """Emits a callable's result via a Qt signal (thread-safe from Python)."""

    finished = Signal(object)

    def fire(self, fn) -> None:
        def worker() -> None:
            try:
                self.finished.emit(fn())
            except Exception as exc:  # error is the result, never a crash
                self.finished.emit(("__error__", exc))

        threading.Thread(target=worker, daemon=True, name="dubtool-settings").start()


class _AddKeysDialog(QDialog):
    """Multi-line add: one key per line, cleared after saving (AGU5 2.2)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("settings.add_title"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(i18n.tr("settings.add_label")))
        self.field = QPlainTextEdit(self)
        self.field.setPlaceholderText("AIza…\nAQ.….…" )
        layout.addWidget(self.field)
        row = QHBoxLayout()
        row.addStretch(1)
        self.ok_button = QPushButton(i18n.tr("settings.add"))
        self.ok_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        row.addWidget(self.ok_button)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)

    def keys(self) -> list[str]:
        """One key per line, blanks dropped, trimmed (duplicates and quote
        stripping live in secrets.add_keys)."""
        text = self.field.toPlainText()
        return [line.strip() for line in text.splitlines() if line.strip()]

    def take_keys(self) -> list[str]:
        """AGENT_UPDATE_7.md 2.1: hand the keys to the caller BEFORE anything
        is cleared — the old done() override wiped the field when exec()
        finished, so the caller read an empty list and 'Add' did nothing.

        Always clears afterwards (the field never holds key text longer
        than needed); the caller discards the result on reject."""
        keys = self.keys()
        self.field.clear()
        return keys


class SettingsDialog(QDialog):
    def __init__(self, parent=None, on_key_saved=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("app.settings"))
        # No explicit global minimum width: the dialog's window minimum is
        # the tab content's own minimum, so it can never be opened narrower
        # than its buttons fit (AGENT_UPDATE_5.md task 4 layout invariant).
        self._on_key_saved = on_key_saved
        self._badges: dict[int, str] = {}   # row index -> outcome string
        self._refreshing = False
        self._testing_rows: set[int] = set()
        self._custom_kept: dict[str, str] = {}  # saved custom ids per mode

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_keys_tab(), i18n.tr("settings.tab_keys"))
        self.tabs.addTab(self._build_models_tab(), i18n.tr("settings.tab_models"))
        self.tabs.addTab(self._build_general_tab(), i18n.tr("settings.tab_general"))
        self.tabs.addTab(self._build_advanced_tab(), i18n.tr("settings.tab_advanced"))

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok = QPushButton("OK")
        ok.setDefault(True)
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(ok)
        btn_row.addWidget(cancel)
        layout.addLayout(btn_row)

        self._refresh_rows()
        self._load()
        self._on_key_saved = on_key_saved

    def _reject(self) -> None:
        self.reject()

    # ------------------------------------------------------------- keys tab
    def _build_keys_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.key_list = QListWidget()
        self.key_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.key_list, 1)
        # AGENT_UPDATE_7.md 2.2: the button states follow the selection live
        # (the old code recomputed them only after a rebuild, when no row
        # was selected — Remove/Up/Down/Test never became clickable).
        self.key_list.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.key_list.itemSelectionChanged.connect(lambda: self._update_buttons())
        btns = QHBoxLayout()
        self.test_all_btn = QPushButton(i18n.tr("settings.test_all"))
        self.test_one_btn = QPushButton(i18n.tr("settings.test_selected"))
        self.add_btn = QPushButton(i18n.tr("settings.add_keys"))
        self.up_btn = QPushButton(i18n.tr("settings.move_up"))
        self.down_btn = QPushButton(i18n.tr("settings.move_down"))
        self.remove_btn = QPushButton(i18n.tr("settings.remove"))
        for b in (self.add_btn, self.up_btn, self.down_btn, self.remove_btn, self.test_one_btn, self.test_all_btn):
            btns.addWidget(b)
        layout.addLayout(btns)

        self.add_btn.clicked.connect(self._add_keys_manual)
        self.remove_btn.clicked.connect(self._remove_key)
        self.up_btn.clicked.connect(lambda: self._move_key(-1))
        self.down_btn.clicked.connect(lambda: self._move_key(+1))
        # AGENT_UPDATE_7.md 2.2: explicit slots instead of the self.sender()
        # trick — "Test selected" tests only the selected key.
        self.test_one_btn.clicked.connect(self._test_selected)
        self.test_all_btn.clicked.connect(self._test_all)
        self._work = _WorkResult(self)
        self._work.finished.connect(self._on_test_finished)

        self.keys_status = QLabel("")
        self.keys_status.setWordWrap(True)
        layout.addWidget(self.keys_status)
        return page

    def _quota_until(self, key: str) -> float | None:
        """Persisted per-(key, accurate-model) daily-quota cutoff, if any
        (AGENT_UPDATE_6.md 5.3). Fingerprints only — never the key value."""
        try:
            from dubtool.llm import quota_state

            return quota_state.exhausted_until(key, self._accurate_model())
        except Exception:
            return None

    def _accurate_model(self) -> str:
        try:
            from dubtool.app import modes as modes_mod

            return str(modes_mod.get("accurate", {}).get("model"))
        except Exception:
            return _SHIPPED["accurate"][0]

    def _refresh_rows(self, select: int | None = None) -> None:
        """Rebuild the masked rows (never shows key values; position + ••••).

        `select` is the row to restore after the rebuild (AGENT_UPDATE_7.md
        2.2): after a move, the moved row; after a remove, the nearest
        remaining row; after an add, the new last key. None keeps row 0
        selected when keys exist and nothing was selected."""
        import time as _time

        count_before = self.key_list.count()
        had_selection = self.key_list.currentRow()
        self.key_list.clear()
        for i, key in enumerate(self._keys()):
            badge = self._badges.get(i)
            label = f"Khóa {i + 1}   ••••{(key[-4:] if len(key) >= 4 else '····')}"
            if badge == "__testing__":
                label += f"  —  {i18n.tr('settings.testing')}"
            elif badge in BADGE_KEYS:
                mark = BADGE_WORDS.get(badge, "")
                label += f"  —  {mark}{i18n.tr(BADGE_KEYS[badge])}"
            until = self._quota_until(key)
            item = QListWidgetItem(label)
            if until is not None and until > _time.time():
                # AGENT_UPDATE_6.md 5.3: badge shows when the day quota for
                # the Accurate model frees up; the tooltip names the model.
                label += "  —  ⏳ " + i18n.tr(
                    "settings.badge_quota_until",
                    time=_time.strftime("%H:%M", _time.localtime(until)),
                )
                item = QListWidgetItem(label)
                item.setToolTip(i18n.tr("settings.badge_quota_until_tooltip", model=self._accurate_model()))
            self.key_list.addItem(item)
        count = len(self._keys())
        self.keys_status.setText(
            i18n.tr("settings.keys_count", count=count) if count else i18n.tr("mode.no_key")
        )
        # Restore the selection: explicit row, or the previously selected
        # row across badge rebuilds, or row 1 (the first) on first show.
        n_rows = self.key_list.count()
        if n_rows:
            target = min(max(0, select if select is not None else (had_selection or 0)), n_rows - 1)
            self.key_list.setCurrentRow(target)
        self._update_buttons()

    def _update_buttons(self) -> None:
        """AGENT_UPDATE_7.md 2.2 button states, from the live selection."""
        rows = self.key_list.count()
        selected = self.key_list.currentRow()
        self.add_btn.setEnabled(True)  # always enabled
        has_sel = 0 <= selected < rows
        self.remove_btn.setEnabled(has_sel)
        self.test_one_btn.setEnabled(has_sel and not self._testing_rows)
        self.up_btn.setEnabled(has_sel and selected > 0)
        self.down_btn.setEnabled(has_sel and selected < rows - 1)
        self.test_all_btn.setEnabled(rows > 0 and not self._testing_rows)

    def _show_store_error(self, exc: Exception) -> None:
        """AGENT_UPDATE_7.md 2.3: a credential-store failure (backend missing
        or locked, cap reached) is never silent — status line + log."""
        log.error("keys tab: secret store failed: %s", exc, exc_info=True)
        self.keys_status.setText(
            i18n.tr("settings.store_error", detail=str(exc) or type(exc).__name__)
        )

    def _keys(self) -> list[str]:
        try:
            return secrets.get_gemini_keys()
        except Exception as exc:
            self._show_store_error(exc)
            return []

    def _add_keys_manual(self) -> None:
        dialog = _AddKeysDialog(self)
        code = dialog.exec()
        # AGENT_UPDATE_7.md 2.1: take the keys first (and clear the field)
        # whatever the outcome, so key text never lingers in the widget.
        new_keys = dialog.take_keys()
        if code != QDialog.DialogCode.Accepted or not new_keys:
            return
        try:
            final = secrets.add_keys(new_keys)
        except Exception as exc:
            self._show_store_error(exc)
            return
        self._badges.clear()
        # Select the new last key (AGENT_UPDATE_7.md 2.2).
        count = len(final) if isinstance(final, (list, tuple)) else len(self._keys())
        self._refresh_rows(select=max(0, count - 1))
        self.keys_status.setText(i18n.tr("settings.key_saved"))
        if self._on_key_saved:
            self._on_key_saved()

    def _remove_key(self) -> None:
        row = self.key_list.currentRow()
        if row < 0:
            return
        answer = QMessageBox.question(
            self, i18n.tr("app.settings"),
            i18n.tr("settings.confirm_remove", n=row + 1),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            remaining = secrets.remove_key(row)
        except Exception as exc:
            self._show_store_error(exc)
            return
        self._badges.pop(row, None)
        # Select the nearest remaining row (AGENT_UPDATE_7.md 2.2).
        n_after = len(remaining) if isinstance(remaining, (list, tuple)) else len(self._keys())
        self._refresh_rows(select=min(row, max(0, n_after - 1)))

    def _move_key(self, delta: int) -> None:
        row = self.key_list.currentRow()
        if row < 0:
            return
        target = row + delta
        try:
            secrets.move_key(row, delta)
        except Exception as exc:
            self._show_store_error(exc)
        self._badges.clear()
        self._refresh_rows(select=target)  # the moved row keeps the selection

    def _run_test_all(self) -> None:
        """Test one key or all of them, one after another, off the UI thread."""
    def _test_all(self) -> None:
        """Test every key (AGENT_UPDATE_7.md 2.2: explicit slot)."""
        keys = self._keys()
        if not keys or self._testing_rows:
            return
        self._start_test(list(range(len(keys))))

    def _test_selected(self) -> None:
        """Test only the selected key (AGENT_UPDATE_7.md 2.2: explicit slot)."""
        keys = self._keys()
        row = self.key_list.currentRow()
        if not keys or self._testing_rows or not (0 <= row < len(keys)):
            return
        self._start_test([row])

    def _start_test(self, rows: list[int]) -> None:
        keys = self._keys()
        self._testing_rows = set(rows)
        for row in rows:
            self._badges[row] = "__testing__"
        self._refresh_rows()
        self._work.fire(lambda: [
            (row, run_key_test(keys[row], model=self._fast_model())) for row in rows
        ])

    def _on_test_finished(self, payload) -> None:
        if (
            isinstance(payload, tuple)
            and len(payload) == 2
            and payload[0] == "__error__"
        ):
            self._testing_rows.clear()
            self._refresh_rows()
            self.keys_status.setText(i18n.tr("settings.error_unk"))
            return
        for row, outcome in payload:
            self._badges[row] = outcome
        self._testing_rows.clear()
        self._refresh_rows()

    def _fast_model(self) -> str:
        """The key test uses the model selected for Fast (AGU5 2.3)."""
        try:
            return str(modes.load(settings.load())["fast"]["model"])
        except Exception:
            return "gemini-3.5-flash-lite"

    # ----------------------------------------------------------- models tab
    def _build_models_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.model_fast_combo = QComboBox()
        self.model_accurate_combo = QComboBox()
        for combo in (self.model_fast_combo, self.model_accurate_combo):
            combo.setEditable(False)
        form.addRow(i18n.tr("settings.model_fast"), self.model_fast_combo)
        form.addRow(i18n.tr("settings.model_accurate"), self.model_accurate_combo)
        self.model_fast_custom = QLineEdit()
        self.model_accurate_custom = QLineEdit()
        form.addRow(i18n.tr("settings.custom_prompt"), self.model_fast_custom)
        self.model_fast_custom.setVisible(False)
        self.model_accurate_custom.setVisible(False)
        row = QHBoxLayout()
        self.refresh_btn = QPushButton(i18n.tr("settings.refresh_models"))
        self.reset_btn = QPushButton(i18n.tr("settings.reset_models"))
        row.addWidget(self.refresh_btn)
        row.addWidget(self.reset_btn)
        form.addRow("", row)
        self.models_status = QLabel("")
        self.models_status.setWordWrap(True)
        form.addRow("", self.models_status)

        self.model_fast_combo.currentIndexChanged.connect(self._update_custom_fields)
        self.model_accurate_combo.currentIndexChanged.connect(self._update_custom_fields)
        self._refresh_work = _WorkResult(self)
        self._refresh_work.finished.connect(self._on_models_fetched)
        self.refresh_btn.clicked.connect(self._refresh_models)
        self.reset_btn.clicked.connect(self._reset_models)

        self._fill_model_combos()
        return page

    def _fill_model_combos(self) -> None:
        s = settings.load()
        current = modes.load(s)
        known = list(s.get("known_models") or [])
        for mode, combo in (
            ("fast", self.model_fast_combo),
            ("accurate", self.model_accurate_combo),
        ):
            shipped = list(current[mode].get("models") or _SHIPPED[mode][1])
            items = list(dict.fromkeys(shipped + [m for m in known if m not in shipped]))
            current_id = str(current[mode].get("model") or "")
            if current_id and current_id not in items:
                items.append(current_id)  # never silently replace a saved id
            combo.blockSignals(True)
            combo.clear()
            for m in items:
                combo.addItem(m, m)
            combo.addItem(i18n.tr("settings.custom_model"), CUSTOM)
            if current_id and current_id in items:
                combo.setCurrentIndex(items.index(current_id))
            else:
                combo.setCurrentIndex(max(0, combo.findData(CUSTOM)))
                self._custom_kept[mode] = current_id  # prefill the field below
            combo.blockSignals(False)
        self._update_custom_fields()

    def _update_custom_fields(self) -> None:
        for mode, combo, field in (
            ("fast", self.model_fast_combo, self.model_fast_custom),
            ("accurate", self.model_accurate_combo, self.model_accurate_custom),
        ):
            is_custom = combo.itemData(combo.currentIndex()) == CUSTOM
            field.setVisible(is_custom)
            if is_custom and not field.text().strip():
                field.setText(str(self._custom_kept.get(mode) or ""))

    def _selected_model(self, mode: str) -> str:
        combo = getattr(self, f"model_{mode}_combo")
        if combo.itemData(combo.currentIndex()) == CUSTOM:
            return self._custom_text(mode)
        return str(combo.currentData() or combo.currentText() or "")

    def _custom_text(self, mode: str) -> str:
        field = getattr(self, f"model_{mode}_custom")
        return field.text().strip()

    def _refresh_models(self) -> None:
        """Fetch the key's model list off the UI thread (never freezes)."""
        if self._refreshing:
            return
        keys = self._keys()
        if not keys:
            self.models_status.setText(i18n.tr("settings.refresh_none"))
            return
        self._refreshing = True
        self.refresh_btn.setEnabled(False)
        self.models_status.setText(i18n.tr("settings.refresh_started"))
        self._refresh_work.fire(lambda: _fetch_models(keys[0]))

    def _on_models_fetched(self, payload) -> None:
        self._refreshing = False
        self.refresh_btn.setEnabled(True)
        if (
            isinstance(payload, tuple)
            and len(payload) == 2
            and payload[0] == "__error__"
        ):
            self.models_status.setText(i18n.tr("settings.refresh_none"))
            return
        fetched = [str(m).strip() for m in payload if str(m).strip()]
        s = settings.load()
        known = list(s.get("known_models") or [])
        added = [m for m in fetched if m not in known and self._valid_id(m)]
        if not added:
            self.models_status.setText(i18n.tr("settings.refresh_none"))
            return
        for combo in (self.model_fast_combo, self.model_accurate_combo):
            combo.blockSignals(True)
            skip_labels = {combo.itemText(i) for i in range(combo.count())}
            for m in added:
                if m not in skip_labels:
                    combo.insertItem(combo.count() - 1, m, m)  # before Custom…
            combo.blockSignals(False)
        s["known_models"] = known + added  # ids only, never any secret
        settings.save(s)
        self.models_status.setText(i18n.tr("settings.refresh_added", n=len(added)))

    @staticmethod
    def _valid_id(model_id: str) -> bool:
        return bool(MODEL_ID_RE.fullmatch(model_id))

    def _reset_models(self) -> None:
        """Reset restores the shipped defaults for both dropdowns (AGU5 2)."""
        modes.reset_to_defaults()
        self._custom_kept = {}
        self.model_fast_custom.clear()
        self.model_accurate_custom.clear()
        self._fill_model_combos()

    # ---------------------------------------------------------- general tab
    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Tiếng Việt", "vi")
        self.lang_combo.addItem("English", "en")
        form.addRow(i18n.tr("settings.language"), self.lang_combo)
        self.voice_combo = QComboBox()
        for voice in VOICE_KEYS:
            self.voice_combo.addItem(voice, voice)
        form.addRow(i18n.tr("options.voice"), self.voice_combo)
        self.whisper_combo = QComboBox()
        # AGU8 task 7: base/small/medium/large-v3 with a one-line description
        # ("nhanh nhất", "cân bằng", …) baked into the item text.
        for size, desc_key in (("base", "settings.whisper_base"), ("small", "settings.whisper_small"), ("medium", "settings.whisper_medium"), ("large-v3", "settings.whisper_large")):
            self.whisper_combo.addItem(f"{size} — {i18n.tr(desc_key)}", size)
        # Last-run stage-2 real time + video length in the tooltip (AGU8 7).
        last = _asr_progress()
        if last:
            from dubtool.audio_utils import format_mmss

            self.whisper_combo.setToolTip(
                i18n.tr(
                    "settings.whisper_last_run",
                    seconds=format_mmss(last.get("s2_elapsed_s", 0.0)),
                    video=format_mmss(last.get("video_s", 0.0)),
                )
            )
        else:
            self.whisper_combo.setToolTip(i18n.tr("settings.whisper_size_hint"))
        form.addRow(i18n.tr("settings.whisper_model"), self.whisper_combo)
        self.device_combo = QComboBox()
        for dev in ("auto", "cpu", "cuda"):
            self.device_combo.addItem(dev, dev)
        form.addRow(i18n.tr("settings.whisper_device"), self.device_combo)
        # AGU8 6.3 fidelity preset: the meaning-vs-timing trade-off.
        self.priority_combo = QComboBox()
        for key in ("faithful", "balanced", "timing"):
            self.priority_combo.addItem(
                i18n.tr("settings.priority_" + key), key
            )
        self.priority_combo.setToolTip(i18n.tr("settings.priority_tooltip"))
        form.addRow(i18n.tr("settings.priority"), self.priority_combo)
        # AGU6 4.2: "Mức dùng CPU / CPU usage" — thread budget for Whisper
        # and the FFmpeg encode (low = 1/4 of cores, balanced = 1/2, max = all).
        self.cpu_combo = QComboBox()
        self.cpu_combo.addItem(i18n.tr("settings.cpu_low"), "low")
        self.cpu_combo.addItem(i18n.tr("settings.cpu_balanced"), "balanced")
        self.cpu_combo.addItem(i18n.tr("settings.cpu_maximum"), "maximum")
        form.addRow(i18n.tr("settings.cpu_usage"), self.cpu_combo)
        return page

    # --------------------------------------------------------- advanced tab
    def _build_advanced_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.jobs_edit = QLineEdit()
        form.addRow(i18n.tr("settings.jobs_dir"), self.jobs_edit)
        self.output_edit = QLineEdit()
        form.addRow(i18n.tr("settings.default_output"), self.output_edit)
        # AGU6 4.2: x264 preset for the burn encode (same CRF, speed/size).
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("veryfast", "veryfast")
        self.preset_combo.addItem("faster", "faster")
        self.preset_combo.addItem("medium", "medium")
        form.addRow(i18n.tr("settings.encode_preset"), self.preset_combo)
        # AGU6 6.7: keep old job folders for at most N days + a manual button.
        from PySide6.QtWidgets import QPushButton, QSpinBox

        self.jobs_days_spin = QSpinBox()
        self.jobs_days_spin.setRange(1, 365)
        form.addRow(i18n.tr("settings.jobs_days"), self.jobs_days_spin)
        self.clean_now_btn = QPushButton(i18n.tr("settings.clean_now"))
        self.clean_now_btn.clicked.connect(self._clean_now)
        form.addRow("", self.clean_now_btn)
        return page

    # --------------------------------------------------------------- state
    def _clean_now(self) -> None:
        """AGU6 6.7: show the jobs/models folder sizes, then prune."""
        from dubtool.app import housekeeping
        from dubtool.app.paths import jobs_dir as _jobs_dir, models_dir

        jd, md = _jobs_dir(), models_dir()
        jobs_size = housekeeping.tree_size(jd)
        models_size = housekeeping.tree_size(md)
        days = int(self.jobs_days_spin.value())
        # prune_jobs refuses running jobs (state.json) and anything modified
        # in the last 3 days, so a live run can never be deleted here.
        deleted, reclaimed = housekeeping.prune_jobs(jd, days=days)
        text = i18n.tr("settings.clean_now_report")
        text = text.replace("{jobs_size}", housekeeping.human(jobs_size))
        text = text.replace("{models_size}", housekeeping.human(models_size))
        text = text.replace("{n}", str(len(deleted)))
        text = text.replace("{size}", housekeeping.human(reclaimed))
        QMessageBox.information(self, i18n.tr("settings.clean_now_title"), text)

    def _load(self) -> None:
        s = settings.load()
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(s["language"])))
        self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(s["voice"])))
        self.whisper_combo.setCurrentIndex(max(0, self.whisper_combo.findData(s["whisper_model"])))
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(s["whisper_device"])))
        self.priority_combo.setCurrentIndex(max(0, self.priority_combo.findData(s.get("translate_priority", "balanced"))))
        self.cpu_combo.setCurrentIndex(max(0, self.cpu_combo.findData(s.get("cpu_usage", "balanced"))))
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(s.get("video_encode_preset", "veryfast"))))
        self.jobs_days_spin.setValue(int(s.get("jobs_max_days", 14) or 14))
        self.jobs_edit.setText(s.get("jobs_dir") or "")
        self.output_edit.setText(s.get("default_output_dir") or "")

    def apply_changes(self) -> dict | None:
        s = settings.load()
        s["language"] = self.lang_combo.currentData() or "vi"
        s["voice"] = self.voice_combo.currentData() or s["voice"]
        s["whisper_model"] = self.whisper_combo.currentData() or s["whisper_model"]
        s["whisper_device"] = self.device_combo.currentData() or s["whisper_device"]
        s["translate_priority"] = self.priority_combo.currentData() or "balanced"
        s["cpu_usage"] = self.cpu_combo.currentData() or "balanced"
        s["video_encode_preset"] = self.preset_combo.currentData() or "veryfast"
        s["jobs_max_days"] = int(self.jobs_days_spin.value())
        s["jobs_dir"] = self.jobs_edit.text().strip()
        s["default_output_dir"] = self.output_edit.text().strip()
        s.pop("default_output", None)  # old key is never written (AGU8 7.3)
        # Model ids: the Custom text field is validated; an empty custom id
        # keeps the previously saved one (nothing to revert to silently).
        fast = self._selected_model("fast")
        accurate = self._selected_model("accurate")
        if fast:
            s.setdefault("mode_models", {})["fast"] = fast
        if accurate:
            s.setdefault("mode_models", {})["accurate"] = accurate
        problems = settings.validate(s)
        if problems:
            QMessageBox.warning(self, i18n.tr("app.settings"), "\n".join(problems))
            return None
        settings.save(s)
        i18n.set_language(s["language"])
        return s

    def accept(self) -> None:  # noqa: D102
        if self.apply_changes() is not None:
            super().accept()
