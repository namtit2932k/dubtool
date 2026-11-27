"""Emerald Studio (Dark Pro) theme engine for DubTool (PySide6).

Provides:
- Design tokens aligned with DubTool's brand banner (Obsidian + Emerald Mint + Sky Cyan).
- ``EmeraldProxyStyle`` over Qt's ``Fusion`` style so checkbox checkmarks,
  radio indicators, and combobox arrows render crisply at every DPI without
  external image files.
- ``apply_win_dark_titlebar`` via Win32 DWM (Windows 10/11), no-op elsewhere.
- ``set_button_variant`` for primary / danger / ghost button hierarchy.
- ``render_brand_icon_pixmap`` matching ``docs/images/brand/banner.svg``.
"""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QProxyStyle, QPushButton, QStyle, QStyleFactory, QWidget

# ---------------------------------------------------------------------- tokens
BG_CANVAS = "#0B0F17"
BG_HEADER = "#0E1420"
BG_CARD = "#131A26"
BG_INPUT = "#0F1623"
BG_CONSOLE = "#080C13"
BG_HOVER = "#1C2638"
BG_PRESSED = "#243147"

BORDER_SUBTLE = "#1F2A3C"
BORDER_INPUT = "#28354B"
BORDER_HOVER = "#3B4D6B"
BORDER_FOCUS = "#34D399"

EMERALD = "#34D399"
EMERALD_HOVER = "#6EE7B7"
EMERALD_PRESS = "#10B981"
SKY = "#38BDF8"
AMBER = "#F59E0B"
ROSE = "#F43F5E"

TEXT_PRIMARY = "#F1F5F9"
TEXT_SECONDARY = "#CBD5E1"
TEXT_MUTED = "#94A3B8"
TEXT_DISABLED = "#64748B"
TEXT_ON_PRIMARY = "#052E16"


# ------------------------------------------------------------ crisp proxy style
class EmeraldProxyStyle(QProxyStyle):
    """Wraps Fusion and draws crisp vector checkboxes, radio dots, and combo
    arrows so QSS dark styling never loses checkmarks at 100%/125%/150% DPI."""

    def __init__(self) -> None:
        base = QStyleFactory.create("Fusion")
        super().__init__(base)

    def drawPrimitive(self, element, option, painter, widget=None) -> None:  # noqa: N802
        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            self._draw_checkbox(option, painter)
            return
        if element == QStyle.PrimitiveElement.PE_IndicatorRadioButton:
            self._draw_radio(option, painter)
            return
        if element in (
            QStyle.PrimitiveElement.PE_IndicatorArrowDown,
            QStyle.PrimitiveElement.PE_IndicatorSpinDown,
        ):
            self._draw_chevron(option, painter, up=False)
            return
        if element in (
            QStyle.PrimitiveElement.PE_IndicatorArrowUp,
            QStyle.PrimitiveElement.PE_IndicatorSpinUp,
        ):
            self._draw_chevron(option, painter, up=True)
            return
        super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _draw_checkbox(option, painter: QPainter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(1.0, 1.0, -1.0, -1.0)
        # Keep square centered inside rect
        side = min(rect.width(), rect.height())
        if side > 16.0:
            side = 16.0
        cx, cy = rect.center().x(), rect.center().y()
        box = QRectF(cx - side / 2.0, cy - side / 2.0, side, side)

        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        checked = bool(state & QStyle.StateFlag.State_On)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)

        if checked:
            fill = QColor(EMERALD if enabled else "#1D4037")
            border = QColor(EMERALD_HOVER if (enabled and hovered) else (EMERALD if enabled else "#275347"))
        else:
            fill = QColor(BG_INPUT if enabled else "#0D121C")
            border = QColor(BORDER_FOCUS if (enabled and hovered) else (BORDER_INPUT if enabled else "#1B2433"))

        painter.setPen(QPen(border, 1.3))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(box, 4.0, 4.0)

        if checked:
            tick = QPainterPath()
            x, y, w, h = box.x(), box.y(), box.width(), box.height()
            tick.moveTo(x + w * 0.24, y + h * 0.52)
            tick.lineTo(x + w * 0.43, y + h * 0.72)
            tick.lineTo(x + w * 0.78, y + h * 0.30)
            pen = QPen(QColor(TEXT_ON_PRIMARY if enabled else "#64748B"), max(1.8, w * 0.14))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(tick)
        painter.restore()

    @staticmethod
    def _draw_radio(option, painter: QPainter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(1.0, 1.0, -1.0, -1.0)
        side = min(rect.width(), rect.height(), 16.0)
        cx, cy = rect.center().x(), rect.center().y()
        circle = QRectF(cx - side / 2.0, cy - side / 2.0, side, side)

        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        checked = bool(state & QStyle.StateFlag.State_On)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)

        border = QColor(EMERALD if (checked and enabled) else (BORDER_HOVER if hovered else BORDER_INPUT))
        if not enabled:
            border = QColor("#1B2433")
        painter.setPen(QPen(border, 1.4))
        painter.setBrush(QBrush(QColor(BG_INPUT if enabled else "#0D121C")))
        painter.drawEllipse(circle)

        if checked:
            dot_r = side * 0.26
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(EMERALD if enabled else "#4B7A6B")))
            painter.drawEllipse(QPointF(cx, cy), dot_r, dot_r)
        painter.restore()

    @staticmethod
    def _draw_chevron(option, painter: QPainter, *, up: bool = False) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect)
        cx, cy = rect.center().x(), rect.center().y()
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        color = QColor(TEXT_MUTED if enabled else TEXT_DISABLED)
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        dx = min(3.4, max(2.2, rect.width() * 0.22))
        dy = min(1.8, max(1.2, rect.height() * 0.16))
        if up:
            path.moveTo(cx - dx, cy + dy)
            path.lineTo(cx, cy - dy)
            path.lineTo(cx + dx, cy + dy)
        else:
            path.moveTo(cx - dx, cy - dy)
            path.lineTo(cx, cy + dy)
            path.lineTo(cx + dx, cy - dy)
        painter.drawPath(path)
        painter.restore()


