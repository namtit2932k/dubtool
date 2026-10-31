"""Reusable widgets: drop zone, file card, stage list, details log view.

No user-facing string literals here — all text comes from i18n.tr().
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
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


class ElidedLabel(QLabel):
    """A QLabel that paints its text elided in the middle.

    The full string is never set via setText (that would grow the layout
    minimum); the paintEvent elides against the *current* width, so the
    label can never push the X button of the file card out of view even
    at the narrowest window size.
    """

    def __init__(self, full_text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setElidedText(full_text)

    def setElidedText(self, full_text: str) -> None:  # noqa: N802
        self._full = full_text
        self.setToolTip(full_text)
        self.update()

    def text(self) -> str:  # noqa: N802
        return self._full

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        fm = self.fontMetrics()
        elided = fm.elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width() - 4)
        )
        painter.drawText(
            self.contentsRect(),
            self.alignment() | Qt.TextFlag.TextSingleLine,
            elided,
        )


class FileCard(QWidget):
    """Selected-video card (Update 9 task 2): file name (elided middle,
    full path in the tooltip), an X button to deselect. Shown instead of
    the drop zone once a video is imported. X is disabled while busy
    (the main window's set_busy drives it)."""

    removed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("file_card")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.name_label = ElidedLabel()
        self.x_btn = QPushButton("✕")
        self.x_btn.setObjectName("file_card_x")
        self.x_btn.setToolTip(i18n.tr("video.remove"))
        self.x_btn.setFixedWidth(24)
        self.x_btn.clicked.connect(self.removed.emit)
        row.addWidget(self.name_label, 1)
        row.addWidget(self.x_btn, 0)
        self.hide()

    def show_file(self, name: str, tooltip: str) -> None:
        self.name_label.setElidedText(name)
        self.name_label.setToolTip(tooltip)
        self.show()

    def setVisible_keeping(self) -> None:  # keep PySide happy: unused
        pass


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
    """Collapsible activity log (Update 9 task 3 "progress details").

    The old unlabeled ▸/▾ toggle is now a clearly labeled flat button
    ("Chi tiết tiến trình ▸ / ▾"). Collapsed by default; the parent
    panel supplies header/liveness lines as sibling labels."""

    EXPANDED_HEIGHT = 170
    openChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self._expanded = False
        self.toggle = QPushButton(i18n.tr("progress.details"))
        self.toggle.setObjectName("details_toggle")
        self.toggle.setFlat(True)
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
        self.set_open(not self._expanded)

    def set_open(self, open_: bool) -> None:
        """Open or close the details area; emits openChanged so the main
        window can persist the choice (Update 9 task 3.1)."""
        self._expanded = bool(open_)
        self.toggle.setText(
            i18n.tr("progress.details") + (" ▾" if self._expanded else " ▸")
        )
        self.text.setVisible(self._expanded)
        self.openChanged.emit(self._expanded)

    def is_open(self) -> bool:
        return self._expanded

    def toggle_text(self) -> str:
        return self.toggle.text()

    def log_text(self) -> str:
        return self.text.toPlainText()

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
