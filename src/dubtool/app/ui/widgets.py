"""Reusable widgets: drop zone, file card, stage list, details log view.

No user-facing string literals here — all text comes from i18n.tr().
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from dubtool.app import i18n
from dubtool.app.ui.theme import (
    EMERALD,
    EMERALD_HOVER,
    ROSE,
    SKY,
    TEXT_DISABLED,
    TEXT_MUTED,
    TEXT_PRIMARY,
    set_button_variant,
)

STAGES = ("s1", "s2", "s3", "s4", "s5", "s6", "s7")

_ROLE_STATE = Qt.ItemDataRole.UserRole + 1
_ROLE_PROGRESS = Qt.ItemDataRole.UserRole + 2


class DropZone(QPushButton):
    """Drag-and-drop or click/keyboard/UIA button to pick a video (AGU4 7.1).

    Inherits ``QPushButton`` so Windows UIA ``InvokePattern`` and keyboard
    activation (Tab + Space/Enter) work natively alongside mouse click and
    drag-and-drop.
    """

    filesDropped = Signal(str)

    def __init__(self):
        super().__init__(i18n.tr("video.drop"))
        self.setObjectName("drop_zone")
        self.setMinimumWidth(0)
        self.setMinimumHeight(88)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("dragActive", False)

    def _set_drag_active(self, active: bool) -> None:
        if bool(self.property("dragActive")) != active:
            self.setProperty("dragActive", active)
            st = self.style()
            if st is not None:
                st.unpolish(self)
                st.polish(self)
            self.update()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls:
            self._set_drag_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_drag_active(False)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                self.filesDropped.emit(path)
                break


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
        painter = QPainter(self)
        fm = self.fontMetrics()
        elided = fm.elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, max(0, self.width() - 4)
        )
        painter.setPen(self.palette().color(self.foregroundRole()))
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(8)
        self.name_label = ElidedLabel()
        font = self.name_label.font()
        font.setWeight(QFont.Weight.DemiBold)
        self.name_label.setFont(font)
        self.x_btn = QPushButton("✕")
        self.x_btn.setObjectName("file_card_x")
        self.x_btn.setToolTip(i18n.tr("video.remove"))
        self.x_btn.setFixedSize(26, 26)
        self.x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
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


class StageItemDelegate(QStyledItemDelegate):
    """Draws each pipeline stage with crisp vector status badge & progress pill
    while preserving ``item.text()`` untouched for tests and accessibility."""

    ROW_HEIGHT = 26

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        sz = super().sizeHint(option, index)
        return QSize(sz.width(), max(sz.height(), self.ROW_HEIGHT))

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRectF(option.rect).adjusted(2.0, 1.5, -2.0, -1.5)
        stage = str(index.data(Qt.ItemDataRole.UserRole) or "")
        state = str(index.data(_ROLE_STATE) or "pending")
        prog = index.data(_ROLE_PROGRESS)
        row_num = index.row() + 1
        label = i18n.tr(stage) if stage in STAGES else str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        # Row background / active highlight
        if state in ("start", "progress"):
            painter.setBrush(QBrush(QColor(56, 189, 248, 30)))
            painter.setPen(QPen(QColor(56, 189, 248, 96), 1.0))
            painter.drawRoundedRect(rect, 6.0, 6.0)
        elif state == "error":
            painter.setBrush(QBrush(QColor(244, 63, 94, 30)))
            painter.setPen(QPen(QColor(244, 63, 94, 96), 1.0))
            painter.drawRoundedRect(rect, 6.0, 6.0)

        # Left circular status icon
        cy = rect.center().y()
        cx = rect.left() + 16.0
        r = 8.5

        if state == "done":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(52, 211, 153, 44)))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            tick = QPainterPath()
            tick.moveTo(cx - 3.6, cy + 0.2)
            tick.lineTo(cx - 1.0, cy + 2.9)
            tick.lineTo(cx + 4.0, cy - 2.8)
            pen = QPen(QColor(EMERALD), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(tick)
            text_color = QColor("#E2E8F0")
        elif state in ("start", "progress"):
            painter.setPen(QPen(QColor(SKY), 1.4))
            painter.setBrush(QBrush(QColor(56, 189, 248, 38)))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(SKY)))
            painter.drawEllipse(QPointF(cx, cy), 3.4, 3.4)
            text_color = QColor("#FFFFFF")
        elif state == "error":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(244, 63, 94, 48)))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            pen = QPen(QColor(ROSE), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx - 3.0, cy - 3.0), QPointF(cx + 3.0, cy + 3.0))
            painter.drawLine(QPointF(cx + 3.0, cy - 3.0), QPointF(cx - 3.0, cy + 3.0))
            text_color = QColor("#FDA4AF")
        elif state == "skipped":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#1B2433")))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            pen = QPen(QColor(TEXT_MUTED), 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx - 3.0, cy), QPointF(cx + 3.0, cy))
            text_color = QColor(TEXT_MUTED)
        else:
            painter.setPen(QPen(QColor("#28354B"), 1.0))
            painter.setBrush(QBrush(QColor("#1B2433")))
            painter.drawEllipse(QPointF(cx, cy), r, r)
            num_font = QFont(option.font)
            num_font.setPointSizeF(max(7.5, option.font.pointSizeF() - 1.5))
            num_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(num_font)
            painter.setPen(QColor(TEXT_DISABLED))
            painter.drawText(
                QRectF(cx - r, cy - r, r * 2, r * 2),
                Qt.AlignmentFlag.AlignCenter,
                str(row_num),
            )
            text_color = QColor(TEXT_MUTED)

        # Optional right-aligned progress pill ("2/5")
        right_edge = rect.right() - 8.0
        if state == "progress" and isinstance(prog, tuple) and len(prog) == 2 and prog[1]:
            badge_txt = f"{prog[0]}/{prog[1]}"
            b_font = QFont(option.font)
            b_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(b_font)
            fm = painter.fontMetrics()
            bw = fm.horizontalAdvance(badge_txt) + 14.0
            bh = 19.0
            b_rect = QRectF(right_edge - bw, cy - bh / 2.0, bw, bh)
            painter.setPen(QPen(QColor(52, 211, 153, 80), 1.0))
            painter.setBrush(QBrush(QColor(52, 211, 153, 30)))
            painter.drawRoundedRect(b_rect, 9.0, 9.0)
            painter.setPen(QColor(EMERALD_HOVER))
            painter.drawText(b_rect, Qt.AlignmentFlag.AlignCenter, badge_txt)
            right_edge = b_rect.left() - 8.0

        # Stage label
        lbl_font = QFont(option.font)
        if state in ("start", "progress"):
            lbl_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(lbl_font)
        painter.setPen(text_color)
        text_rect = QRectF(cx + r + 10.0, rect.top(), max(10.0, right_edge - (cx + r + 10.0)), rect.height())
        elided = painter.fontMetrics().elidedText(
            label, Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )
        painter.restore()


class StageList(QListWidget):
    """The seven pipeline stages with state icons (AGU4 7.6)."""

    STATE_ICON = {"start": "▶", "progress": "…", "done": "✓", "error": "✗", "skipped": "→"}

    def __init__(self):
        super().__init__()
        self._states: dict[str, str] = {}
        self.setItemDelegate(StageItemDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(1)
        for stage in STAGES:
            item = QListWidgetItem(self._label(stage))
            item.setData(Qt.ItemDataRole.UserRole, stage)
            item.setData(_ROLE_STATE, "pending")
            item.setData(_ROLE_PROGRESS, None)
            self.addItem(item)
        self.setAllPending()
        # All seven stages visible at once; no scrolling (AGU4 7.6).
        row_h = max(self.sizeHintForRow(0), StageItemDelegate.ROW_HEIGHT)
        self.setMinimumHeight(7 * (row_h + 2) + 12)

    @staticmethod
    def _label(stage: str) -> str:
        base = i18n.tr(stage)
        return f"○ {base}"

    def setAllPending(self) -> None:
        self._states = {stage: "pending" for stage in STAGES}
        for row in range(self.count()):
            item = self.item(row)
            item.setText(f"○ {i18n.tr(item.data(Qt.ItemDataRole.UserRole))}")
            item.setData(_ROLE_STATE, "pending")
            item.setData(_ROLE_PROGRESS, None)

    def update_state(self, stage: str, state: str, done: int | None = None, total: int | None = None) -> None:
        self._states[stage] = state
        label = i18n.tr(stage) if stage in STAGES else stage
        icon = self.STATE_ICON.get(state, "○")
        text = f"{icon} {label}"
        prog = None
        if state == "progress" and done is not None and total and total > 0:
            text = f"{icon} {label}: {done}/{total}"
            prog = (int(done), int(total))
        for row in range(self.count()):
            item = self.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == stage:
                item.setText(text)
                item.setData(_ROLE_STATE, state)
                item.setData(_ROLE_PROGRESS, prog)
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
        set_button_variant(self.toggle, "ghost")
        self.toggle.clicked.connect(self._toggle)
        self.text = QPlainTextEdit()
        self.text.setObjectName("log_console")
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
