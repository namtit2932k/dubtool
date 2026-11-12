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
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QPalette
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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dubtool.app import i18n, secrets, settings
from dubtool.app.key_test import run_key_test

PROVIDERS_UI = (("gemini", "Gemini"), ("openai", "OpenAI (ChatGPT)"),
                ("anthropic", "Anthropic (Claude)"))

# Editable widget types the read-only sweep disables (Update 9 task 2.2).
_READONLY_TYPES = (QComboBox, QCheckBox, QLineEdit, QSpinBox, QPlainTextEdit, QListWidget)
# Buttons that stay enabled while read-only: the key-network checks do not
# affect a running pipeline; Close aborts the dialog without applying.
_READ_ONLY_OK_BUTTONS = ("test_all_btn", "test_one_btn", "close_btn")


def _asr_progress() -> dict:
    """The last stage-2 measurement for the whisper-model tooltip (AGU8 7);
    imported lazily so the dialog loads on a machine without audio files."""
    from dubtool.app import asr_stats

    return asr_stats.last()

log = logging.getLogger("dubtool")

VOICE_KEYS = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")

CUSTOM = "__custom__"  # data value of the old Custom… dropdown item (kept for ids)
MODEL_ID_RE = re.compile(r"[A-Za-z0-9._/\-]{1,128}")
ENV_ROW_ROLE = "env"  # Qt UserRole role data marking a read-only .env row

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