# ----------------------------------------------------------- brand emblem art
def render_brand_icon_pixmap(size: int = 256, *, with_badge: bool = True) -> QPixmap:
    """Render the geometric Emerald hexagon + play emblem from banner.svg."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    s = float(size)
    if with_badge:
        pad = s * 0.04
        badge_rect = QRectF(pad, pad, s - 2 * pad, s - 2 * pad)
        bg_grad = QLinearGradient(badge_rect.topLeft(), badge_rect.bottomRight())
        bg_grad.setColorAt(0.0, QColor("#090D1A"))
        bg_grad.setColorAt(0.55, QColor("#111936"))
        bg_grad.setColorAt(1.0, QColor("#181534"))
        p.setBrush(QBrush(bg_grad))
        border_pen = QPen(QColor(255, 255, 255, 42), max(1.0, s * 0.02))
        p.setPen(border_pen)
        radius = s * 0.22
        p.drawRoundedRect(badge_rect, radius, radius)

    cx, cy = s / 2.0, s / 2.0
    scale = (s * 0.38 if with_badge else s * 0.45) / 42.0

    def pt(x: float, y: float) -> QPointF:
        return QPointF(cx + x * scale, cy + y * scale)

    # Outer dark hexagon with crisp white stroke
    outer = QPolygonF([pt(0, -42), pt(36, -21), pt(36, 21), pt(0, 42), pt(-36, 21), pt(-36, -21)])
    p.setBrush(QBrush(QColor(15, 23, 42, 220)))
    outer_pen = QPen(QColor("#FFFFFF"), max(1.2, 2.8 * scale))
    outer_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(outer_pen)
    p.drawPolygon(outer)

    # Mint-emerald right shell facets matching banner.svg
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#6EE7B7")))
    p.drawPolygon(QPolygonF([pt(0, -36), pt(30, -18), pt(18, -10), pt(0, -21)]))
    p.setBrush(QBrush(QColor("#34D399")))
    p.drawPolygon(QPolygonF([pt(30, -18), pt(30, 18), pt(18, 10), pt(18, -10)]))
    p.setBrush(QBrush(QColor("#6EE7B7")))
    p.drawPolygon(QPolygonF([pt(30, 18), pt(0, 36), pt(0, 21), pt(18, 10)]))

    # Inner hexagon outline
    inner = QPolygonF([pt(0, -21), pt(18, -10), pt(18, 10), pt(0, 21), pt(-18, 10), pt(-18, -10)])
    inner_pen = QPen(QColor(255, 255, 255, 230), max(1.0, 2.2 * scale))
    inner_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(inner_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolygon(inner)

    # Center play triangle
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#FFFFFF")))
    p.drawPolygon(QPolygonF([pt(-5.5, -10), pt(11, 0), pt(-5.5, 10)]))

    p.end()
    return pm


# ----------------------------------------------------------- Win32 dark chrome
def apply_win_dark_titlebar(widget: QWidget) -> None:
    """Enable Windows 10/11 dark window frame & Obsidian caption via DWM."""
    if sys.platform != "win32":
        return
    try:
        app = QApplication.instance()
        if app is not None and str(app.platformName()).lower() != "windows":
            return
        hwnd = int(widget.winId())
        if not hwnd:
            return
        import ctypes

        dwmapi = ctypes.windll.dwmapi
        val_dark = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE (20 on Win10 2004+/Win11, fallback 19)
        hr = dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(val_dark), ctypes.sizeof(val_dark)
        )
        if hr != 0:
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), 19, ctypes.byref(val_dark), ctypes.sizeof(val_dark)
            )
        # DWMWA_CAPTION_COLOR = 35 (#0E1420 -> COLORREF 0x0020140E)
        cap_color = ctypes.c_int(0x0020140E)
        dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 35, ctypes.byref(cap_color), ctypes.sizeof(cap_color)
        )
        # DWMWA_BORDER_COLOR = 34 (#1F2A3C -> COLORREF 0x003C2A1F)
        bdr_color = ctypes.c_int(0x003C2A1F)
        dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 34, ctypes.byref(bdr_color), ctypes.sizeof(bdr_color)
        )
    except Exception:
        pass


def set_button_variant(btn: QPushButton, variant: str) -> None:
    """Attach a semantic QSS variant ('primary', 'danger', 'ghost') to a button."""
    btn.setProperty("variant", variant)
    style = btn.style()
    if style is not None:
        style.unpolish(btn)
        style.polish(btn)


# --------------------------------------------------------------- QSS & Palette
EMERALD_QSS = """
/* ===== Base & Canvas ===== */
QMainWindow, QDialog, QMessageBox {
    background-color: #0B0F17;
    color: #F1F5F9;
}

