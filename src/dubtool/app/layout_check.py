"""Automated layout check (AGENT_UPDATE_5.md task 4).

Asserts, for window sizes 760x600 … 1920x1000 and the UI states idle /
video selected / running / finished / error:
1. no two visible sibling widgets in the same layout intersect;
2. every visible widget is at least its minimumSizeHint();
3. no QLabel/QPushButton/QCheckBox text is clipped (word wrap accounted);
4. the scroll area shows a vertical scrollbar (when the content is
   taller) and never a horizontal one.

Usable both as a pytest helper (`violations`) and as the flag
`--layout-check [--scale S]` from source and from the packaged exe. The
flag prints a size x state table and exits non-zero on any failure.
"""

from __future__ import annotations

from pathlib import Path

SIZES = [(760, 600), (900, 700), (1100, 800), (1366, 700), (1920, 1000)]
STATES = ("idle", "video", "running", "finished", "error")
OVERLAP_EPS = 2  # px tolerated for rounding


def _probe_file() -> Path:
    """An existing file to name as the fake video. Path(__file__) points
    inside the frozen PYZ archive (not a real file), so prefer the exe."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(__file__)


def apply_state(win, state: str) -> None:
    """Move the window into one of the check/screenshot states, without
    any probing or dialogs (the samples emulate the widgets directly)."""
    from dubtool.api import DubResult
    from dubtool.app.ui.main_window import MainWindow

    assert isinstance(win, MainWindow)
    win.service.running = state == "running"
    win.video_path = None
    win.video_info.clear()
    win.output_warn.clear()
    win.progress_group.reset_for_check()
    win._refresh_buttons()
    if state == "video":
        win.video_path = _probe_file()
        win.video_info.setText("test.mp4 · 1.0 min")
        win.output_dir = Path.cwd()
        win._validate_output()
        win._refresh_buttons()
    elif state == "running":
        win.output_dir = Path.cwd()
        win.output_edit.setText(str(win.output_dir))
        win._refresh_buttons()
        win.progress_group.show_running()
        events = [
            ("s1", "start", -1, -1, ""),
            ("s1", "done", -1, -1, ""),
            ("s2", "start", -1, -1, ""),
            ("s2", "progress", 40, 92, ""),
            ("s3", "start", -1, -1, ""),
            ("s3", "progress", 2, 5, ""),
        ]
        for stage, st, done, total, msg in events:
            win.progress_group.on_progress(stage, st, done, total, msg, 12.0)
    elif state == "finished":
        win.output_dir = Path.cwd()
        win.output_edit.setText(str(win.output_dir))
        win._refresh_buttons()
        win.progress_group.show_running()
        win.progress_group.on_finished(DubResult(ok=True, code="ok", out_video=Path("out/ok.mp4")))
    elif state == "error":
        win.output_dir = Path.cwd()
        win.output_edit.setText(str(win.output_dir))
        win._refresh_buttons()
        win.progress_group.show_running()
        win.progress_group.on_finished(DubResult(ok=False, code="quota"))


def _new_window():
    from dubtool.app.service import PipelineService
    from dubtool.app.ui.main_window import MainWindow

    return MainWindow(PipelineService())


def violations(root) -> list[str]:
    """All layout violations of `root` as printable descriptions."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractButton,
        QCheckBox,
        QLabel,
        QPushButton,
        QScrollArea,
        QWidget,
    )

    problems: list[str] = []

    def name(w) -> str:
        n = w.objectName() or w.__class__.__name__
        parent = w.parentWidget()
        pname = (parent.objectName() if parent is not None else "") or (
            parent.__class__.__name__ if parent is not None else ""
        )
        return f"{n} in {pname}"

    def walk(w):
        yield w
        for c in w.findChildren(QWidget):
            yield c

    widgets = list(walk(root))
    scroll_pages = {
        sa.widget()
        for sa in widgets
        if isinstance(sa, QScrollArea) and sa.widget() is not None
    }

    # 1. Visible siblings under the same parent may not overlap.
    for w in widgets:
        if not w.isVisible():
            continue
        parent = w.parentWidget()
        if parent is None or parent.layout() is None:
            continue
        siblings = [
            c
            for c in parent.children()
            if isinstance(c, QWidget) and c.isVisible() and parent.layout().indexOf(c) >= 0
        ]
        for i in range(len(siblings)):
            for j in range(i + 1, len(siblings)):
                a, b = siblings[i], siblings[j]
                inter = a.geometry().intersected(b.geometry())
                if inter.width() > OVERLAP_EPS and inter.height() > OVERLAP_EPS:
                    problems.append(
                        f"overlap:{name(parent)}: {name(a)} <-> {name(b)} "
                        f"({inter.width()}x{inter.height()})"
                    )

    # 2. Every visible widget is at least its minimum size. Per axis, the
    #    explicit minimumSize (e.g. the LogView toggle's setFixedWidth, or a
    #    label's setMinimumHeight) wins over the text-based
    #    minimumSizeHint; an axis of zero means "unset" there.
    #    Exempted: the QScrollArea's resizable page itself (scrolling exists
    #    precisely to show content bigger than the viewport; the page is
    #    then sized by the scroll area, and the scrollbar check below makes
    #    sure the content is reachable) and QLabels with empty text.
    for w in widgets:
        if (
            not w.isVisible()
            or w in scroll_pages
            or (isinstance(w, QLabel) and not w.text())
        ):
            continue
        try:
            msh = w.minimumSizeHint()
        except Exception:
            continue
        if not msh.isValid():
            continue
        explicit = w.minimumSize()
        min_w = explicit.width() if explicit.width() > 0 else msh.width()
        min_h = explicit.height() if explicit.height() > 0 else msh.height()
        if w.width() + OVERLAP_EPS < min_w or w.height() + OVERLAP_EPS < min_h:
            problems.append(
                f"too-small:{name(w)}: {w.width()}x{w.height()} < min {min_w}x{min_h}"
            )

    # 3. Text not clipped (QLabel/QAbstractButton), word wrap allowed.
    for w in widgets:
        if not w.isVisible():
            continue
        text = w.text() if isinstance(w, (QLabel, QAbstractButton)) else ""
        if not text:
            continue
        fm = w.fontMetrics()
        available = w.contentsRect()
        if isinstance(w, QLabel) and w.wordWrap():
            rect = fm.boundingRect(available, Qt.TextFlag.TextWordWrap, text)
            if rect.height() > available.height() + OVERLAP_EPS:
                problems.append(
                    f"text-clipped:{name(w)}: needs {rect.height()}px, has {available.height()}px"
                )
        else:
            rect = fm.boundingRect(available, 0, text)
            if (
                rect.width() > available.width() + OVERLAP_EPS
                or rect.height() > available.height() + OVERLAP_EPS
            ):
                problems.append(
                    f"text-clipped:{name(w)}: needs {rect.width()}x{rect.height()}, "
                    f"has {available.width()}x{available.height()}"
                )

    # 4. Scroll areas: vertical when needed, never horizontal.
    for w in widgets:
        if not isinstance(w, QScrollArea) or not w.isVisible():
            continue
        policy = w.horizontalScrollBarPolicy()
        if policy != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            problems.append(f"scroll-horizontal-enabled:{name(w)}")
        content = w.widget()
        if content is not None and content.minimumSizeHint().height() > w.viewport().height():
            if not w.verticalScrollBar().isVisible():
                problems.append(f"scroll-vertical-missing:{name(w)}")
    return problems


def run_layout_check(scale: float = 1.0, sizes=SIZES, states=STATES) -> int:
    """The CLI/pytest driver: returns the number of violations."""
    import os

    if scale != 1.0:
        # Must be set before the QApplication/platform exist.
        os.environ["QT_SCALE_FACTOR"] = str(scale)

    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication

    from dubtool.app import i18n, settings

    i18n.set_language("vi")
    app = QApplication.instance() or QApplication([])
    win = _new_window()
    win.show()
    rows: list[tuple[str, str, list[str]]] = []
    for size in sizes:
        win.resize(QSize(size[0], size[1]))
        app.processEvents()
        for state in states:
            apply_state(win, state)
            app.processEvents()
            rows.append((f"{size[0]}x{size[1]}", state, violations(win)))
    print(f"layout check (scale {scale}):")
    print(f"{'size':<12} {'state':<10} result")
    print("-" * 40)
    total = 0
    for size_txt, state, problems in rows:
        total += len(problems)
        print(f"{size_txt:<12} {state:<10} {'PASS' if not problems else 'FAIL'}")
        for p in problems:
            print(f"{'':>24}{p}")
    print(f"total violations: {total}")
    win.hide()
    return total
