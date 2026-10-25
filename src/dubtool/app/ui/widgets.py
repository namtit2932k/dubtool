"""Reusable widgets: drop zone, stage list, log view (AGU4 6.2).

No user-facing string literals here — all text comes from i18n.tr().
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dubtool.app import i18n

STAGES = ("s1", "s2", "s3", "s4", "s5", "s6", "s7")


class DropZone(QLabel):
    """Drag-and-drop or click to pick a video (AGU4 7.1)."""

    filesDropped = Signal(str)
    clicked = Signal()

    def __init__(self):
        super().__init__(i18n.tr("video.drop"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(90)
        self.setAcceptDrops(True)
        self.setStyleSheet("QLabel { border: 2px dashed #888; border-radius: 8px; padding: 16px; }")

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.filesDropped.emit(path)
                break

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()


class StageList(QListWidget):
    """The seven pipeline stages with state icons (AGU4 7.6)."""

    STATE_ICON = {"start": "▶", "progress": "…", "done": "✓", "error": "✗", "skipped": "→"}

    def __init__(self):
        super().__init__()
        for stage in STAGES:
            item = QListWidgetItem(self._label(stage))
            item.setData(Qt.ItemDataRole.UserRole, stage)
            self.addItem(item)
        self.setAllPending()
        # All seven stages visible at once; no scrolling (AGU4 7.6).
        self.setMinimumHeight(7 * self.sizeHintForRow(0) + 14)

    @staticmethod
    def _label(stage: str) -> str:
        base = i18n.tr(stage)
        return f"○ {base}"

    def setAllPending(self) -> None:
        for row in range(self.count()):
            item = self.item(row)
            item.setText(f"○ {i18n.tr(item.data(Qt.ItemDataRole.UserRole))}")

    def update_state(self, stage: str, state: str, done: int | None = None, total: int | None = None) -> None:
        label = i18n.tr(stage) if stage in STAGES else stage
        icon = self.STATE_ICON.get(state, "○")
        text = f"{icon} {label}"
        if state == "progress" and done is not None and total and total > 0:
            text = f"{icon} {label}: {done}/{total}"
        for row in range(self.count()):
            item = self.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == stage:
                item.setText(text)
                return


class LogView(QWidget):
    """Collapsible log tail (AGU4 7.6)."""

    def __init__(self):
        super().__init__()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumHeight(160)
        self.label = QLabel()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.text)

    def append(self, line: str) -> None:
        self.text.appendPlainText(line)


class CounterFormat:
    """Formats e.g. 'Dịch: 3/5 đoạn' via i18n without literals in widgets."""

    @staticmethod
    def stage_counter(stage: str, done: int, total: int) -> str:
        return f"{i18n.tr(stage)}: {done}/{total}"