QWidget {
    color: #F1F5F9;
    selection-background-color: rgba(52, 211, 153, 0.30);
    selection-color: #F8FAFC;
}

QScrollArea, QScrollArea > QWidget > QWidget {
    background-color: #0B0F17;
    border: none;
}

QStatusBar {
    background-color: #0E1420;
    color: #94A3B8;
    border-top: 1px solid #1F2A3C;
}

/* ===== Menu Bar & Menus ===== */
QMenuBar {
    background-color: #0E1420;
    color: #CBD5E1;
    border-bottom: 1px solid #1F2A3C;
    padding: 2px 8px;
}
QMenuBar::item {
    background: transparent;
    padding: 5px 10px;
    border-radius: 6px;
}
QMenuBar::item:selected {
    background-color: #1C2638;
    color: #F8FAFC;
}
QMenu {
    background-color: #131A26;
    color: #F1F5F9;
    border: 1px solid #28354B;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 18px 6px 12px;
    border-radius: 6px;
}
QMenu::item:selected {
    background-color: rgba(52, 211, 153, 0.16);
    color: #6EE7B7;
}

/* ===== Brand Header Strip ===== */
QFrame#brand_header {
    background-color: #0E1420;
    border-bottom: 1px solid #1F2A3C;
}
QLabel#brand_title {
    color: #F8FAFC;
    font-size: 14px;
    font-weight: 700;
}
QLabel#brand_version {
    background-color: rgba(52, 211, 153, 0.14);
    color: #6EE7B7;
    border: 1px solid rgba(52, 211, 153, 0.32);
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 11px;
    font-weight: 600;
}

