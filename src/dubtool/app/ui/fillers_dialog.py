"""AGU8 task 4 (5.3) and task 2 (3.4): app dialogs asked from the worker
thread but shown on the GUI thread.

- `FillersDialog`: one dialog before translation when the filler detector
  found speaker tics — per token Bỏ / Giữ / Dịch thành… and Apply /
  Use defaults this time. Returns a {token: action} dict ({} = defaults).
- `OverloadDialog`: the Gemini-overload policy question with the three
  buttons Thử lại sau / Dùng model Nhanh cho phần còn lại / Hủy.
- `DialogBridge`: marshals those calls onto the GUI thread and blocks the
  worker thread until the dialog returns (the pipeline runs on a plain
  thread and never touches widgets directly).
"""

from __future__ import annotations

from dubtool.app import i18n

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ACTION_DROP = "drop"
ACTION_KEEP = "keep"
ACTION_TRANSLATE = "translate_as"


class FillersDialog(QDialog):
    """AGU8 5.3: 'Phát hiện từ đệm: girl (14 lần), like (9 lần)…'."""

    def __init__(self, suggestions: list[dict], parent: QWidget | None = None,
                 defaults: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("DubTool")
        self.setMinimumWidth(520)
        defaults = defaults or {}
        summary = ", ".join(
            f"{s.get('token')} ({s.get('count')})" for s in (suggestions or [])[:6]
        )
        layout = QVBoxLayout(self)
        header = QLabel(i18n.tr("fillers.header", tokens=summary))
        header.setWordWrap(True)
        layout.addWidget(header)
        note = QLabel(i18n.tr("fillers.note"))
        note.setWordWrap(True)
        layout.addWidget(note)

        self._rows: list[tuple[str, QComboBox, QLineEdit]] = []
        for sug in suggestions or []:
            token = str(sug.get("token", ""))
            if not token:
                continue
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{token} ({sug.get('count')})"))
            combo = QComboBox()
            default_action = str(defaults.get(token.lower(), ACTION_DROP))
            combo.addItem(i18n.tr("fillers.action.drop"), ACTION_DROP)
            combo.addItem(i18n.tr("fillers.action.keep"), ACTION_KEEP)
            combo.addItem(i18n.tr("fillers.action.translate_as"), ACTION_TRANSLATE)
            if default_action == ACTION_KEEP:
                combo.setCurrentIndex(1)
            elif default_action.startswith(ACTION_TRANSLATE):
                combo.setCurrentIndex(2)
            target = QLineEdit()
            if default_action.startswith(ACTION_TRANSLATE):
                target.setText(default_action.split(":", 1)[-1].strip())
                target.show()
            else:
                target.hide()
            row.addWidget(combo, 1)
            row.addWidget(target, 1)
            holder = QWidget()
            holder.setLayout(row)
            layout.addWidget(holder)
            self._rows.append((token, combo, target))

        buttons = QDialogButtonBox()
        apply_btn = buttons.addButton(
            i18n.tr("fillers.button.apply"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        apply_btn.setDefault(True)
        buttons.addButton(
            i18n.tr("fillers.button.use_defaults"),
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_choices(self) -> dict:
        """"Áp dụng" -> the chosen actions; "Dùng mặc định lần này" -> {}."""
        if self.result() != QDialog.DialogCode.Accepted:
            return {}
        out: dict = {}
        for token, combo, target in self._rows:
            action = combo.currentData()
            if action == ACTION_TRANSLATE:
                text = target.text().strip()
                out[token.lower()] = f"translate_as: {text}" if text else ACTION_DROP
            else:
                out[token.lower()] = action
        return out


class OverloadDialog(QDialog):
    """AGU8 3.4: three buttons — Thử lại sau / Dùng model Nhanh / Hủy."""

    def __init__(self, model: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("DubTool")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        label = QLabel(i18n.tr("overload.header", model=model))
        label.setWordWrap(True)
        layout.addWidget(label)

        wait_btn = QPushButton(i18n.tr("overload.button.try_later"))
        fast_btn = QPushButton(i18n.tr("overload.button.fallback_fast", model=model))
        cancel_btn = QPushButton(i18n.tr("overload.button.cancel"))
        for btn in (wait_btn, fast_btn, cancel_btn):
            layout.addWidget(btn)
        wait_btn.clicked.connect(lambda: self._pick("wait"))
        fast_btn.clicked.connect(lambda: self._pick("fallback_fast"))
        cancel_btn.clicked.connect(lambda: self._pick("cancel"))
        self.choice = "wait"
        self._done = False

    def _pick(self, value: str) -> None:
        self.choice = value
        self._done = True
        self.accept()


class DialogBridge(QObject):
    """Runs a dialog opener callable on the object's thread — the GUI
    thread — and BLOCKS the calling (worker) thread until it finished.
    A direct call from the GUI thread just runs the callable."""

    _invoke = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._invoke.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

    def call(self, opener):
        """opener: () -> the dialog result. Never returns before the user."""
        if QThread.currentThread() is self.thread():
            return opener()
        box: list = []
        self._invoke.emit(lambda: box.append(opener()))
        return box[0] if box else None

    def _run(self, fn) -> None:
        fn()
