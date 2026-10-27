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
    QPushButton,
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
    """Truly collapsible log tail (AGU4 7.6, AGENT_UPDATE_5.md task 4):
    a toggle button with an arrow, collapsed by default, expanding to a
    fixed height with auto-scroll while lines arrive."""

    EXPANDED_HEIGHT = 140

    def __init__(self):
        super().__init__()
        self._expanded = False
        self.toggle = QPushButton("▸")
        self.toggle.setFlat(True)
        self.toggle.setFixedWidth(28)
        self.toggle.clicked.connect(self._toggle)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        # AGU6 4.3: a real 82-segment run can emit thousands of log lines;
        # cap the document so long runs cannot lag the UI (newest kept).
        self.text.setMaximumBlockCount(2000)
        self.text.setFixedHeight(self.EXPANDED_HEIGHT)
        self.text.setVisible(False)
        self.text.verticalScrollBar().rangeChanged.connect(self._auto_scroll)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.text)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.toggle.setText("▾" if self._expanded else "▸")
        self.text.setVisible(self._expanded)

    def _auto_scroll(self) -> None:
        sb = self.text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def append(self, line: str) -> None:
        self.text.appendPlainText(line)
        if self._expanded:
            self._auto_scroll()


class CounterFormat:
    """Formats e.g. 'Dịch: 3/5 đoạn' via i18n without literals in widgets."""

    @staticmethod
    def stage_counter(stage: str, done: int, total: int) -> str:
        return f"{i18n.tr(stage)}: {done}/{total}"