/* ===== Cards (QGroupBox) ===== */
QGroupBox {
    background-color: #131A26;
    border: 1px solid #1F2A3C;
    border-radius: 10px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    padding: 0 6px;
    color: #94A3B8;
    background-color: #0B0F17;
    border-radius: 4px;
}
QGroupBox:disabled {
    border-color: #182130;
}

/* ===== DropZone & FileCard ===== */
QPushButton#drop_zone {
    background-color: #0F1623;
    border: 1.5px dashed rgba(52, 211, 153, 0.42);
    border-radius: 10px;
    color: #CBD5E1;
    padding: 14px 12px;
    font-weight: 500;
    text-align: center;
}
QPushButton#drop_zone:hover, QPushButton#drop_zone:focus, QPushButton#drop_zone[dragActive="true"] {
    background-color: rgba(52, 211, 153, 0.09);
    border-color: #34D399;
    color: #F8FAFC;
}
QPushButton#drop_zone:disabled {
    background-color: #0D121C;
    border-color: #1F2A3C;
    color: #64748B;
}

QWidget#file_card {
    background-color: #0F1623;
    border: 1px solid #28354B;
    border-radius: 8px;
}
QPushButton#file_card_x {
    background-color: transparent;
    color: #94A3B8;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0px;
    font-weight: 600;
}
QPushButton#file_card_x:hover {
    background-color: rgba(244, 63, 94, 0.18);
    border-color: rgba(244, 63, 94, 0.40);
    color: #FDA4AF;
}
QPushButton#file_card_x:disabled {
    background-color: transparent;
    border-color: transparent;
    color: #475569;
}

/* ===== Labels ===== */
QLabel#video_info, QLabel#brief_counter, QLabel#details_liveness {
    color: #94A3B8;
}
QLabel#output_warn {
    color: #F59E0B;
}
QLabel#gain_value {
    color: #6EE7B7;
    font-weight: 600;
}
QLabel#lang_status_lbl {
    background-color: #0F1623;
    border: 1px solid #28354B;
    border-radius: 6px;
    padding: 4px 10px;
    color: #6EE7B7;
}
QLabel#details_header {
    color: #E2E8F0;
    font-weight: 600;
}
QLabel#read_only_banner {
    background-color: rgba(245, 158, 11, 0.16);
    border: 1px solid rgba(245, 158, 11, 0.45);
    color: #FDE68A;
    padding: 6px 10px;
    border-radius: 6px;
}

/* ===== Inputs & Combos ===== */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background-color: #0F1623;
    color: #F1F5F9;
    border: 1px solid #28354B;
    border-radius: 8px;
    padding: 4px 8px;
}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {
    border-color: #3B4D6B;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #34D399;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    background-color: #0D121C;
    color: #64748B;
    border-color: #1B2433;
}
QComboBox QAbstractItemView {
    background-color: #131A26;
    color: #F1F5F9;
    border: 1px solid #28354B;
    border-radius: 8px;
    selection-background-color: rgba(52, 211, 153, 0.18);
    selection-color: #6EE7B7;
    outline: 0;
}

/* ===== Checkboxes & Radios ===== */
QCheckBox, QRadioButton {
    spacing: 7px;
    color: #E2E8F0;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: #64748B;
}

/* ===== Buttons & Variants ===== */
QPushButton {
    background-color: #1A2436;
    color: #E2E8F0;
    border: 1px solid #28354B;
    border-radius: 8px;
    padding: 5px 10px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #233047;
    border-color: #3B4D6B;
    color: #F8FAFC;
}
QPushButton:pressed {
    background-color: #2A3A55;
}
QPushButton:disabled {
    background-color: #121824;
    color: #64748B;
    border-color: #1B2433;
}

