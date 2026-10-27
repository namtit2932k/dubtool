"""Main window (AGU4 7). Vietnamese by default; all strings via i18n.tr().
The dub runs on the PipelineService worker thread; the UI only receives
signals, so the window stays responsive.
"""

from __future__ import annotations

import shutil
import os
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dubtool import api, proc
from dubtool.app import diagnostics, errors, i18n, modes, paths, secrets, settings
from dubtool.app.coalesce import ProgressGate
from dubtool.app.service import PipelineService
from dubtool.app.ui import first_run
from dubtool.app.ui.settings_dialog import VOICE_KEYS, SettingsDialog
from dubtool.app.ui.widgets import DropZone, LogView, StageList

VIDEO_FILTERS = "Videos (*.mp4 *.mkv *.mov *.webm *.avi *.m4v)"


def _open_in_explorer(path: Path) -> None:
    try:
        if sys.platform == "win32":
            # explorer needs its own process; through the helper it stays
            # hidden (no console flash, AGU6 task 1).
            from dubtool import proc

            proc.run(["explorer", str(path)], capture=False)
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        pass


def _open_file(path: Path) -> None:
    """No console anywhere (AGU6 task 1): os.startfile instead of cmd /c start."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            from dubtool import proc

            proc.run(["xdg-open", str(path)], capture=False)
    except OSError:
        pass


def _writable(path: Path) -> bool:
    try:
        probe = path / ".dubtool_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _ffmpeg_version() -> str:
    try:
        from dubtool import audio_utils

        out = proc.run(
            [audio_utils.ffmpeg_bin(), "-version"], timeout=30
        )
        return (out.stdout or out.stderr).splitlines()[0]
    except Exception:
        return "?"


# ------------------------------------------------------------------- widgets
class BriefEdit(QTextEdit):
    """Video description, max 1000 chars with a counter (AGU4 7.3)."""

    def __init__(self, counter: QLabel):
        super().__init__()
        self._counter = counter
        self.setPlaceholderText(i18n.tr("brief.placeholder"))
        self.setMaximumHeight(72)
        self._truncating = False
        self.textChanged.connect(self._on_change)
        self._on_change()

    def _on_change(self) -> None:
        text = self.toPlainText()
        if len(text) > 1000 and not self._truncating:
            # Truncate to 1000 chars while preserving the cursor position
            # (AGENT_UPDATE_5.md 5.4).
            self._truncating = True
            pos = min(self.textCursor().position(), 1000)
            self.setPlainText(text[:1000])
            cursor = self.textCursor()
            cursor.setPosition(max(0, pos))
            self.setTextCursor(cursor)
            self._truncating = False
            text = self.toPlainText()
        self._counter.setText(i18n.tr("brief.counter", n=len(text)))


class OptionsWidget(QGroupBox):
    """Options panel (collapsed by default, AGU4 7.4)."""

    def __init__(self):
        super().__init__(i18n.tr("options.header"))
        self.setCheckable(True)
        self.setChecked(False)
        grid = QGridLayout(self)
        s = settings.load()
        self.burn_check = QCheckBox(i18n.tr("options.burn"))
        self.burn_check.setChecked(s["burn_subtitles"])
        self.srt_check = QCheckBox(i18n.tr("options.srt"))
        self.srt_check.setChecked(s["save_srt"])
        self.keep_check = QCheckBox(i18n.tr("options.keep"))
        self.keep_check.setChecked(s["keep_intermediate"])
        # AGENT_UPDATE_7.md task 4: the customer-readable translation that
        # ships next to every exported video (default on, docx or txt).
        self.readable_check = QCheckBox(i18n.tr("options.readable"))
        self.readable_check.setChecked(bool(s.get("export_readable", True)))
        self.readable_format_combo = QComboBox()
        self.readable_format_combo.addItem(i18n.tr("readable.format_docx"), "docx")
        self.readable_format_combo.addItem(i18n.tr("readable.format_txt"), "txt")
        self.readable_format_combo.setCurrentIndex(
            max(0, self.readable_format_combo.findData(s.get("readable_format", "docx")))
        )
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(-24, -6)
        self.gain_slider.setValue(int(s["original_gain_db"]))
        self.gain_value = QLabel(f"{s['original_gain_db']} dB")
        self.gain_slider.valueChanged.connect(lambda v: self.gain_value.setText(f"{v} dB"))
        self.voice_combo = QComboBox()
        self.voice_combo.addItems(VOICE_KEYS)
        self.voice_combo.setCurrentText(s["voice"])
        grid.addWidget(self.burn_check, 0, 0, 1, 3)
        grid.addWidget(self.srt_check, 1, 0, 1, 3)
        grid.addWidget(self.keep_check, 2, 0, 1, 3)
        grid.addWidget(self.readable_check, 3, 0)
        grid.addWidget(self.readable_format_combo, 3, 1, 1, 2)
        grid.addWidget(QLabel(i18n.tr("options.gain")), 4, 0)
        grid.addWidget(self.gain_slider, 4, 1)
        grid.addWidget(self.gain_value, 4, 2)
        grid.addWidget(QLabel(i18n.tr("options.voice")), 5, 0)
        grid.addWidget(self.voice_combo, 5, 1, 1, 2)

    def to_options(self, mode: str, brief: str) -> api.DubOptions:
        from dubtool.brief import normalize_brief

        return api.DubOptions(
            mode=mode,
            burn_subtitles=self.burn_check.isChecked(),
            save_srt=self.srt_check.isChecked(),
            original_gain_db=float(self.gain_slider.value()),
            voice=self.voice_combo.currentText(),
            keep_intermediate=self.keep_check.isChecked(),
            export_readable=self.readable_check.isChecked(),
            readable_format=str(self.readable_format_combo.currentData() or "docx"),
            description=(normalize_brief(brief) or None),
        )


class ProgressWidget(QGroupBox):
    """Compact progress panel (AGENT_UPDATE_5.md task 4): the seven stages
    with no inner scrollbar, bar + counter on one line, ONE horizontal row
    for the action buttons, and a collapsible log (collapsed by default)."""

    def __init__(self, service: PipelineService, window: "MainWindow"):
        super().__init__(i18n.tr("progress.header"))
        self.setObjectName("progress_group")
        self.service = service
        self.window_ref = window
        self._last_result: api.DubResult | None = None
        self._last_mode = "fast"
        layout = QVBoxLayout(self)
        self.stages = StageList()
        layout.addWidget(self.stages)
        line = QHBoxLayout()
        self.bar = QProgressBar()
        line.addWidget(self.bar, 1)
        self.counter = QLabel("")
        self.counter.setWordWrap(False)
        line.addWidget(self.counter)
        layout.addLayout(line)

        # One horizontal row for the action buttons; only applicable ones
        # are visible, so the row collapses or extends as needed.
        self.action_row = QHBoxLayout()
        self.cancel_btn = QPushButton(i18n.tr("progress.cancel"))
        self.cancel_btn.clicked.connect(service.cancel)
        self.resume_btn = QPushButton(i18n.tr("progress.resume"))
        self.resume_btn.clicked.connect(lambda: self._retry(force=False))
        self.over_btn = QPushButton(i18n.tr("progress.start_over"))
        self.over_btn.clicked.connect(lambda: self._retry(force=True))
        self.open_btn = QPushButton(i18n.tr("progress.open_folder"))
        self.open_btn.clicked.connect(self._open_folder)
        self.play_btn = QPushButton(i18n.tr("progress.play"))
        self.play_btn.clicked.connect(self._play)
        # AGENT_UPDATE_7.md task 4: opens the readable translation file.
        self.readable_btn = QPushButton(i18n.tr("progress.open_readable"))
        self.readable_btn.clicked.connect(self._open_readable)
        for b in (
            self.cancel_btn, self.resume_btn, self.over_btn,
            self.open_btn, self.play_btn, self.readable_btn,
        ):
            self.action_row.addWidget(b)
        layout.addLayout(self.action_row)

        self.log = LogView()
        layout.addWidget(self.log)
        self.hide()

    def reset_for_check(self):
        """Pristine state between layout-check/screenshot states (AGENT_UPDATE_5.md task 4)."""
        self.hide()
        self.stages.setAllPending()
        self.bar.setRange(0, 0)
        self.bar.setValue(0)
        self.counter.setText("")
        self.cancel_btn.hide()
        self.resume_btn.hide()
        self.over_btn.hide()
        self.open_btn.hide()
        self.play_btn.hide()
        self.readable_btn.hide()
        self._last_result = None

    def show_running(self) -> None:
        self.show()
        self.cancel_btn.show()
        for b in (self.resume_btn, self.over_btn, self.open_btn, self.play_btn, self.readable_btn):
            b.hide()
        self.stages.setAllPending()

    def on_progress(self, stage: str, state: str, done: int, total: int, message: str, eta: float) -> None:
        self.stages.update_state(stage, state, done if done > 0 else None, total if total > 0 else None)
        if state == "progress" and total > 0 and done >= 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            eta_text = f"  ·  ETA {eta:.0f} s" if eta == eta and eta >= 0 else ""
            self.counter.setText(f"{done}/{total}{eta_text}")
        elif state == "start":
            self.bar.setRange(0, 0)
        if message:
            self.log.append(f"[{stage}] {state}: {message}")

    def on_finished(self, result: api.DubResult) -> None:
        self._last_result = result
        self.cancel_btn.hide()
        if result.ok:
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.open_btn.show()
            self.play_btn.show()
            if result.out_readable:
                self.readable_btn.show()
            if result.readable_warning:
                # AGENT_UPDATE_7.md 5.5: the video is fine; say so plainly.
                self.log.append(i18n.tr("progress.readable_warning"))
            # The success summary lists what was delivered (section 5.5).
            if result.out_video:
                if result.out_readable:
                    self.log.append(
                        i18n.tr("progress.summary", video=result.out_video.name, readable=result.out_readable.name)
                    )
                else:
                    self.log.append(i18n.tr("progress.summary_no_readable", video=result.out_video.name))
            if result.readable_warning:
                self._show_readable_warning()
        else:
            self.resume_btn.show()
            self.over_btn.show()

    def _retry(self, force: bool) -> None:
        self.window_ref.start_run(self._last_mode, force=force)

    def _open_folder(self) -> None:
        if self.window_ref.output_dir:
            _open_in_explorer(self.window_ref.output_dir)

    def _play(self) -> None:
        if self._last_result and self._last_result.out_video:
            _open_file(self._last_result.out_video)

    def _open_readable(self) -> None:
        if self._last_result and self._last_result.out_readable:
            _open_file(self._last_result.out_readable)

    def _show_readable_warning(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        window = self.window_ref
        QMessageBox.warning(
            window,
            i18n.tr("progress.header"),
            i18n.tr("progress.readable_warning")
            + (f"\n{self._last_result.readable_warning}" if self._last_result and self._last_result.readable_warning else ""),
        )


# ------------------------------------------------------------------ window
class MainWindow(QMainWindow):
    """Main window (AGU4 7, reworked per AGENT_UPDATE_5.md task 4).

    The content lives inside a QScrollArea (no hard-coded minimum size),
    so small screens / high DPI scale it scrolls instead of squeezing
    widgets below their minimum sizes (the old overlap's root cause).
    When the window is at least SPLIT_MIN_WIDTH wide the generate controls
    and the progress panel form two columns; below that they stack.
    """

    SPLIT_MIN_WIDTH = 1000

    def __init__(self, service: PipelineService | None = None):
        super().__init__()
        self.setWindowTitle(i18n.tr("app.title"))
        self.service = service or PipelineService()
        self.service.progressed.connect(self._on_progress)
        self.service.finishedRun.connect(self._on_finished)
        self.video_path: Path | None = None
        saved = settings.load()
        self.output_dir = Path(saved["default_output"]) if saved.get("default_output") else None
        self._split_state: str = ""

        # AGU6 4.3: progress events coalesce to <=~10 dispatches/s; the
        # worker thread only fills a dict, the timer does the UI work.
        self._gate = ProgressGate()
        self._gate_timer = QTimer(self)
        self._gate_timer.setInterval(100)
        self._gate_timer.timeout.connect(self._flush_progress)
        self._gate_timer.start()
        self._heavy_note_shown: set[str] = set()

        # AGU6 6.7: prune old job folders at startup (setting jobs_max_days).
        # Never fatal — a corrupt jobs dir must not block the window.
        try:
            from dubtool.app import housekeeping

            housekeeping.prune_jobs(
                self._jobs_dir(), days=int(saved.get("jobs_max_days", 14) or 14)
            )
        except Exception:
            pass

        # Menu / gear (AGU4 7.7)
        menu = self.menuBar().addMenu(i18n.tr("app.settings"))
        menu.addAction(i18n.tr("app.settings"), self.open_settings)
        menu.addAction(i18n.tr("app.export_diagnostics"), self.export_diagnostics)
        menu.addAction(i18n.tr("app.about"), self.show_about)

        # Scroll area wrapping everything below the menu bar, no horizontal
        # scrollbar (AGENT_UPDATE_5.md task 4).
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        central_layout.addWidget(self.scroll)
        self.setCentralWidget(central)

        self.left_box = QWidget()
        self.left_layout = QVBoxLayout(self.left_box)
        self.left_layout.setContentsMargins(0, 0, 0, 0)

        # 1. Video (AGU4 7.1)
        self.video_group = QGroupBox(i18n.tr("video.header"))
        self.video_group.setObjectName("video_group")
        video_layout = QVBoxLayout(self.video_group)
        self.drop_zone = DropZone()
        self.drop_zone.clicked.connect(self.choose_video)
        self.drop_zone.filesDropped.connect(lambda p: self.set_video(Path(p)))
        video_layout.addWidget(self.drop_zone)
        self.video_info = QLabel("")
        self.video_info.setWordWrap(True)
        # A stable minimum: an empty-text word-wrap label has a 0 height hint,
        # and text arriving later changes hints at a moment Qt's layout cache
        # may not re-propagate (seen as a 14 px squeeze/label overlap in the
        # running/finished states). Pinning 14 px makes hint and cache agree
        # at all times (AGENT_UPDATE_5.md task 4, overlap invariant).
        self.video_info.setMinimumHeight(14)
        video_layout.addWidget(self.video_info)
        self.left_layout.addWidget(self.video_group)

        # 2. Output folder (AGU4 7.2)
        self.output_group = QGroupBox(i18n.tr("output.header"))
        self.output_group.setObjectName("output_group")
        output_layout = QGridLayout(self.output_group)
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        if self.output_dir:
            self.output_edit.setText(str(self.output_dir))
        self.output_btn = QPushButton(i18n.tr("output.choose"))
        self.output_btn.clicked.connect(self.choose_output)
        output_layout.addWidget(self.output_edit, 0, 0)
        output_layout.addWidget(self.output_btn, 0, 1)
        self.output_warn = QLabel("")
        self.output_warn.setWordWrap(True)
        self.output_warn.setMinimumHeight(14)  # stable hint, same reason as video_info
        self.output_warn.setStyleSheet("color: #a40;")
        output_layout.addWidget(self.output_warn, 1, 0, 1, 2)
        self.left_layout.addWidget(self.output_group)

        # 3. Brief (AGU4 7.3)
        self.brief_group = QGroupBox(i18n.tr("brief.header"))
        self.brief_group.setObjectName("brief_group")
        brief_group_layout = QVBoxLayout(self.brief_group)
        self.brief_edit = BriefEdit(QLabel(""))
        brief_group_layout.addWidget(self.brief_edit)
        self.brief_counter = QLabel(i18n.tr("brief.counter", n=0))
        self.brief_edit._counter = self.brief_counter
        brief_group_layout.addWidget(self.brief_counter)
        self.left_layout.addWidget(self.brief_group)

        # 4. Options (AGU4 7.4)
        self.options = OptionsWidget()
        self.left_layout.addWidget(self.options)

        # 5. Generate buttons (AGU4 7.5) in a horizontal row
        self.buttons_row = QWidget()
        self.buttons_row.setObjectName("buttons_row")
        buttons_row = QHBoxLayout(self.buttons_row)
        buttons_row.setContentsMargins(0, 0, 0, 0)
        self.fast_btn = QPushButton(i18n.tr("mode.fast"))
        self.accurate_btn = QPushButton(i18n.tr("mode.accurate"))
        self._update_mode_tips()
        self.fast_btn.clicked.connect(lambda: self.start_run("fast"))
        self.accurate_btn.clicked.connect(lambda: self.start_run("accurate"))
        buttons_row.addWidget(self.fast_btn)
        buttons_row.addWidget(self.accurate_btn)
        self.left_layout.addWidget(self.buttons_row)

        # 6. Progress (AGU4 7.6, compact per AGENT_UPDATE_5.md task 4)
        self.progress_group = ProgressWidget(self.service, self)

        self.page = QWidget()
        self.page.setObjectName("page")
        self.left_box.setObjectName("left_box")
        # One box layout flips direction between split (horizontal) and
        # stacked (vertical) modes — never rebuilt, so it is stable.
        self.page_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.page)
        self.page_layout.setContentsMargins(10, 10, 10, 10)
        self.page_layout.addWidget(self.left_box, 1)
        self.page_layout.addWidget(self.progress_group, 1)
        self._refresh_section_layout()
        self.scroll.setWidget(self.page)

        self._set_initial_size(saved)
        self._refresh_buttons()

    def _set_initial_size(self, saved: dict) -> None:
        """About 90% of the available height with sensible maximums, or the
        remembered last window size (AGENT_UPDATE_5.md task 4)."""
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else None
        max_w = geo.width() if geo is not None else 1920
        max_h = geo.height() if geo is not None else 1080
        w = min(int(max_w * 0.92), 1400)
        h = min(int(max_h * 0.9), 1000)
        last = saved.get("window_size") or []
        if isinstance(last, (list, tuple)) and len(last) == 2:
            try:
                w, h = int(last[0]), int(last[1])
            except (TypeError, ValueError):
                pass
        self.resize(QSize(max(520, min(w, max_w)), max(420, min(h, max_h))))

    def _refresh_section_layout(self) -> None:
        """Split into two columns when wide enough; single (stacked) column
        below that — the same QBoxLayout just flips its direction, so the
        layout is never rebuilt (stable reparenting)."""
        split = self.width() >= self.SPLIT_MIN_WIDTH
        state = "split" if split else "stack"
        if state == self._split_state:
            return
        self._split_state = state
        direction = (
            QBoxLayout.Direction.LeftToRight if split
            else QBoxLayout.Direction.TopToBottom
        )
        self.page_layout.setDirection(direction)
        # Horizontal mode: share the width; vertical mode: each widget keeps
        # its sizeHint height and the box grows downward (scroll handles it).
        stretch = (1, 1) if split else (0, 1)
        self.page_layout.setStretchFactor(self.left_box, stretch[0])
        self.page_layout.setStretchFactor(self.progress_group, stretch[1])

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_section_layout()

    def _update_mode_tips(self) -> None:
        """Generate-button tooltips must show the currently selected model
        id (AGENT_UPDATE_5.md task 1)."""
        try:
            current = modes.load(settings.load())
            fast = str(current["fast"]["model"])
            accurate = str(current["accurate"]["model"])
        except Exception:
            fast = accurate = ""
        self.fast_btn.setToolTip(i18n.tr("mode.fast.tip") + (f"\n{fast}" if fast else ""))
        self.accurate_btn.setToolTip(
            i18n.tr("mode.accurate.tip") + (f"\n{accurate}" if accurate else "")
        )

    # ------------------------------------------------------------ video
    def choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, i18n.tr("video.choose"), "", VIDEO_FILTERS)
        if path:
            self.set_video(Path(path))

    def set_video(self, path: Path) -> None:
        from dubtool import audio_utils

        if not path.is_file():
            return
        try:
            info = audio_utils.probe_video_info(path)
        except Exception:
            info = {}
        # A video stream means codec_name is set; audio-only files come back
        # with an empty stream dict.
        if not info.get("codec_name"):
            QMessageBox.warning(self, i18n.tr("video.header"), i18n.tr("video.no_stream"))
            return
        self.video_path = path
        duration = float(info.get("duration") or 0.0)  # seconds (one helper unit)
        if duration > 30 * 60:
            QMessageBox.information(self, i18n.tr("video.header"), i18n.tr("video.warn_long"))
        rate_text = str(info.get("r_frame_rate") or "0/1")
        num, _, den = rate_text.partition("/")
        try:
            fps = float(num) / max(1.0, float(den or "1"))
        except ValueError:
            fps = 0.0
        parts = [
            path.name,
            audio_utils.format_mmss(duration) if duration else "",
            f"{info.get('width')}x{info.get('height')}" if info.get("width") else "",
            f"{fps:.2f} fps" if fps else "",
            f"{path.stat().st_size / (1024 * 1024):.1f} MB",
        ]
        self.video_info.setText(" · ".join(p for p in parts if p))
        self.statusBar().showMessage("" if audio_utils.has_audio_stream(path) else i18n.tr("audio.no_stream_warn"))
        self._load_saved_brief(path)
        self._validate_output()
        self._refresh_buttons()

    def _load_saved_brief(self, video: Path) -> None:
        """Description remembered per video (AGU4 7.3): the app-data
        briefs.json entry (hash-keyed) first, then a legacy sidecar written
        before AGU5 task 5.2 removed sidecar writes."""
        from dubtool.brief import default_brief_path, load_video_brief

        text = load_video_brief(video)
        if not text:
            brief_path = default_brief_path(video)
            if brief_path and brief_path.exists():
                text = brief_path.read_text(encoding="utf-8")
        if text:
            self.brief_edit.setPlainText(text)

    # ------------------------------------------------------------ output
    def choose_output(self) -> None:
        start = self.output_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, i18n.tr("output.choose"), start)
        if chosen:
            self.output_dir = Path(chosen)
            self.output_edit.setText(chosen)
            s = settings.load()
            s["default_output"] = chosen
            settings.save(s)
            self._validate_output()
            self._refresh_buttons()

    def _validate_output(self) -> None:
        if not self.output_dir:
            self.output_warn.clear()
            return
        p = self.output_dir
        if not p.is_dir() or not _writable(p):
            self.output_warn.setText(i18n.tr("output.not_writable"))
            return
        if self.video_path and shutil.disk_usage(p).free < 3 * self.video_path.stat().st_size:
            self.output_warn.setText(i18n.tr("output.low_space"))
        else:
            self.output_warn.clear()

    # ------------------------------------------------------------ generate
    def _refresh_buttons(self) -> None:
        ready = bool(self.video_path and self.output_dir and not self.service.running)
        self.fast_btn.setEnabled(ready)
        self.accurate_btn.setEnabled(ready)

    def start_run(self, mode: str, force: bool = False) -> None:
        if not secrets.has_key():
            QMessageBox.information(self, i18n.tr("app.settings"), i18n.tr("mode.no_key"))
            self.open_settings()
            return
        if not first_run.ensure_shown(self):
            return
        self._validate_output()
        if self.output_warn.text():
            QMessageBox.warning(self, i18n.tr("output.header"), self.output_warn.text())
            return
        options = self.options.to_options(mode, brief=self.brief_edit.toPlainText())
        if options.description:
            # Remember per-video descriptions in app data (briefs.json keyed
            # by a hash of the video path) — nothing is written into the
            # user's video folder (AGENT_UPDATE_5.md task 5.2).
            from dubtool.brief import save_video_brief

            save_video_brief(self.video_path, options.description)
        self.progress_group._last_mode = mode
        self.progress_group.show_running()
        self.fast_btn.setEnabled(False)
        self.accurate_btn.setEnabled(False)
        started = self.service.start(
            video=self.video_path,
            output_dir=self.output_dir,
            options=options,
            secrets=secrets,
            force=force,
            jobs_dir=self._jobs_dir(),
        )
        if not started:
            self._refresh_buttons()

    def _jobs_dir(self) -> Path:
        s = settings.load()
        if s.get("jobs_dir"):
            return Path(s["jobs_dir"])
        return paths.jobs_dir()

    # ------------------------------------------------------------ progress
    def _on_progress(self, stage: str, state: str, done: int, total: int, message: str, eta: float) -> None:
        import time as _time

        for event in self._gate.feed(stage, state, done, total, message, eta, _time.monotonic()):
            self._render_progress(*event)

    def _flush_progress(self) -> None:
        import time as _time

        for event in self._gate.drain(_time.monotonic()):
            self._render_progress(*event)

    def _render_progress(self, stage: str, state: str, done: int, total: int, message: str, eta: float) -> None:
        self.progress_group.on_progress(stage, state, done, total, message, eta)
        # AGU6 4.2: a short localized note while the heavy stages run, so
        # 100% CPU in the encoder / Whisper reads as expected behavior.
        if state == "start" and stage in ("s2", "s7") and stage not in self._heavy_note_shown:
            self._heavy_note_shown.add(stage)
            self.progress_group.log.append(i18n.tr("progress.heavy_cpu_note", stage=i18n.tr(stage)))

    def _on_finished(self, result: api.DubResult) -> None:
        self.progress_group.on_finished(result)
        self._refresh_buttons()
        self._validate_output()
        if result.code == "output_exists":
            answer = QMessageBox.question(
                self,
                i18n.tr("progress.header"),
                i18n.tr("generate.conflict") + f"\n{result.out_video}",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.start_run(self.progress_group._last_mode, force=True)
        elif not result.ok and result.code != "cancelled":
            self._show_error(result)

    def _show_error(self, result: api.DubResult) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("DubTool")
        dialog.setText(errors.message_for_code(result.code))
        if result.code == "internal":
            # AGENT_UPDATE_7.md 4.3: an internal failure says plainly it is
            # not a connection problem, with details and diagnostics at hand.
            dialog.setInformativeText(i18n.tr("error.internal"))
        copy_btn = dialog.addButton(i18n.tr("copy_details"), QMessageBox.ButtonRole.ActionRole)
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(errors.detail_text(result.error, result.detail))
        )
        export_btn = dialog.addButton(
            i18n.tr("app.export_diagnostics"), QMessageBox.ButtonRole.ActionRole
        )

        def _export_diag() -> None:
            try:
                diagnostics.export_diagnostics(
                    Path.home() / "Desktop" / "dubtool-diagnostics.zip",
                    jobs_dir=self._jobs_dir(),
                )
            except Exception:
                pass

        export_btn.clicked.connect(_export_diag)
        dialog.addButton(QMessageBox.StandardButton.Ok)
        dialog.exec()

    # ------------------------------------------------------------ menu
    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.exec()
        self._update_mode_tips()

    def export_diagnostics(self) -> None:
        default = Path.home() / "Desktop" / "dubtool-diagnostics.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.tr("app.export_diagnostics"), str(default), "Zip (*.zip)"
        )
        if path:
            out = diagnostics.export_diagnostics(Path(path), jobs_dir=self._jobs_dir())
            QMessageBox.information(self, i18n.tr("app.export_diagnostics"), str(out))

    def show_about(self) -> None:
        from dubtool.app.main import _build_info_line, app_version

        build_line = _build_info_line()  # the BUILD_INFO.txt next to the exe
        QMessageBox.information(
            self,
            i18n.tr("app.about"),
            f"{i18n.tr('about.version')}: {app_version()}\n"
            + (build_line + "\n" if build_line else "")
            + f"{i18n.tr('about.ffmpeg')}: {_ffmpeg_version()}\n"
            + i18n.tr("about.license"),
        )

    # ------------------------------------------------------------ close
    def closeEvent(self, event) -> None:  # noqa: N802
        # Remember the last window size (AGENT_UPDATE_5.md task 4).
        s = settings.load()
        s["window_size"] = [self.width(), self.height()]
        try:
            settings.save(s)
        except OSError:
            pass
        if self.service.running:
            answer = QMessageBox.question(self, "DubTool", i18n.tr("confirm.close_while_running"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.service.cancel()
            self.service.wait(timeout=30)
        event.accept()