def _fetch_models(provider: str, key: str) -> list[str]:
    """Model ids the provider's list endpoint offers to this key (no
    secret appears anywhere; no tokens are billed)."""
    pid = (provider or "gemini").lower()
    if pid == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=key, timeout=15.0, max_retries=0)
        return sorted(str(m.id) for m in client.models.list().data if m.id)
    if pid == "anthropic":
        from anthropic import Anthropic

        client = Anthropic(api_key=key, timeout=15.0, max_retries=0)
        return sorted(str(m.id) for m in client.models.list().data if m.id)
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
    """Multi-line add: one key per line, cleared after saving (AGU5 2.2).
    Provider-aware: a pasted key whose prefix does not match the selected
    provider warns and offers to switch (`sk-ant-` is checked before `sk-`
    — anthropic keys also start `sk-`)."""

    def __init__(self, parent=None, provider: str = "gemini"):
        super().__init__(parent)
        self.provider = (provider or "gemini").lower()
        self.setWindowTitle(i18n.tr("settings.add_title"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(i18n.tr("settings.add_label")))
        self.field = QPlainTextEdit(self)
        self.field.setPlaceholderText(
            "AIza…" if self.provider == "gemini"
            else ("sk-ant-…" if self.provider == "anthropic" else "sk-proj-…")
        )
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

    def mismatched_keys(self) -> tuple[str, int] | None:
        """(detected provider, count) when keys belong to another provider."""
        counted: dict[str, int] = {}
        for key in self.keys():
            detected = secrets.detect_provider(key)
            if detected and detected != self.provider:
                counted[detected] = counted.get(detected, 0) + 1
        if not counted:
            return None
        pid, count = max(counted.items(), key=lambda item: item[1])
        return pid, count

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
    def __init__(self, parent=None, on_key_saved=None, read_only=False,
                 busy_provider=None, initial_provider: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("app.settings"))
        # No explicit global minimum width: the dialog's window minimum is
        # the tab content's own minimum, so it can never be opened narrower
        # than its buttons fit (AGENT_UPDATE_5.md task 4 layout invariant).
        self._on_key_saved = on_key_saved
        self._badges: dict[tuple[str, int], str] = {}  # (provider, row) -> outcome
        self._refreshing = False
        self._testing_rows: set[int] = set()
        self._provider = "gemini"  # the keys tab's current provider

        layout = QVBoxLayout(self)
        # Update 9 task 2.2: the read-only banner during a running job.
        self.read_only_banner = QLabel(i18n.tr("settings.read_only_banner"))
        self.read_only_banner.setObjectName("read_only_banner")
        self.read_only_banner.setWordWrap(True)
        self.read_only_banner.setStyleSheet(
            "background: #f3d98a; color: #40300a; padding: 6px; border-radius: 4px;"
        )
        self.read_only_banner.setVisible(bool(read_only))
        layout.addWidget(self.read_only_banner)
        self._read_only_flag = bool(read_only)
        self._busy_provider = busy_provider
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_keys_tab(), i18n.tr("settings.tab_keys"))
        self.tabs.addTab(self._build_general_tab(), i18n.tr("settings.tab_general"))
        self.tabs.addTab(self._build_advanced_tab(), i18n.tr("settings.tab_advanced"))

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok = QPushButton("OK")
        ok.setObjectName("ok_btn")
        ok.setDefault(True)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("close_btn")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(ok)
        btn_row.addWidget(cancel)
        layout.addLayout(btn_row)
        self._ok_btn = ok

        self._apply_read_only()
        # Update 9 task 2.2: re-check while open — a run ending re-enables
        # the controls without the user having to reopen the dialog.
        self._ro_timer = QTimer(self)
        self._ro_timer.setInterval(400)
        self._ro_timer.timeout.connect(self._poll_read_only)
        self._ro_timer.start()

        if initial_provider:
            from dubtool.app.secrets import normalize_provider

            idx = self.provider_combo.findData(normalize_provider(initial_provider))
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
        self._refresh_rows()
        self._load()
        self._on_key_saved = on_key_saved

    # ------------------------------------------------- read-only (AGU9 2.2)
    def _apply_read_only(self) -> None:
        ro = self._read_only_flag
        self.read_only_banner.setVisible(ro)
        for tc in _READONLY_TYPES:
            for w in self.findChildren(tc):
                w.setEnabled(not ro)
        for b in self.findChildren(QPushButton):
            # Allowlist: key network checks (no effect on a running run)
            # and the Close button. OK would apply changes — it stays off.
            allow = b.objectName() in _READ_ONLY_OK_BUTTONS
            b.setEnabled(not ro or allow)

    def _poll_read_only(self) -> None:
        if self._busy_provider is None:
            return
        try:
            busy = bool(self._busy_provider())
        except Exception:
            return
        if busy != self._read_only_flag:
            self._read_only_flag = busy
            self._apply_read_only()

    def _reject(self) -> None:
        self.reject()

    # ------------------------------------------------------------- keys tab
    def _build_keys_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        # The provider selector: the key editor and the test below work on
        # whichever provider is chosen here (three stores, one list).
        self.provider_combo = QComboBox()
        for _pid, label in PROVIDERS_UI:
            self.provider_combo.addItem(label, _pid)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

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
        self.import_env_btn = QPushButton(i18n.tr("settings.env_import"))
        for b in (self.add_btn, self.up_btn, self.down_btn, self.remove_btn,
                  self.test_one_btn, self.test_all_btn, self.import_env_btn):
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
        self.import_env_btn.clicked.connect(self._import_env_row)
        self._work = _WorkResult(self)
        self._work.finished.connect(self._on_test_finished)
        self._refresh_work = _WorkResult(self)
        self._refresh_work.finished.connect(self._on_models_fetched)

        self.keys_status = QLabel("")
        self.keys_status.setWordWrap(True)
        layout.addWidget(self.keys_status)
        return page

    def _on_provider_changed(self) -> None:
        self._provider = str(self.provider_combo.currentData() or "gemini")
        self._testing_rows.clear()
        self._refresh_rows()

    def _quota_until(self, key: str) -> float | None:
        """Persisted per-(key, model) daily-quota cutoff, if any
        (AGENT_UPDATE_6.md 5.3). Fingerprints only — never the key value."""
        if self._provider != "gemini" or key.startswith("••••"):
            return None
        try:
            from dubtool.llm import quota_state

            return quota_state.exhausted_until(key, self._selected_model_id())
        except Exception:
            return None

    def _selected_model_id(self) -> str:
        try:
            saved = str((settings.load() or {}).get("model_id") or "")
        except Exception:
            saved = ""
        return saved or "gemini-3.5-flash-lite"

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
            badge = self._badges.get((self._provider, i)) or self._badges.get(i)
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
        # The read-only `.env` rows for this provider: a key the app did not
        # store (masked, position shown) with an import action.
        env_rows = secrets.env_key_sources(self._provider)
        for name, masked in env_rows:
            item = QListWidgetItem(i18n.tr("settings.env_row", name=name, masked=masked))
            item.setData(Qt.ItemDataRole.UserRole, "env")
            item.setToolTip(i18n.tr("settings.env_row_tip"))
            self.key_list.addItem(item)
        count = len(self._keys())
        status = (
            i18n.tr("settings.keys_count", count=count) if count
            else i18n.tr("settings.no_key_provider", provider=self._provider_ui_name())
        )
        if env_rows:
            status += "  ·  " + i18n.tr("settings.env_count", n=len(env_rows))
        self.keys_status.setText(status)
        # Restore the selection: explicit row, or the previously selected
        # row across badge rebuilds, or row 1 (the first) on first show.
        n_rows = self.key_list.count()
        if n_rows:
            target = min(max(0, select if select is not None else (had_selection or 0)), n_rows - 1)
            self.key_list.setCurrentRow(target)
            if select is None and had_selection is not None:
                self.key_list.setCurrentRow(min(had_selection, n_rows - 1))
        self._update_buttons()

    def _update_buttons(self) -> None:
        """AGENT_UPDATE_7.md 2.2 button states, from the live selection.
        While read-only (Update 9 task 2.2) every control stays disabled."""
        ro = getattr(self, "_read_only_flag", False)
        rows = self.key_list.count()
        selected = self.key_list.currentRow()
        self.add_btn.setEnabled(not ro)  # was: always enabled
        has_sel = self._row_is_key(selected)
        self.remove_btn.setEnabled(has_sel and not ro)
        # Update 9 task 2.2: the key network tests stay enabled read-only
        # (they only read the stored key and never touch the run).
        self.test_one_btn.setEnabled(has_sel and not self._testing_rows)
        self.up_btn.setEnabled(has_sel and selected > 0 and not ro)
        self.down_btn.setEnabled(has_sel and selected < self._stored_row_count() - 1 and not ro)
        self.test_all_btn.setEnabled(self._stored_row_count() > 0 and not self._testing_rows)
        # Import works only while an `.env` row is selected (and not busy).
        self.import_env_btn.setEnabled(
            not ro and self._row_is_env(selected) and not self._testing_rows
        )

    def _row_is_key(self, row: int) -> bool:
        return self._stored_row_count() > row >= 0

    def _row_is_env(self, row: int) -> bool:
        if not 0 <= row < self.key_list.count():
            return False
        item = self.key_list.item(row)
        return bool(item and item.data(Qt.ItemDataRole.UserRole) == ENV_ROW_ROLE)

    def _stored_row_count(self) -> int:
        return len(self._keys())

    def _provider_ui_name(self) -> str:
        return dict(PROVIDERS_UI).get(self._provider, self._provider)

    def _show_store_error(self, exc: Exception) -> None:
        """AGENT_UPDATE_7.md 2.3: a credential-store failure (backend missing
        or locked, cap reached) is never silent — status line + log."""
        log.error("keys tab: secret store failed: %s", exc, exc_info=True)
        self.keys_status.setText(
            i18n.tr("settings.store_error", detail=str(exc) or type(exc).__name__)
        )

    def _keys(self) -> list[str]:
        try:
            return secrets.get_keys(self._provider)
        except Exception as exc:
            self._show_store_error(exc)
            return []

    def _add_keys_manual(self) -> None:
        dialog = _AddKeysDialog(self, self._provider)
        code = dialog.exec()
        # AGENT_UPDATE_7.md 2.1: take the keys first (and clear the field)
        # whatever the outcome, so key text never lingers in the widget.
        new_keys = dialog.take_keys()
        if code != QDialog.DialogCode.Accepted or not new_keys:
            return
        mismatch = dialog.mismatched_keys()
        if mismatch is not None:
            other_pid, count = mismatch
            answer = QMessageBox.question(
                self, i18n.tr("app.settings"),
                i18n.tr(
                    "settings.add_wrong_provider",
                    n=count,
                    other=dict(PROVIDERS_UI).get(other_pid, other_pid),
                    mine=self._provider_ui_name(),
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                delta = PROVIDERS_UI.index(
                    next(p for p in PROVIDERS_UI if p[0] == other_pid)
                )
                self.provider_combo.setCurrentIndex(delta)
                return
        try:
            try:
                final = secrets.add_keys(self._provider, new_keys)
            except TypeError:
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

    def _import_env_row(self) -> None:
        """Copy the selected `.env` row's key into the Credential Manager
        store (the .env value itself stays; the user can remove it). The
        real value comes only from the environment — the list widget holds
        name and masked value, never key text."""
        import os

        row = self.key_list.currentRow()
        env_position = self._env_row_position(row)
        if env_position is None:
            return
        env_rows = secrets.env_key_sources(self._provider)
        if not 0 <= env_position < len(env_rows):
            return
        name = env_rows[env_position][0]
        value = secrets.clean_key(os.environ.get(name, ""))
        if not value or secrets.looks_like_placeholder(value):
            self.keys_status.setText(i18n.tr("settings.env_import_missing"))
            return
        try:
            try:
                secrets.add_keys(self._provider, [value])
            except TypeError:
                secrets.add_keys([value])
        except Exception as exc:
            self._show_store_error(exc)
            return
        self.keys_status.setText(i18n.tr("settings.env_import_ok", name=name))
        self._refresh_rows(select=self._stored_row_count() - 1)
        if self._on_key_saved:
            self._on_key_saved()

    def _env_row_position(self, row: int) -> int | None:
        """Index into secrets.env_key_sources() order for the row."""
        env_positions = [
            r for r in range(self.key_list.count()) if self._row_is_env(r)
        ]
        if row not in env_positions:
            return None
        return env_positions.index(row)

    def _remove_key(self) -> None:
        row = self.key_list.currentRow()
        if not self._row_is_key(row):
            return
        answer = QMessageBox.question(
            self, i18n.tr("app.settings"),
            i18n.tr("settings.confirm_remove", n=row + 1),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            try:
                remaining = secrets.remove_key(self._provider, row)
            except TypeError:
                remaining = secrets.remove_key(row)
        except Exception as exc:
            self._show_store_error(exc)
            return
        self._badges.pop((self._provider, row), None)
        self._badges.pop(row, None)
        # Select the nearest remaining row (AGENT_UPDATE_7.md 2.2).
        n_after = len(remaining) if isinstance(remaining, (list, tuple)) else len(self._keys())
        self._refresh_rows(select=min(row, max(0, n_after - 1)))

    def _move_key(self, delta: int) -> None:
        row = self.key_list.currentRow()
        if not self._row_is_key(row):
            return
        target = row + delta
        try:
            try:
                secrets.move_key(self._provider, row, delta)
            except TypeError:
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
            self._badges[(self._provider, row)] = "__testing__"
            self._badges[row] = "__testing__"
        self._refresh_rows()
        provider = self._provider
        self._work.fire(lambda: [
            (row, run_key_test(keys[row], model=self._selected_model_id()
                               if provider == "gemini" else None,
                               provider=provider))
            for row in rows
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
            self._badges[(self._provider, row)] = outcome
            self._badges[row] = outcome
        self._testing_rows.clear()
        self._refresh_rows()

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
        # AGU11 3.5: the backup voice — the languages' voices that actually
        # exist on this machine (Windows SAPI, piper models), or None.
        self.backup_combo = QComboBox()
        self.backup_combo.addItem(i18n.tr("settings.fallback_none"), "none")
        self._backup_winrt: list[tuple[str, str]] = []
        self._backup_sapi = self._backup_piper = []
        self._backup_choices: dict[str, str] = {"none": "none"}
        try:
            from dubtool.tts.fallback import detect_winrt_voices

            self._backup_winrt = detect_winrt_voices()
        except Exception:
            self._backup_winrt = []
        try:
            from dubtool.tts.fallback import detect_sapi_voices

            self._backup_sapi = detect_sapi_voices()
        except Exception:
            self._backup_sapi = []
        try:
            from dubtool.tts.fallback import PiperTTSProvider

            self._backup_piper = PiperTTSProvider.detect_piper_voices()
        except Exception:
            self._backup_piper = []
        for name, lang in self._backup_winrt:
            key = f"windows_winrt:{name}"
            tag = f"{lang}, trên máy" if lang else "trên máy"
            self.backup_combo.addItem(f"Windows — {name} ({tag})", key)
            self._backup_choices[key] = "none"
        for name in self._backup_sapi:
            key = f"windows_sapi:{name}"
            self.backup_combo.addItem(f"Windows SAPI — {name} (trên máy)", key)
            self._backup_choices[key] = "none"
        for path in self._backup_piper:
            key = f"piper:{path}"
            self.backup_combo.addItem(f"Piper — {Path(path).name} (trên máy)", key)
        form.addRow(i18n.tr("settings.fallback_voice"), self.backup_combo)
        # Update 12 task 3 item 4: a one-line warning when the chosen backup
        # uses the same Microsoft online service as the primary voice — it
        # may then fail for the same reason.
        self.backup_warn = QLabel(i18n.tr("settings.fallback_same_service"))
        self.backup_warn.setWordWrap(True)
        self.backup_warn.setStyleSheet("color: #e6b800;")
        self.backup_warn.setVisible(False)
        form.addRow("", self.backup_warn)
        self.backup_combo.currentIndexChanged.connect(self._on_backup_changed)

        # The translation-model group (one model, no Fast/Accurate modes):
        # the fallback model used when the selected model is overloaded or
        # out of quota (items = the models that have a key; “Không” = no
        # fallback), plus the overload policy dropdown and the refresh of
        # the model list from every provider that has a key.
        self.fallback_combo = QComboBox()
        self.fallback_combo.addItem(i18n.tr("settings.fallback_model_none"), "")
        from dubtool.llm import catalog as catalog_mod

        for _pid, entries in catalog_mod.grouped_order():
            if not entries:
                continue
            try:
                has_k = secrets.has_key(_pid)
            except TypeError:
                has_k = secrets.has_key()
            if not has_k:
                continue
            for entry in entries:
                suffix = i18n.tr("settings.model_paid") if entry.paid else ""
                self.fallback_combo.addItem(
                    f"{entry.label} ({_pid})" + (f" {suffix}" if suffix else ""),
                    entry.id,
                )
        for model_id in [m for m in (settings.load() or {}).get("known_models", [])
                         if isinstance(m, str) and not self.fallback_combo.findData(m) >= 0]:
            pid = catalog_mod.infer_provider(model_id)
            try:
                has_k = secrets.has_key(pid)
            except TypeError:
                has_k = secrets.has_key()
            if not has_k:
                continue
            self.fallback_combo.addItem(f"{model_id} ({pid})", model_id)
        self.fallback_combo.setToolTip(i18n.tr("settings.fallback_model_tip"))
        form.addRow(i18n.tr("settings.fallback_model"), self.fallback_combo)
        self.refresh_models_btn = QPushButton(i18n.tr("settings.refresh_models_btn"))
        self.refresh_models_btn.clicked.connect(self._refresh_provider_models)
        form.addRow("", self.refresh_models_btn)
        self.models_status = QLabel("")
        self.models_status.setWordWrap(True)
        form.addRow("", self.models_status)

        # Update 12 task 2.4: what happens when the selected model is
        # overloaded or out of quota for every key (disabled while a run is
        # busy through the dialog's read-only mode).
        self.overload_combo = QComboBox()
        self.overload_combo.addItem(i18n.tr("settings.overload_auto"), "fallback_fast")
        self.overload_combo.addItem(i18n.tr("settings.overload_ask"), "ask")
        self.overload_combo.addItem(i18n.tr("settings.overload_wait"), "keep_backoff")
        self.overload_combo.setToolTip(i18n.tr("settings.overload_tooltip"))
        form.addRow(i18n.tr("settings.overload_label"), self.overload_combo)
        self.backup_listen_btn = QPushButton(i18n.tr("settings.fallback_listen"))
        self.backup_listen_btn.clicked.connect(self._listen_backup)
        form.addRow("", self.backup_listen_btn)
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
        # AGU9 10.4: the punctuation-hint checkbox — present, not decided.
        # Default stays off; the tooltip states the measured trade-off.
        self.asr_prompt_check = QCheckBox(i18n.tr("settings.asr_prompt"))
        self.asr_prompt_check.setToolTip(i18n.tr("settings.asr_prompt_tooltip"))
        form.addRow(i18n.tr("settings.asr_prompt_label"), self.asr_prompt_check)
        return page

    def _refresh_provider_models(self) -> None:
        """Ask every provider that has a key for its model list (off the UI
        thread) and add the returned ids to the dropdowns (cached in
        settings.json as known_models — ids only, never another secret)."""
        if self._refreshing:
            return
        providers: list[str] = []
        keys: dict[str, str] = {}
        for pid, _label in PROVIDERS_UI:
            provider_keys = []
            try:
                provider_keys = secrets.get_keys(pid)
            except Exception:
                provider_keys = []
            if provider_keys:
                providers.append(pid)
                keys[pid] = provider_keys[0]
        if not providers:
            self.models_status.setText(i18n.tr("settings.refresh_none"))
            return
        self._refreshing = True
        self.refresh_models_btn.setEnabled(False)
        self.models_status.setText(i18n.tr("settings.refresh_started"))
        self._refresh_work.fire(lambda: [
            (pid, _fetch_models(pid, keys[pid])) for pid in providers
        ])

    def _on_models_fetched(self, payload) -> None:
        self._refreshing = False
        self.refresh_models_btn.setEnabled(True)
        if (
            isinstance(payload, tuple)
            and len(payload) == 2
            and payload[0] == "__error__"
        ):
            self.models_status.setText(i18n.tr("settings.refresh_none"))
            return
        s = settings.load()
        known = list(s.get("known_models") or [])
        added_total = 0
        from dubtool.llm import catalog as catalog_mod

        for pid, fetched in payload:
            if isinstance(fetched, tuple) and len(fetched) == 2 and fetched[0] == "__error__":
                continue
            for model_id in fetched:
                model_id = str(model_id).strip()
                if not model_id or not self._valid_id(model_id):
                    continue
                if model_id not in known:
                    known.append(model_id)
                    added_total += 1
                    if self.fallback_combo.findData(model_id) < 0 and secrets.has_key(pid):
                        self.fallback_combo.addItem(
                            f"{model_id} ({pid})", model_id
                        )
        if not added_total:
            self.models_status.setText(i18n.tr("settings.refresh_none"))
            return
        s["known_models"] = known  # ids only, never any secret
        settings.save(s)
        self.models_status.setText(i18n.tr("settings.refresh_added", n=added_total))

    # ------------------------------------------------ backup voice (AGU11 3.5)
    def _selected_backup(self) -> str:
        return str(self.backup_combo.currentData() or "none")

    def _listen_backup(self) -> None:
        """Nghe thử: synthesize one short sample with the chosen backup voice
        and play it through Qt's media backend (bundled, no extra window)."""
        choice = self._selected_backup()
        if choice == "none":
            QMessageBox.information(self, i18n.tr("settings.fallback_voice"),
                                    i18n.tr("settings.fallback_none"))
            return
        import tempfile

        from dubtool.tts.base import TTSFailure

        btn = self.backup_listen_btn
        btn.setEnabled(False)
        btn.setText("…")
        try:
            sample_dir = Path(tempfile.gettempdir()) / "dubtool-samples"
            sample_dir.mkdir(parents=True, exist_ok=True)
            out_path = sample_dir / "backup-sample.mp3"
            from dubtool.tts.fallback import build_backup_for_setting

            provider = build_backup_for_setting(choice)
            provider.synthesize(
                "Xin chào, đây là bản nghe thử của giọng đọc dự phòng.", "", out_path,
            )
            if not out_path.exists() or out_path.stat().st_size < 400:
                QMessageBox.warning(
                    self, i18n.tr("settings.fallback_listen"),
                    i18n.tr("settings.fallback_sample_failed", detail="no audio"),
                )
                return
            # AGU11: QtMultimedia is an addons-only module the exe does not
            # bundle; the sample opens in the OS default player instead.
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_path)))
        except TTSFailure as exc:
            QMessageBox.warning(
                self, i18n.tr("settings.fallback_listen"),
                i18n.tr("settings.fallback_sample_failed", detail=str(exc)),
            )
        except Exception as exc:  # a preview can never break the dialog
            QMessageBox.warning(
                self, i18n.tr("settings.fallback_listen"),
                i18n.tr("settings.fallback_sample_failed", detail=str(exc)),
            )
        finally:
            btn.setText(i18n.tr("settings.fallback_listen"))
            btn.setEnabled(True)

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
        # Update 9 task 6: the jobs folder and the cleanup button live in a
        # titled group with the plain explanation, the path, size, run count,
        # open-folder, the days spinner with its one-line hint, and cleanup.
        from PySide6.QtWidgets import QGroupBox, QPushButton, QSpinBox, QVBoxLayout

        group = QGroupBox(i18n.tr("adv.working_files_title"), self)
        gv = QVBoxLayout(group)
        info = QLabel(i18n.tr("adv.working_files_info"), group)
        info.setWordWrap(True)
        gv.addWidget(info)
        self.jobs_stats_label = QLabel("", group)
        self.jobs_stats_label.setWordWrap(True)
        gv.addWidget(self.jobs_stats_label)
        self.open_jobs_btn = QPushButton(i18n.tr("adv.open_jobs_dir"), group)
        self.open_jobs_btn.clicked.connect(self._open_jobs_dir)
        gv.addWidget(self.open_jobs_btn)
        form.addRow(group)

        self.jobs_days_spin = QSpinBox()
        self.jobs_days_spin.setRange(1, 365)
        form.addRow(i18n.tr("settings.jobs_days"), self.jobs_days_spin)
        days_hint = QLabel(i18n.tr("adv.jobs_days_hint"), page)
        days_hint.setWordWrap(True)
        form.addRow("", days_hint)
        self.jobs_days_hint_label = days_hint
        # AGU10 5.3: trim the jobs folder when it exceeds the cap.
        self.jobs_total_mb_spin = QSpinBox()
        self.jobs_total_mb_spin.setRange(200, 200000)
        self.jobs_total_mb_spin.setSuffix(" MB")
        form.addRow(i18n.tr("settings.jobs_total_mb"), self.jobs_total_mb_spin)
        self.clean_now_btn = QPushButton(i18n.tr("settings.clean_now"))
        self.clean_now_btn.clicked.connect(self._clean_now)
        form.addRow("", self.clean_now_btn)
        return page

    def _jobs_root(self):
        """The jobs folder from the dialog's current edit value, or the default."""
        from dubtool.app.paths import jobs_dir as _jobs_dir

        text = self.jobs_edit.text().strip()
        return Path(text) if text else _jobs_dir()

    def _refresh_jobs_stats(self) -> None:
        """Update the size/run-count line (first called in _load; also after
        a manual jobs-dir edit is applied)."""
        from dubtool.app import housekeeping

        count, size, _oldest = housekeeping.job_stats(self._jobs_root())
        self.jobs_stats_label.setText(i18n.tr(
            "adv.jobs_stats", count=count, size=housekeeping.human(size)
        ))

    def _open_jobs_dir(self) -> None:
        """Open the jobs root with the fixed (non-hidden) open path —
        os.startfile on Windows, xdg-open through proc(gui=True) elsewhere."""
        import sys

        target = self._jobs_root()
        if not target.is_dir():
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError:
                QMessageBox.warning(self, i18n.tr("settings.clean_now_title"),
                                    i18n.tr("progress.folder_missing"))
                return
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            from dubtool import proc

            proc.run(["xdg-open", str(target)], capture=False, gui=True)

    # --------------------------------------------------------------- state
    def _clean_now(self) -> None:
        """Update 9 task 6: first a confirmation listing what will be deleted
        (run count, total size, oldest date) and what never is; the user
        then chooses 'older than N days' (default) or 'delete all temporary
        files' (except the running run). The running-job check in
        housekeeping.prune_jobs stays in every path."""
        from dubtool.app import housekeeping
        from dubtool.app.paths import models_dir

        jd = self._jobs_root()
        md = models_dir()
        stored_count, stored_size, stored_oldest = housekeeping.job_stats(jd)
        models_size = housekeeping.tree_size(md)
        if stored_count == 0:
            QMessageBox.information(
                self, i18n.tr("settings.clean_now_title"),
                i18n.tr("adv.nothing_to_delete"),
            )
            return
        days = int(self.jobs_days_spin.value())
        # The numbers in the text are what the default button deletes:
        # a dry run of prune_jobs (Update 9 task 6 — the confirmation
        # must list what will be deleted, not the whole inventory). The
        # oldest date shown is the oldest run that the default pass removes.
        plan, plan_size = housekeeping.prune_plan(jd, days=days)
        plan_oldest = min((housekeeping._mtime(p) for p in plan), default=stored_oldest)
        oldest_text = (
            datetime.fromtimestamp(plan_oldest).strftime("%Y-%m-%d")
            if plan_oldest else "?"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(i18n.tr("settings.clean_now_title"))
        box.setText(i18n.tr(
            "adv.confirm_text",
            count=len(plan), size=housekeeping.human(plan_size),
            oldest=oldest_text,
        ))
        older_btn = box.addButton(
            i18n.tr("adv.choice_older", n=days),
            QMessageBox.ButtonRole.ActionRole,
        )
        all_btn = box.addButton(
            i18n.tr("adv.choice_all"),
            QMessageBox.ButtonRole.ActionRole,
        )
        box.addButton(i18n.tr("progress.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is older_btn:
            deleted, reclaimed = housekeeping.prune_jobs(jd, days=days)
        elif clicked is all_btn:
            deleted, reclaimed = housekeeping.prune_jobs(jd, days=0, all_jobs=True)
        else:
            return  # cancelled
        if not deleted:
            QMessageBox.information(
                self, i18n.tr("settings.clean_now_title"),
                i18n.tr("adv.nothing_to_delete"),
            )
        else:
            QMessageBox.information(
                self, i18n.tr("settings.clean_now_title"),
                i18n.tr(
                    "adv.clean_done", n=len(deleted),
                    size=housekeeping.human(reclaimed),
                    models_size=housekeeping.human(models_size),
                ),
            )
        self._refresh_jobs_stats()

    def _on_backup_changed(self) -> None:
        """Show the same-service warning when the chosen backup would fail
        for the same reason as the primary (Update 12 task 3 item 4)."""
        key = self._selected_backup()
        same = key.startswith("edge:") or key.startswith("edge_voice:")
        self.backup_warn.setVisible(bool(same))

    def _load(self) -> None:
        s = settings.load()
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(s["language"])))
        self.voice_combo.setCurrentIndex(max(0, self.voice_combo.findData(s["voice"])))
        self.whisper_combo.setCurrentIndex(max(0, self.whisper_combo.findData(s["whisper_model"])))
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findData(s["whisper_device"])))
        self.priority_combo.setCurrentIndex(max(0, self.priority_combo.findData(s.get("translate_priority", "balanced"))))
        self.cpu_combo.setCurrentIndex(max(0, self.cpu_combo.findData(s.get("cpu_usage", "balanced"))))
        self.asr_prompt_check.setChecked(bool(s.get("asr_punctuation_prompt", False)))
        self.overload_combo.setCurrentIndex(
            max(0, self.overload_combo.findData(s.get("overload_policy", "fallback_fast")))
        )
        saved_fallback = str(s.get("fallback_model") or "")
        idx_f = self.fallback_combo.findData(saved_fallback)
        if saved_fallback and idx_f < 0:
            # A saved fallback that is not in the list keeps its id (never
            # wait silently replaced).
            self.fallback_combo.addItem(f"{saved_fallback}", saved_fallback)
            idx_f = self.fallback_combo.findData(saved_fallback)
        self.fallback_combo.setCurrentIndex(max(0, idx_f))
        self._load_backup_choice(s)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(s.get("video_encode_preset", "veryfast"))))
        self.jobs_days_spin.setValue(int(s.get("jobs_max_days", 14) or 14))
        self.jobs_total_mb_spin.setValue(int(s.get("jobs_max_total_mb", 3000) or 3000))
        self.jobs_edit.setText(s.get("jobs_dir") or "")
        self.output_edit.setText(s.get("default_output_dir") or "")
        self._refresh_jobs_stats()

    def apply_changes(self) -> dict | None:
        s = settings.load()
        s["language"] = self.lang_combo.currentData() or "vi"
        s["voice"] = self.voice_combo.currentData() or s["voice"]
        s["whisper_model"] = self.whisper_combo.currentData() or s["whisper_model"]
        s["whisper_device"] = self.device_combo.currentData() or s["whisper_device"]
        s["translate_priority"] = self.priority_combo.currentData() or "balanced"
        s["cpu_usage"] = self.cpu_combo.currentData() or "balanced"
        s["asr_punctuation_prompt"] = bool(self.asr_prompt_check.isChecked())
        s["overload_policy"] = self.overload_combo.currentData() or "fallback_fast"
        s["fallback_model"] = str(self.fallback_combo.currentData() or "")
        s["tts_backup_voice"] = self._selected_backup()
        s["video_encode_preset"] = self.preset_combo.currentData() or "veryfast"
        s["jobs_max_days"] = int(self.jobs_days_spin.value())
        s["jobs_max_total_mb"] = int(self.jobs_total_mb_spin.value())
        s["jobs_dir"] = self.jobs_edit.text().strip()
        s["default_output_dir"] = self.output_edit.text().strip()
        s.pop("default_output", None)  # old key is never written (AGU8 7.3)
        # The saved fast/accurate mode models (mode_models, last_mode)
        # are ignored — the model list replaced the old modes entirely.
        problems = settings.validate(s)
        if problems:
            QMessageBox.warning(self, i18n.tr("app.settings"), "\n".join(problems))
            return None
        settings.save(s)
        i18n.set_language(s["language"])
        return s

    def _load_backup_choice(self, s: dict) -> None:
        """AGU11 3.5: restore the backup-voice choice; when the saved voice
        vanished from the machine, fall back to None (silent harm-free)."""
        saved = str(s.get("tts_backup_voice", "none") or "none")
        if self.backup_combo.findData(saved) < 0:
            saved = "none"
        self.backup_combo.setCurrentIndex(max(0, self.backup_combo.findData(saved)))

    def accept(self) -> None:  # noqa: D102
        # Update 9 task 2.2: a read-only dialog must never apply changes
        # (the OK button is disabled anyway; this is the second gate).
        if self._read_only_flag:
            return
        if self.apply_changes() is not None:
            super().accept()