QPushButton[variant="primary"] {
    background-color: #34D399;
    color: #052E16;
    border: 1px solid #34D399;
    font-weight: 700;
    padding: 5px 14px;
}
QPushButton[variant="primary"]:hover {
    background-color: #6EE7B7;
    border-color: #6EE7B7;
    color: #052E16;
}
QPushButton[variant="primary"]:pressed {
    background-color: #10B981;
    border-color: #10B981;
}
QPushButton[variant="primary"]:disabled {
    background-color: #19322C;
    color: #4B7A6B;
    border-color: #1F3D36;
}

QPushButton[variant="danger"] {
    background-color: rgba(244, 63, 94, 0.14);
    color: #FDA4AF;
    border: 1px solid rgba(244, 63, 94, 0.36);
    font-weight: 600;
}
QPushButton[variant="danger"]:hover {
    background-color: rgba(244, 63, 94, 0.24);
    border-color: rgba(244, 63, 94, 0.56);
    color: #FFE4E6;
}
QPushButton[variant="danger"]:disabled {
    background-color: #1B161C;
    color: #64748B;
    border-color: #2A2029;
}

QPushButton[variant="ghost"] {
    background-color: transparent;
    color: #94A3B8;
    border: 1px solid transparent;
    padding: 4px 8px;
}
QPushButton[variant="ghost"]:hover {
    background-color: #1A2436;
    color: #F1F5F9;
}

/* ===== Slider ===== */
QSlider::groove:horizontal {
    height: 5px;
    background: #1F2A3C;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #34D399;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #F8FAFC;
    border: 2px solid #34D399;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::handle:horizontal:disabled {
    background: #475569;
    border-color: #334155;
}

/* ===== Progress Bar ===== */
QProgressBar {
    background-color: #1B2433;
    border: none;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38BDF8, stop:1 #34D399);
    border-radius: 4px;
}

/* ===== List / StageList / Log ===== */
QListWidget {
    background-color: #0F1623;
    border: 1px solid #1F2A3C;
    border-radius: 8px;
    padding: 4px;
    outline: 0;
}
QListWidget::item {
    border-radius: 6px;
    padding: 4px 8px;
}
QListWidget::item:selected {
    background-color: rgba(52, 211, 153, 0.16);
    color: #F8FAFC;
}
QPlainTextEdit#log_console {
    background-color: #080C13;
    color: #CBD5E1;
    border: 1px solid #1F2A3C;
    border-radius: 8px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}

/* ===== Tabs (SettingsDialog) ===== */
QTabWidget::pane {
    background-color: #131A26;
    border: 1px solid #1F2A3C;
    border-radius: 10px;
    top: -1px;
}
QTabBar::tab {
    background-color: #0E1420;
    color: #94A3B8;
    border: 1px solid #1F2A3C;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 12px;
    margin-right: 4px;
}
QTabBar::tab:selected {
    background-color: #131A26;
    color: #6EE7B7;
    border-color: #28354B;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    color: #E2E8F0;
    background-color: #151E2E;
}

/* ===== Scrollbars ===== */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #28354B;
    min-height: 28px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #3B4D6B;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
    height: 0px;
}

/* ===== Tooltip ===== */
QToolTip {
    background-color: #1C2638;
    color: #F1F5F9;
    border: 1px solid #34D399;
    border-radius: 6px;
    padding: 4px 8px;
}
"""


def _build_dark_palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG_CANVAS))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG_INPUT))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_CARD))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_HOVER))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Button, QColor("#1A2436"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(EMERALD))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(TEXT_ON_PRIMARY))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    pal.setColor(QPalette.ColorRole.Link, QColor(SKY))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(TEXT_DISABLED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TEXT_DISABLED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_DISABLED))
    return pal


def apply_emerald_theme(app: QApplication) -> None:
    """Apply Emerald Studio proxy style, dark palette, and stylesheet once."""
    if not app.property("_dubtool_emerald_theme"):
        app.setStyle(EmeraldProxyStyle())
        app.setProperty("_dubtool_emerald_theme", True)
    app.setPalette(_build_dark_palette())
    if app.styleSheet() != EMERALD_QSS:
        app.setStyleSheet(EMERALD_QSS)
