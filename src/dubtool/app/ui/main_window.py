"""Main window (AGU4 7). Vietnamese by default; all strings via i18n.tr().
The dub runs on the PipelineService worker thread; the UI only receives
signals, so the window stays responsive.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
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
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dubtool import api
from dubtool.app import diagnostics, errors, i18n, paths, secrets, settings
from dubtool.app.service import PipelineService
from dubtool.app.ui import first_run
from dubtool.app.ui.settings_dialog import VOICE_KEYS, SettingsDialog
from dubtool.app.ui.widgets import DropZone, LogView, StageList

VIDEO_FILTERS = "Videos (*.mp4 *.mkv *.mov *.webm *.avi *.m4v)"


def _open_in_explorer(path: Path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def _open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
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

        out = subprocess.run(
            [audio_utils.ffmpeg_bin(), "-version"], capture_output=True, text=True, timeout=30
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
        self.textChanged.connect(self._on_change)
        self._on_change()

    def _on_change(self) -> None:
        if self.toPlainText().__len__() > 1000:
            cursor = self.textCursor()
            cursor.setPosition(1000)
            cursor.movePosition(1, 998, False)  # keep a cursor, drop the rest
            self.setPlainText(self.toPlainText()[:1000])
        self._counter.setText(i18n.tr("brief.counter", n=len(self.toPlainText())))


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
        grid.addWidget(QLabel(i18n.tr("options.gain")), 3, 0)
        grid.addWidget(self.gain_slider, 3, 1)
        grid.addWidget(self.gain_value, 3, 2)
        grid.addWidget(QLabel(i18n.tr("options.voice")), 4, 0)
        grid.addWidget(self.voice_combo, 4, 1, 1, 2)

    def to_options(self, mode: str, brief: str) -> api.DubOptions:
        from dubtool.brief import normalize_brief

        return api.DubOptions(
            mode=mode,
            burn_subtitles=self.burn_check.isChecked(),
            save_srt=self.srt_check.isChecked(),
            original_gain_db=float(self.gain_slider.value()),
            voice=self.voice_combo.currentText(),
            keep_intermediate=self.keep_check.isChecked(),
            description=(normalize_brief(brief) or None),
        )


class ProgressWidget(QGroupBox):
    """Stage list, bar, counter, cancel, resume/over, open/play, log tail."""

    def __init__(self, service: PipelineService, window: "MainWindow"):
        super().__init__(i18n.tr("progress.header"))
        self.service = service
        self.window_ref = window
        self._last_result: api.DubResult | None = None
        self._last_mode = "fast"
        layout = QVBoxLayout(self)
        self.stages = StageList()
        layout.addWidget(self.stages)
        self.bar = QProgressBar()
        layout.addWidget(self.bar)
        self.counter = QLabel("")
        layout.addWidget(self.counter)
        self.cancel_btn = QPushButton(i18n.tr("progress.cancel"))
        self.cancel_btn.clicked.connect(service.cancel)
        layout.addWidget(self.cancel_btn)
        self.resume_btn = QPushButton(i18n.tr("progress.resume"))
        self.resume_btn.clicked.connect(lambda: self._retry(force=False))
        self.over_btn = QPushButton(i18n.tr("progress.start_over"))
        self.over_btn.clicked.connect(lambda: self._retry(force=True))
        self.open_btn = QPushButton(i18n.tr("progress.open_folder"))
        self.open_btn.clicked.connect(self._open_folder)
        self.play_btn = QPushButton(i18n.tr("progress.play"))
        self.play_btn.clicked.connect(self._play)
        for b in (self.resume_btn, self.over_btn, self.open_btn, self.play_btn):
            b.hide()
            layout.addWidget(b)
        self.log = LogView()
        layout.addWidget(self.log)
        self.hide()

    def show_running(self) -> None:
        self.show()
        self.cancel_btn.show()
        for b in (self.resume_btn, self.over_btn, self.open_btn, self.play_btn):
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


# ------------------------------------------------------------------ window
class MainWindow(QMainWindow):
    def __init__(self, service: PipelineService | None = None):
        super().__init__()
        self.setWindowTitle(i18n.tr("app.title"))
        self.setMinimumSize(760, 680)
        self.service = service or PipelineService()
        self.service.progressed.connect(self._on_progress)
        self.service.finishedRun.connect(self._on_finished)
        self.video_path: Path | None = None
        saved = settings.load()
        self.output_dir = Path(saved["default_output"]) if saved.get("default_output") else None

        central = QWidget()
        layout = QVBoxLayout(central)

        # Menu / gear (AGU4 7.7)
        menu = self.menuBar().addMenu(i18n.tr("app.settings"))
        menu.addAction(i18n.tr("app.settings"), self.open_settings)
        menu.addAction(i18n.tr("app.export_diagnostics"), self.export_diagnostics)
        menu.addAction(i18n.tr("app.about"), self.show_about)

        # 1. Video (AGU4 7.1)
        self.video_group = QGroupBox(i18n.tr("video.header"))
        video_layout = QVBoxLayout(self.video_group)
        self.drop_zone = DropZone()
        self.drop_zone.clicked.connect(self.choose_video)
        self.drop_zone.filesDropped.connect(lambda p: self.set_video(Path(p)))
        video_layout.addWidget(self.drop_zone)
        self.video_info = QLabel("")
        self.video_info.setWordWrap(True)
        video_layout.addWidget(self.video_info)
        layout.addWidget(self.video_group)

        # 2. Output folder (AGU4 7.2)
        self.output_group = QGroupBox(i18n.tr("output.header"))
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
        self.output_warn.setStyleSheet("color: #a40;")
        output_layout.addWidget(self.output_warn, 1, 0, 1, 2)
        layout.addWidget(self.output_group)

        # 3. Brief (AGU4 7.3)
        layout.addWidget(QLabel(i18n.tr("brief.header")))
        brief_row = QVBoxLayout()
        self.brief_edit = BriefEdit(QLabel(""))
        brief_row.addWidget(self.brief_edit)
        self.brief_counter = QLabel(i18n.tr("brief.counter", n=0))
        self.brief_edit._counter = self.brief_counter  # BriefEdit updates this label
        brief_row.addWidget(self.brief_counter)
        layout.addLayout(brief_row)

        # 4. Options (AGU4 7.4)
        self.options = OptionsWidget()
        layout.addWidget(self.options)

        # 5. Generate buttons (AGU4 7.5)
        buttons_row = QHBoxLayout()
        self.fast_btn = QPushButton(i18n.tr("mode.fast"))
        self.accurate_btn = QPushButton(i18n.tr("mode.accurate"))
        self.fast_btn.setToolTip(i18n.tr("mode.fast.tip"))
        self.accurate_btn.setToolTip(i18n.tr("mode.accurate.tip"))
        self.fast_btn.clicked.connect(lambda: self.start_run("fast"))
        self.accurate_btn.clicked.connect(lambda: self.start_run("accurate"))
        buttons_row.addWidget(self.fast_btn)
        buttons_row.addWidget(self.accurate_btn)
        layout.addLayout(buttons_row)
        self._refresh_buttons()

        # 6. Progress (AGU4 7.6)
        self.progress_group = ProgressWidget(self.service, self)
        layout.addWidget(self.progress_group)
        layout.addStretch(1)

        self.setCentralWidget(central)

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
        duration = float(info.get("duration") or 0.0)
        if duration > 30 * 60:
            QMessageBox.information(self, i18n.tr("video.header"), i18n.tr("video.warn_long"))
        parts = [
            path.name,
            f"{duration / 60:.1f} min" if duration else "",
            f"{info.get('width')}x{info.get('height')}" if info.get("width") else "",
            f"{info.get('fps', 0):.2f} fps" if info.get("fps") else "",
            f"{path.stat().st_size / (1024 * 1024):.1f} MB",
        ]
        self.video_info.setText(" · ".join(p for p in parts if p))
        self.statusBar().showMessage("" if audio_utils.has_audio_stream(path) else i18n.tr("audio.no_stream_warn"))
        self._load_saved_brief(path)
        self._validate_output()
        self._refresh_buttons()

    def _load_saved_brief(self, video: Path) -> None:
        """Description remembered per video (AGU4 7.3): sidecar <video>.brief.txt,
        the same file the CLI reads."""
        from dubtool.brief import default_brief_path

        brief_path = default_brief_path(video)
        if brief_path and brief_path.exists():
            self.brief_edit.setPlainText(brief_path.read_text(encoding="utf-8"))

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
            # Remember the description side-by-side with the video so the CLI
            # path sees the same brief (AGU4 7.3).
            sidecar = self.video_path.parent / f"{self.video_path.stem}.brief.txt"
            sidecar.write_text(options.description, encoding="utf-8")
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
        self.progress_group.on_progress(stage, state, done, total, message, eta)

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
        copy_btn = dialog.addButton(i18n.tr("copy_details"), QMessageBox.ButtonRole.ActionRole)
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(errors.detail_text(result.error, result.detail))
        )
        dialog.addButton(QMessageBox.StandardButton.Ok)
        dialog.exec()

    # ------------------------------------------------------------ menu
    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.exec()

    def export_diagnostics(self) -> None:
        default = Path.home() / "Desktop" / "dubtool-diagnostics.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.tr("app.export_diagnostics"), str(default), "Zip (*.zip)"
        )
        if path:
            out = diagnostics.export_diagnostics(Path(path), jobs_dir=self._jobs_dir())
            QMessageBox.information(self, i18n.tr("app.export_diagnostics"), str(out))

    def show_about(self) -> None:
        from dubtool.app.main import app_version

        QMessageBox.information(
            self,
            i18n.tr("app.about"),
            f"{i18n.tr('about.version')}: {app_version()}\n"
            f"{i18n.tr('about.ffmpeg')}: {_ffmpeg_version()}\n"
            + i18n.tr("about.license"),
        )

    # ------------------------------------------------------------ close
    def closeEvent(self, event) -> None:  # noqa: N802
        if self.service.running:
            answer = QMessageBox.question(self, "DubTool", i18n.tr("confirm.close_while_running"))
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.service.cancel()
            self.service.wait(timeout=30)
        event.accept()
