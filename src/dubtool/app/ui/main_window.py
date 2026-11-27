"""Main window (AGU4 7). Vietnamese by default; all strings via i18n.tr().
The dub runs on the PipelineService worker thread; the UI only receives
signals, so the window stays responsive.
"""

from __future__ import annotations

import shutil
import os
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QListWidgetItem,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
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
    QSizePolicy,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dubtool import __version__, api, proc
from dubtool.app import diagnostics, errors, i18n, paths, secrets, settings
from dubtool.app.coalesce import ProgressGate
from dubtool.app.service import PipelineService
from dubtool.app.ui import first_run
from dubtool.app.ui.settings_dialog import VOICE_KEYS, SettingsDialog
from dubtool.app.ui.theme import (
    apply_win_dark_titlebar,
    render_brand_icon_pixmap,
    set_button_variant,
)
from dubtool.app.ui.widgets import DropZone, FileCard, LogView, StageList, STAGES

VIDEO_FILTERS = "Videos (*.mp4 *.mkv *.mov *.webm *.avi *.m4v)"


def _win_reveal_in_explorer(target_file: Path) -> bool:
    """Reveal and highlight ``target_file`` in Windows Explorer via the official
    Win32 Shell API (SHOpenFolderAndSelectItems).

    Calling ``explorer.exe /select,<path>`` via subprocess list args breaks
    whenever ``<path>`` contains spaces (Python quotes the whole argument as
    ``"/select,C:\\...\\New folder\\file.mp4"``), causing explorer.exe to ignore
    ``/select`` and open the user's Documents folder instead.
    """
    import ctypes
    from ctypes import wintypes

    path_str = os.path.normpath(str(target_file.resolve()))
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32

    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    shell32.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long

    hr_init = ole32.CoInitialize(None)
    need_uninit = hr_init in (0, 1)  # S_OK or S_FALSE
    pidl = ctypes.c_void_p()
    try:
        hr = shell32.SHParseDisplayName(path_str, None, ctypes.byref(pidl), 0, None)
        if hr != 0 or not pidl.value:
            return False
        hr = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
        return hr == 0
    finally:
        if pidl.value:
            ole32.CoTaskMemFree(pidl)
        if need_uninit:
            ole32.CoUninitialize()


def _open_in_explorer(path: Path, *, select: Path | None = None) -> None:
    """Open a folder in Explorer, optionally highlighting ``select``."""
    try:
        if sys.platform == "win32":
            if select is not None:
                try:
                    if _win_reveal_in_explorer(select):
                        return
                except Exception:
                    pass
            os.startfile(os.path.normpath(str(path.resolve())))  # type: ignore[attr-defined]
        else:
            from dubtool import proc

            proc.run(["xdg-open", str(path)], capture=False, gui=True)
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


_SAFE_MODEL_TOKEN_RE = re.compile(
    r"^(?:gemini|gpt|claude|llama|qwen|deepseek|gemma|whisper|vieneu)-[a-z0-9.\-]+$"
)


def _redact(text: str) -> str:
    """Redact key-shaped tokens before the details log lands on the
    clipboard (Update 9 task 3.2): any run of 20+ key characters becomes
    bullets, while preserving known lowercase model identifiers such as
    ``gemini-3-flash-preview`` or ``gemini-flash-lite-latest``."""
    def _sub(match: re.Match[str]) -> str:
        tok = match.group(0)
        if _SAFE_MODEL_TOKEN_RE.fullmatch(tok):
            return tok
        return "•••"

    return re.sub(r"[A-Za-z0-9_\-]{20,}", _sub, text)


def _make_shrinkable_combo(combo: QComboBox, min_chars: int = 10) -> None:
    """Allow a combo box with long item labels to shrink with the window.

    Without AdjustToMinimumContentsLengthWithIcon, QComboBox reports the
    widest item (e.g. a 70-char Vietnamese voice description) as its
    minimumSizeHint, pushing the QScrollArea page wider than the viewport
    and clipping the right edge of the entire UI on narrower windows.
    """
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    combo.setMinimumContentsLength(min_chars)
    combo.setMinimumWidth(0)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


# ------------------------------------------------------------------- widgets
class BriefEdit(QTextEdit):
    """Video description, max 1000 chars with a counter (AGU4 7.3)."""

    def __init__(self, counter: QLabel):
        super().__init__()
        self._counter = counter
        self.setPlaceholderText(i18n.tr("brief.placeholder"))
        self.setFixedHeight(76)
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
        # AGU10 5.2: the old 'Giữ các tệp trung gian' checkbox became a
        # three-choice dropdown.
        self.keep_combo = QComboBox()
        self.keep_combo.setObjectName("keep_level_combo")
        self.keep_combo.setAccessibleName(i18n.tr("options.keep_label"))
        _make_shrinkable_combo(self.keep_combo)
        self.keep_combo.addItem(i18n.tr("options.keep_none"), "none")
        self.keep_combo.addItem(i18n.tr("options.keep_light"), "light")
        self.keep_combo.addItem(i18n.tr("options.keep_full"), "full")
        level = str(s.get("keep_level") or "light")
        if level not in ("none", "light", "full"):
            level = "light"
        self.keep_combo.setCurrentIndex(max(0, self.keep_combo.findData(level)))
        # One-line consequence of turning this off (Update 9 task 6),
        # extended with what each choice keeps and the failed-run rule.
        self.keep_combo.setToolTip(i18n.tr("options.keep_hint"))
        # AGENT_UPDATE_7.md task 4: the customer-readable translation that
        # ships next to every exported video (default on, docx or txt).
        self.readable_check = QCheckBox(i18n.tr("options.readable"))
        self.readable_check.setChecked(bool(s.get("export_readable", True)))
        self.readable_format_combo = QComboBox()
        self.readable_format_combo.setObjectName("readable_format_combo")
        self.readable_format_combo.setAccessibleName(i18n.tr("options.readable"))
        _make_shrinkable_combo(self.readable_format_combo, min_chars=8)
        self.readable_format_combo.addItem(i18n.tr("readable.format_docx"), "docx")
        self.readable_format_combo.addItem(i18n.tr("readable.format_txt"), "txt")
        self.readable_format_combo.setCurrentIndex(
            max(0, self.readable_format_combo.findData(s.get("readable_format", "docx")))
        )
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setAccessibleName(i18n.tr("options.gain"))
        self.gain_slider.setRange(-24, -6)
        self.gain_slider.setValue(int(s["original_gain_db"]))
        self.gain_value = QLabel(f"{s['original_gain_db']} dB")
        self.gain_value.setObjectName("gain_value")
        self.gain_slider.valueChanged.connect(lambda v: self.gain_value.setText(f"{v} dB"))
        self.voice_combo = QComboBox()
        self.voice_combo.setObjectName("voice_combo")
        self.voice_combo.setAccessibleName(i18n.tr("options.voice"))
        _make_shrinkable_combo(self.voice_combo)
        self._tts_mode = str(s.get("tts_model") or "v3turbo_int8")
        self.reload_voices(s)
        from dubtool.app.voice_preview import make_preview_button

        self.preview_voice_btn = make_preview_button(
            lambda: (
                self._tts_mode,
                str(self.voice_combo.currentData() or self.voice_combo.currentText() or ""),
            ),
            parent=self,
        )
        grid.setColumnStretch(1, 1)
        self.setMinimumWidth(0)
        grid.addWidget(self.burn_check, 0, 0, 1, 3)
        grid.addWidget(self.srt_check, 1, 0, 1, 3)
        grid.addWidget(QLabel(i18n.tr("options.keep_label")), 2, 0)
        grid.addWidget(self.keep_combo, 2, 1, 1, 2)
        grid.addWidget(self.readable_check, 3, 0)
        grid.addWidget(self.readable_format_combo, 3, 1, 1, 2)
        grid.addWidget(QLabel(i18n.tr("options.gain")), 4, 0)
        grid.addWidget(self.gain_slider, 4, 1)
        grid.addWidget(self.gain_value, 4, 2)
        grid.addWidget(QLabel(i18n.tr("options.voice")), 5, 0)
        grid.addWidget(self.voice_combo, 5, 1, 1, 2)
        grid.addWidget(self.preview_voice_btn, 6, 1, 1, 2)

    def reload_voices(self, loaded_settings: dict | None = None) -> None:
        from dubtool.tts.vieneu import list_preset_voices, normalize_mode, resolve_voice

        s = loaded_settings if loaded_settings is not None else settings.load()
        self._tts_mode = normalize_mode(s.get("tts_model"))
        target = resolve_voice(s.get("voice"), self._tts_mode)
        self.voice_combo.blockSignals(True)
        try:
            self.voice_combo.clear()
            for item in list_preset_voices(self._tts_mode):
                self.voice_combo.addItem(item["label"], item["id"])
            idx = self.voice_combo.findData(target)
            self.voice_combo.setCurrentIndex(max(0, idx))
        finally:
            self.voice_combo.blockSignals(False)

    def to_options(self, model: str, brief: str) -> api.DubOptions:
        from dubtool.brief import normalize_brief
        from dubtool.tts.vieneu import DEFAULT_VOICE, normalize_mode, resolve_voice

        mode = normalize_mode(self._tts_mode)
        raw_voice = self.voice_combo.currentData() or self.voice_combo.currentText() or DEFAULT_VOICE
        return api.DubOptions(
            model=model,
            burn_subtitles=self.burn_check.isChecked(),
            save_srt=self.srt_check.isChecked(),
            original_gain_db=float(self.gain_slider.value()),
            voice=resolve_voice(str(raw_voice), mode),
            tts_mode=mode,
            keep_level=str(self.keep_combo.currentData() or "light"),
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
        line.setContentsMargins(0, 0, 0, 0)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        line.addWidget(self.bar, 1)
        self.counter = QLabel("")
        self.counter.setWordWrap(False)
        self.counter.setVisible(False)
        line.addWidget(self.counter)
        layout.addLayout(line)

        # One horizontal row for the action buttons; only applicable ones
        # are visible, so the row collapses or extends as needed.
        self.action_row = QHBoxLayout()
        self.cancel_btn = QPushButton(i18n.tr("progress.cancel"))
        set_button_variant(self.cancel_btn, "danger")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.resume_btn = QPushButton(i18n.tr("progress.resume"))
        set_button_variant(self.resume_btn, "primary")
        self.resume_btn.clicked.connect(lambda: self._retry(force=False))
        self.over_btn = QPushButton(i18n.tr("progress.start_over"))
        self.over_btn.clicked.connect(lambda: self._retry(force=True))
        self.open_btn = QPushButton(i18n.tr("progress.open_folder"))
        self.open_btn.clicked.connect(self._open_folder)
        self.play_btn = QPushButton(i18n.tr("progress.play"))
        set_button_variant(self.play_btn, "primary")
        self.play_btn.clicked.connect(self._play)
        # AGENT_UPDATE_7.md task 4: opens the readable translation file.
        self.readable_btn = QPushButton(i18n.tr("progress.open_readable"))
        self.readable_btn.clicked.connect(self._open_readable)
        for b in (
            self.cancel_btn, self.resume_btn, self.over_btn,
            self.open_btn, self.play_btn, self.readable_btn,
        ):
            b.hide()
            self.action_row.addWidget(b)
        layout.addLayout(self.action_row)

        self.log = LogView()
        self.lang_status_lbl = QLabel("")
        self.lang_status_lbl.setWordWrap(True)
        self.lang_status_lbl.setObjectName("lang_status_lbl")
        self.lang_status_lbl.setVisible(False)
        layout.addWidget(self.lang_status_lbl)
        # Update 9 task 3: the details area — header line, liveness line,
        # copy and open-log-folder buttons — all hidden while collapsed.
        self.details_header = QLabel("")
        self.details_header.setWordWrap(True)
        self.details_header.setObjectName("details_header")
        self.details_liveness = QLabel("")
        self.details_liveness.setWordWrap(True)
        self.details_liveness.setObjectName("details_liveness")
        self.setMinimumWidth(0)
        self.details_copy_btn = QPushButton(i18n.tr("details.copy"))
        self.details_copy_btn.setObjectName("details_copy")
        self.details_copy_btn.clicked.connect(self._copy_details)
        self.details_open_btn = QPushButton(i18n.tr("details.open_log"))
        self.details_open_btn.setObjectName("details_open_log")
        self.details_open_btn.clicked.connect(self._open_log_folder)
        details_row = QHBoxLayout()
        details_row.addWidget(self.details_copy_btn, 1)
        details_row.addWidget(self.details_open_btn, 1)
        for w in (self.details_header, self.details_liveness, self.details_copy_btn,
                  self.details_open_btn):
            w.setVisible(False)
        self.log.openChanged.connect(self._on_details_toggled)
        self._last_event_mono: float | None = None   # time.monotonic of last event
        self._last_stage: str | None = None
        self._last_desc = ""
        self._deadline_note = False                 # "— vẫn đang chạy" state
        self._liveness_timer = QTimer(self)
        self._liveness_timer.setInterval(1000)
        self._liveness_timer.timeout.connect(self._tick_liveness)
        self._liveness_timer.start()
        layout.addWidget(self.details_header)
        layout.addWidget(self.details_liveness)
        layout.addWidget(self.log)
        layout.addLayout(details_row)
        self.hide()

    # ------------------------------------------------- details (AGU9 task 3)
    def _on_details_toggled(self, open_: bool) -> None:
        for w in (self.details_header, self.details_liveness, self.details_copy_btn,
                  self.details_open_btn):
            w.setVisible(open_)
        if open_:
            self._tick_liveness()
        else:
            self.details_header.setText("")
            self.details_liveness.setText("")

    def is_busy(self) -> bool:
        return bool(getattr(self.window_ref, "_busy", False))

    def _tick_liveness(self) -> None:
        """Update the liveness line once per second (Update 9 task 3.2)."""
        if not self.log.is_open():
            return
        self.details_header.setText(self._header_text())
        if not self.service.running:
            self.details_liveness.setText("")
            return
        now = time.monotonic()
        if self._last_event_mono is None:
            seconds = 0
        else:
            seconds = int(now - self._last_event_mono)
        quiet_after = 20
        text = i18n.tr("details.last_update", s=seconds)
        if seconds >= quiet_after:
            thread = getattr(self.service, "_thread", None)
            if thread is not None and thread.is_alive():
                text += "  " + i18n.tr("details.still_running")
                self._deadline_note = True
            else:
                text = i18n.tr("details.last_update", s=seconds) + "  " + i18n.tr("details.worker_dead")
        else:
            self._deadline_note = False
        self.details_liveness.setText(text)
        self.details_liveness.setVisible(self.log.is_open())

    def _header_text(self) -> str:
        stage = self._last_stage
        if not stage:
            if self._last_result is not None and self._last_result.ok:
                return i18n.tr("details.done")
            return ""
        label = i18n.tr(stage) if stage in STAGES else stage
        desc = self._last_desc
        if desc and self._deadline_note:
            desc = desc.rstrip(".")
        if not desc:
            return i18n.tr("details.doing_stage", stage=label)
        return i18n.tr("details.doing", stage=label, what=desc)

    def reset_for_check(self):
        """Pristine state between layout-check/screenshot states (AGENT_UPDATE_5.md task 4)."""
        self.hide()
        self.stages.setAllPending()
        self.bar.setRange(0, 0)
        self.bar.setValue(0)
        self.counter.setText("")
        self.counter.setVisible(False)
        self.cancel_btn.setText(i18n.tr("progress.cancel"))
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.hide()
        self.resume_btn.hide()
        self.over_btn.hide()
        self.open_btn.hide()
        self.play_btn.hide()
        self.readable_btn.hide()
        self._last_result = None
        # Update 9 task 3 details state reset.
        self._last_stage = None
        self._last_desc = ""
        self._last_event_mono = None
        self._deadline_note = False
        self.details_header.setText("")
        self.details_liveness.setText("")
        self.lang_status_lbl.setText("")
        self.lang_status_lbl.setVisible(False)
        self.log.text.clear()

    def _on_cancel_clicked(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText(i18n.tr("progress.cancelling"))
        self.log.append(i18n.tr("progress.cancelling_log"))
        self.service.cancel()

    def show_running(self) -> None:
        self.show()
        self._last_result = None
        self._last_stage = None
        self._last_desc = ""
        self.lang_status_lbl.setText("")
        self.lang_status_lbl.setVisible(False)
        self.bar.setRange(0, 0)
        self.counter.setText("")
        self.counter.setVisible(False)
        self.cancel_btn.setText(i18n.tr("progress.cancel"))
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.show()
        for b in (self.resume_btn, self.over_btn, self.open_btn, self.play_btn, self.readable_btn):
            b.hide()
        self.stages.setAllPending()

    def on_progress(self, stage: str, state: str, done: int, total: int, message: str, eta: float) -> None:
        import time as _time

        self._last_event_mono = _time.monotonic()
        self.stages.update_state(stage, state, done if done > 0 else None, total if total > 0 else None)
        if state == "progress" and total > 0 and done >= 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            pct = int(round(100 * done / max(total, 1)))
            eta_text = f"  ·  ETA {eta:.0f} s" if eta == eta and eta >= 0 else ""
            self.counter.setText(f"{pct}% · {done}/{total}{eta_text}")
            self.counter.setVisible(True)
        elif state == "start":
            self.bar.setRange(0, 0)
            self.counter.setText("")
            self.counter.setVisible(False)
        # Update 9 task 3.2 header content: the stage being worked on and
        # exactly what it is doing (item count or the event message).
        if state == "start":
            self._last_stage = stage
            self._last_desc = message.splitlines()[0][:120] if message else ""
        elif state == "progress":
            self._last_stage = stage
            if total > 0 and 0 <= done:
                self._last_desc = f"{done}/{total}"
            elif message:
                self._last_desc = message.splitlines()[0][:120]
        elif state in ("done", "skipped"):
            if self._last_stage == stage:
                self._last_desc = ""
        if self.log.is_open():
            self.details_header.setText(self._header_text())
        # Timestamped activity log lines (HH:MM:SS), coalescing handled by
        # the ProgressGate upstream as before.
        stamp = _time.strftime("%H:%M:%S")
        if message:
            if message.startswith("Ngôn ngữ:") or message.startswith("Language:"):
                self.lang_status_lbl.setText(message)
                self.lang_status_lbl.setVisible(True)
            self.log.append(f"[{stamp}] [{stage}] {message}")
        elif state in ("start", "done", "error", "skipped"):
            self.log.append(f"[{stamp}] [{stage}] {state}")

    def on_finished(self, result: api.DubResult) -> None:
        self._last_result = result
        import time as _time

        self._last_event_mono = _time.monotonic()
        self._last_stage = None
        self._last_desc = ""
        self._deadline_note = False
        self.cancel_btn.setText(i18n.tr("progress.cancel"))
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.hide()
        if self.log.is_open():
            self.details_header.setText(self._header_text())
            self.details_liveness.setText("")
        if result.ok:
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.counter.setText("100%")
            self.counter.setVisible(True)
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
            self.counter.setText("")
            self.counter.setVisible(False)
            self.resume_btn.show()
            self.over_btn.show()

    def _retry(self, force: bool) -> None:
        self.window_ref.start_run(self._last_mode, force=force)

    def _copy_details(self) -> None:
        """Copy the details log, redacted (Update 9 task 3.2)."""
        QApplication.clipboard().setText(_redact(self.log.log_text()))

    def _open_log_folder(self) -> None:
        """Open the log folder with the fixed open function (task 5)."""
        target = self.window_ref._jobs_dir()
        if not target or not Path(target).is_dir():
            QMessageBox.information(self.window_ref, i18n.tr("details.open_log"), i18n.tr("progress.folder_missing"))
            return
        _open_in_explorer(target)

    def _open_folder(self) -> None:
        """Open the folder of the last successful result (Update 9 task 5),
        not the current field value the user may have changed; highlight
        the output video when it exists; message when nothing is there."""
        target: Path | None = None
        select: Path | None = None
        result = self._last_result
        if result and result.out_video is not None:
            target = result.out_video.parent
            if result.out_video.exists():
                select = result.out_video
        elif result and result.out_srt is not None:
            target = result.out_srt.parent
            if result.out_srt.exists():
                select = result.out_srt
        elif result and result.out_readable is not None:
            target = result.out_readable.parent
            if result.out_readable.exists():
                select = result.out_readable
        if target is None:
            target = self.window_ref.output_dir
        if not target or not Path(target).is_dir():
            QMessageBox.information(
                self.window_ref,
                i18n.tr("progress.open_folder"),
                i18n.tr("progress.folder_missing"),
            )
            return
        _open_in_explorer(target, select=select if (select and select.exists()) else None)

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
        self._busy = False
        self.video_path: Path | None = None
        saved = settings.load()
        # AGU8 7.3: one key — `default_output_dir` (the old `default_output`
        # was migrated on load; it is never written back).
        saved_out = saved.get("default_output_dir")
        self.output_dir = Path(saved_out) if saved_out else None
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
            # AGU10 5.3: the size cap deletes the oldest finished jobs
            # until the jobs folder is under jobs.max_total_mb. Same
            # never-fatal contract as prune_jobs.
            housekeeping.trim_to_budget(
                self._jobs_dir(),
                max_total_mb=int(saved.get("jobs_max_total_mb", 3000) or 3000),
            )
        except Exception:
            pass

        # Menu / gear (AGU4 7.7). Defer modal dialog exec() by one event-loop
        # tick via QTimer.singleShot(0, ...) so Windows UIA InvokePattern.Invoke
        # returns immediately instead of hanging COM UIA while exec() runs.
        menu = self.menuBar().addMenu(i18n.tr("app.settings"))
        menu.addAction(i18n.tr("app.settings"), lambda: QTimer.singleShot(0, self.open_settings))
        menu.addAction(
            i18n.tr("app.export_diagnostics"), lambda: QTimer.singleShot(0, self.export_diagnostics)
        )
        menu.addAction(i18n.tr("app.about"), lambda: QTimer.singleShot(0, self.show_about))

        # Scroll area wrapping everything below the menu bar, no horizontal
        # scrollbar (AGENT_UPDATE_5.md task 4).
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # Compact brand header strip aligned with README banner identity.
        self.brand_header = QFrame()
        self.brand_header.setObjectName("brand_header")
        brand_row = QHBoxLayout(self.brand_header)
        brand_row.setContentsMargins(12, 5, 12, 5)
        brand_row.setSpacing(8)
        logo_lbl = QLabel()
        logo_lbl.setObjectName("brand_logo")
        logo_pm = render_brand_icon_pixmap(44, with_badge=True)
        logo_pm.setDevicePixelRatio(2.0)
        logo_lbl.setPixmap(logo_pm)
        logo_lbl.setFixedSize(22, 22)
        title_lbl = QLabel("DubTool")
        title_lbl.setObjectName("brand_title")
        ver_lbl = QLabel(f"v{__version__}")
        ver_lbl.setObjectName("brand_version")
        brand_row.addWidget(logo_lbl)
        brand_row.addWidget(title_lbl)
        brand_row.addWidget(ver_lbl)
        brand_row.addStretch(1)
        central_layout.addWidget(self.brand_header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        central_layout.addWidget(self.scroll)
        self.setCentralWidget(central)
        self._dwm_applied = False

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
        # Update 9 task 2: the file card replaces the drop zone once a
        # video is imported, with an X that clears the selection.
        self.file_card = FileCard()
        self.file_card.removed.connect(self.clear_video)
        video_layout.addWidget(self.file_card)
        self.video_info = QLabel("")
        self.video_info.setObjectName("video_info")
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
        self.output_warn.setObjectName("output_warn")
        self.output_warn.setWordWrap(True)
        self.output_warn.setMinimumHeight(14)  # stable hint, same reason as video_info
        output_layout.addWidget(self.output_warn, 1, 0, 1, 2)
        self.left_layout.addWidget(self.output_group)

        # 3. Brief (AGU4 7.3)
        self.brief_group = QGroupBox(i18n.tr("brief.header"))
        self.brief_group.setObjectName("brief_group")
        brief_group_layout = QVBoxLayout(self.brief_group)
        self.brief_edit = BriefEdit(QLabel(""))
        brief_group_layout.addWidget(self.brief_edit)
        self.brief_counter = QLabel(i18n.tr("brief.counter", n=0))
        self.brief_counter.setObjectName("brief_counter")
        self.brief_edit._counter = self.brief_counter
        brief_group_layout.addWidget(self.brief_counter)
        self.left_layout.addWidget(self.brief_group)

        # 4. Options (AGU4 7.4)
        self.options = OptionsWidget()
        self.left_layout.addWidget(self.options)

        # 5. Language row (single source-language dropdown; starts at Auto every run)
        from dubtool import languages as lang_mod

        self.lang_video_row = QWidget()
        self.lang_video_row.setObjectName("lang_video_row")
        lang_video_layout = QHBoxLayout(self.lang_video_row)
        lang_video_layout.setContentsMargins(0, 0, 0, 0)
        lang_video_layout.addWidget(QLabel(i18n.tr("language.video_label")))
        self.lang_video_combo = QComboBox()
        _make_shrinkable_combo(self.lang_video_combo)
        self.lang_video_combo.setObjectName("lang_video_combo")
        self.lang_video_combo.setAccessibleName(i18n.tr("language.video_label"))
        ui_lang = "en" if i18n.language() == "en" else "vi"
        self.lang_video_combo.addItem(i18n.tr("language.auto"), "auto")
        for lang_code, _en, _nat, _vi in lang_mod.CURATED_LANGUAGES:
            self.lang_video_combo.addItem(lang_mod.dropdown_label(lang_code, ui_lang), lang_code)
        self.lang_video_combo.addItem(i18n.tr("language.other"), "__other__")
        self._last_lang_idx = 0
        self.lang_video_combo.activated.connect(self._on_lang_combo_activated)
        lang_video_layout.addWidget(self.lang_video_combo, 1)
        self.left_layout.addWidget(self.lang_video_row)

        # 6. Generate row (AGU4 7.5): the model dropdown and ONE Tạo button
        self.buttons_row = QWidget()
        self.buttons_row.setObjectName("buttons_row")
        buttons_row = QHBoxLayout(self.buttons_row)
        buttons_row.setContentsMargins(0, 0, 0, 0)
        self.model_combo = QComboBox()
        _make_shrinkable_combo(self.model_combo)
        self.model_combo.setEditable(False)
        self.model_combo.setToolTip("")
        self.model_combo.currentIndexChanged.connect(self._on_model_combo_changed)
        self.generate_btn = QPushButton(i18n.tr("run.generate"))
        set_button_variant(self.generate_btn, "primary")
        self.generate_btn.setMinimumHeight(34)
        self.generate_btn.clicked.connect(self.start_run)
        self._update_mode_tips()
        buttons_row.addWidget(self.model_combo, 1)
        buttons_row.addWidget(self.generate_btn, 0)
        self.left_layout.addWidget(self.buttons_row)

        # 6. Progress (AGU4 7.6, compact per AGENT_UPDATE_5.md task 4)
        self.progress_group = ProgressWidget(self.service, self)

        self.page = QWidget()
        self.page.setObjectName("page")
        self.page.setMinimumWidth(0)
        self.left_box.setObjectName("left_box")
        self.left_box.setMinimumWidth(0)
        # One box layout flips direction between split (horizontal) and
        # stacked (vertical) modes — never rebuilt, so it is stable.
        self.page_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.page)
        self.page_layout.setContentsMargins(10, 8, 10, 8)
        self.page_layout.setSpacing(10)
        self.page_layout.addWidget(self.left_box, 1)
        self.page_layout.addWidget(self.progress_group, 1)
        self._refresh_section_layout()
        self.scroll.setWidget(self.page)

        min_w = min(
            740,
            max(
                480,
                max(
                    self.left_box.minimumSizeHint().width(),
                    self.progress_group.minimumSizeHint().width(),
                ) + 44,
            ),
        )
        self.setMinimumSize(min_w, 420)
        self._set_initial_size(saved)
        self._refresh_buttons()
        # Update 9 task 3.1: restore the last details open/closed state.
        if bool(saved.get("details_open", False)):
            self.progress_group.log.set_open(True)
        self._set_details_persist(saved.get("details_open", False))
        self.progress_group.log.openChanged.connect(self._persist_details)

    @property
    def fast_btn(self) -> QPushButton:
        return self.generate_btn

    @property
    def accurate_btn(self) -> QPushButton:
        return self.generate_btn

    def _persist_details(self, open_: bool) -> None:
        """Remember the details area state across runs (Update 9 task 3.1)."""
        try:
            s = settings.load()
            if s.get("details_open", False) != open_:
                s["details_open"] = bool(open_)
                settings.save(s)
        except OSError:
            pass

    def _set_details_persist(self, value) -> None:  # marks init wiring done
        self._details_persist_value = bool(value)

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

    def _scroll_to_progress(self) -> None:
        """In single-column (stacked) layout, scroll smoothly down so the
        newly visible Progress card is immediately in view after clicking
        Generate. No-op in two-column split layout where Progress sits side
        by side with the controls."""
        if self._split_state == "split" or not self.progress_group.isVisible():
            return
        bar = self.scroll.verticalScrollBar()
        if bar is None:
            return
        target_y = max(0, self.progress_group.y() - 8)
        bar.setValue(min(bar.maximum(), target_y))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_dwm_applied", False):
            self._dwm_applied = True
            apply_win_dark_titlebar(self)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_section_layout()

    def _on_model_combo_changed(self, _index: int = -1) -> None:
        self._update_generate_tip()
        item = self.model_combo.model().item(self.model_combo.currentIndex())
        model_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""
        if model_id:
            try:
                s = settings.load()
                if s.get("model_id") != model_id:
                    s["model_id"] = model_id
                    settings.save(s)
            except OSError:
                pass

    def _update_mode_tips(self, *, preserve_current: bool = False) -> None:
        """The dropdown lists the catalog's models, grouped by provider with
        non-selectable headers: "Gemini", "OpenAI (ChatGPT)", "Anthropic
        (Claude)". A provider's items are disabled ("(cần khóa API)") when
        no key exists for it; the default selection is gemini-3.5-flash-
        lite and the last choice is remembered (settings.json: model_id).
        The Tạo button's tooltip names the provider, the model id, and
        "trả phí" when the provider is paid."""
        from dubtool.llm import catalog as catalog_mod

        current_id = ""
        if preserve_current and self.model_combo.count() > 0:
            cur_item = self.model_combo.model().item(self.model_combo.currentIndex())
            if cur_item is not None and cur_item.data(Qt.ItemDataRole.UserRole):
                current_id = str(cur_item.data(Qt.ItemDataRole.UserRole))
        saved = current_id or str(settings.load().get("model_id") or catalog_mod.DEFAULT_MODEL_ID)
        self._paid_provider_seen: set[str] = getattr(
            self, "_paid_provider_seen", set()
        )
        combo = self.model_combo
        combo.blockSignals(True)
        combo.clear()
        _PAID_SFX = i18n.tr("run.model_paid")
        _NO_KEY_SFX = i18n.tr("run.model_no_key")
        headers = {
            "auto": i18n.tr("run.provider_auto"),
            "gemini": i18n.tr("run.provider_gemini"),
            "groq": i18n.tr("run.provider_groq"),
            "openai": i18n.tr("run.provider_openai"),
            "anthropic": i18n.tr("run.provider_anthropic"),
            "ollama": "Ollama",
        }
        default_index = -1
        saved_index = -1
        for pid, entries in catalog_mod.grouped_order():
            if not entries:
                continue
            header_item = QStandardItem(headers.get(pid, pid))
            header_item.setEnabled(False)
            header_item.setSelectable(False)
            font = header_item.font()
            font.setBold(True)
            header_item.setFont(font)
            combo.model().appendRow(header_item)
            try:
                has_key = secrets.has_key(pid)
            except TypeError:
                has_key = secrets.has_key()
            for entry in entries:
                label = entry.display_label
                if entry.paid and _PAID_SFX not in label:
                    label += f" {_PAID_SFX}"
                if not has_key and _NO_KEY_SFX not in label:
                    label += f" {_NO_KEY_SFX}"
                item = QStandardItem(label)
                item.setData(entry.id, Qt.ItemDataRole.UserRole)
                if not has_key:
                    item.setEnabled(False)
                    item.setToolTip(i18n.tr(
                        "run.model_no_key_tip", provider=headers.get(pid, pid)
                    ))
                elif entry.paid:
                    item.setToolTip(i18n.tr(
                        "run.model_tip_paid",
                        provider=headers.get(pid, pid), id=entry.id,
                    ))
                else:
                    item.setToolTip(i18n.tr(
                        "run.model_tip", provider=headers.get(pid, pid), id=entry.id,
                    ))
                combo.model().appendRow(item)
                index = combo.count() - 1
                if entry.id == catalog_mod.DEFAULT_MODEL_ID:
                    default_index = index
                if entry.id == saved:
                    saved_index = index
        combo.setCurrentIndex(saved_index if saved_index >= 0 else max(0, default_index))
        combo.blockSignals(False)
        self._update_generate_tip()

    def _selected_model_entry(self):
        """The catalog entry of the dropdown's model id (None safe)."""
        model_id = self._selected_model_id()
        from dubtool.llm import catalog as catalog_mod

        return catalog_mod.get(model_id)

    def _selected_model_id(self) -> str:
        item = self.model_combo.model().item(self.model_combo.currentIndex())
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            return str(item.data(Qt.ItemDataRole.UserRole))
        from dubtool.llm import catalog as catalog_mod

        return catalog_mod.DEFAULT_MODEL_ID

    def _update_generate_tip(self) -> None:
        model_id = self._selected_model_id()
        entry = self._selected_model_entry()
        provider_name = {
            "auto": i18n.tr("run.provider_auto"),
            "gemini": i18n.tr("run.provider_gemini"),
            "groq": i18n.tr("run.provider_groq"),
            "openai": i18n.tr("run.provider_openai"),
            "anthropic": i18n.tr("run.provider_anthropic"),
            "ollama": "Ollama",
        }.get(entry.provider.lower(), entry.provider) if entry else ""
        tip = i18n.tr(
            "run.model_tip_paid" if (entry and entry.paid) else "run.model_tip",
            provider=provider_name, id=model_id,
        )
        self.model_combo.setToolTip(tip)
        self.generate_btn.setToolTip(tip)

    # ------------------------------------------------------------ video
    def choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, i18n.tr("video.choose"), "", VIDEO_FILTERS)
        if path:
            self.set_video(Path(path))

    def set_video(self, path: Path) -> None:
        if self._busy:
            # AGU9 2.3: dropping a file onto the window while busy is ignored.
            return
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
        # Update 9 task 2: the card replaces the drop zone once a video is
        # chosen; the details line below keeps duration/resolution/etc.
        self.drop_zone.hide()
        self.file_card.show_file(path.name, str(path))
        duration = float(info.get("duration") or 0.0)  # seconds (one helper unit)
        if duration > 30 * 60:
            QMessageBox.information(self, i18n.tr("video.header"), i18n.tr("video.warn_long"))
        rate_text = str(info.get("r_frame_rate") or "0/1")
        num, _, den = rate_text.partition("/")
        try:
            fps = float(num) / max(1.0, float(den or 1))
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

    def clear_video(self) -> None:
        """Update 9 task 2: the card's X deselects the video — back to the
        drop zone, Generate disabled, info reset, description cleared."""
        if self._busy:
            return
        self.video_path = None
        self.file_card.hide()
        self.drop_zone.show()
        self.video_info.clear()
        self.brief_edit.clear()
        self.statusBar().clearMessage()
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
            s["default_output_dir"] = chosen
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
        ready = bool(
            self.video_path and self.output_dir
            and not self.service.running and not self._busy
        )
        self.generate_btn.setEnabled(ready)
        self.model_combo.setEnabled(ready)

    # --------------------------------------------------- busy state (AGU9 2)
    def _busy_widgets(self) -> list:
        """AGU9 2.1 disable-list: drop zone + card X, output folder button,
        the whole options group (its own checkbox included — disabling the
        group reaches every control inside), and both generate buttons.
        The cancel/details/About/diagnostics controls are NOT in this list.
        The enumerate test in tests/test_app_ui.py sweeps the window for
        widgets forgotten here."""
        out = [self.drop_zone, self.output_btn, self.options,
               self.lang_video_combo, self.generate_btn, self.model_combo]
        if getattr(self, "file_card", None) is not None:
            out.append(self.file_card)
        return out

    def set_busy(self, busy: bool) -> None:
        """AGU9 2.1: the one busy state. Called when a run starts and
        every way it can end (success, error, cancel, close-while-cancel)."""
        self._busy = bool(busy)
        for w in self._busy_widgets():
            w.setEnabled(not self._busy)
        # AGU9 2.1: the description becomes read-only (never hidden).
        if getattr(self, "brief_edit", None) is not None:
            self.brief_edit.setReadOnly(self._busy)
        self._refresh_buttons()

    def is_busy(self) -> bool:
        return bool(getattr(self, "_busy", False))

    def _on_lang_combo_activated(self, index: int) -> None:
        code = str(self.lang_video_combo.itemData(index) or "auto")
        if code != "__other__":
            self._last_lang_idx = index
            return
        from dubtool import languages as lang_mod
        from dubtool.app.ui.language_dialogs import OtherLanguageDialog

        dlg = OtherLanguageDialog(self)
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.selected_code:
            chosen = dlg.selected_code
            existing = self.lang_video_combo.findData(chosen)
            if existing >= 0:
                self.lang_video_combo.setCurrentIndex(existing)
                self._last_lang_idx = existing
            else:
                ui_lang = "en" if i18n.language() == "en" else "vi"
                insert_at = max(1, self.lang_video_combo.count() - 1)
                self.lang_video_combo.insertItem(insert_at, lang_mod.dropdown_label(chosen, ui_lang), chosen)
                self.lang_video_combo.setCurrentIndex(insert_at)
                self._last_lang_idx = insert_at
        else:
            self.lang_video_combo.setCurrentIndex(self._last_lang_idx)

    def start_run(self, _checked: bool = False, force: bool = False) -> None:
        model_id = self._selected_model_id()
        entry = self._selected_model_entry()
        provider = (entry.provider if entry else "gemini").lower()
        if not secrets.has_key(provider):
            QMessageBox.information(
                self, i18n.tr("app.settings"),
                i18n.tr("run.no_key", provider=self._provider_label(provider)),
            )
            self.open_settings("gemini" if provider == "auto" else provider)
            return
        if entry and entry.paid and provider not in self._paid_provider_seen:
            s = settings.load()
            already_ok = provider in list(s.get("paid_notice_ok", []))
            if already_ok:
                self._paid_provider_seen.add(provider)
            elif self._paid_provider_confirm(provider):
                return
            else:
                self._paid_provider_seen.add(provider)
        if not first_run.ensure_shown(self):
            return
        self._validate_output()
        if self.output_warn.text():
            QMessageBox.warning(self, i18n.tr("output.header"), self.output_warn.text())
            return
        options = self.options.to_options(model_id, brief=self.brief_edit.toPlainText())
        sel_lang = str(self.lang_video_combo.currentData() or "auto")
        options.video_language = "auto" if sel_lang == "__other__" else sel_lang
        # The selected model id persists for the next run.
        try:
            s = settings.load()
            s["model_id"] = model_id
            settings.save(s)
        except OSError:
            pass
        if options.description:
            # Remember per-video descriptions in app data (briefs.json keyed
            # by a hash of the video path) — nothing is written into the
            # user's video folder (AGENT_UPDATE_5.md task 5.2).
            from dubtool.brief import save_video_brief

            save_video_brief(self.video_path, options.description)
        self.progress_group._last_mode = "generate"
        self.progress_group.show_running()
        self.set_busy(True)
        QTimer.singleShot(0, self._scroll_to_progress)
        from dubtool.app.ui.fillers_dialog import (
            DialogBridge, FillersDialog, OverloadDialog,
        )

        prev_bridge = getattr(self, "_active_dialog_bridge", None)
        if prev_bridge is not None:
            try:
                prev_bridge.deleteLater()
            except RuntimeError:
                pass
        bridge = DialogBridge(self, cancel_event=getattr(self.service, "_cancel", None))  # lives on the GUI thread
        self._active_dialog_bridge = bridge

        def _run_modal(factory, extract=lambda dlg: dlg.choice):
            def _open():
                dlg = factory()
                dlg.exec()
                return extract(dlg)

            return bridge.call(_open)

        def _fillers_dialog(suggestions):
            from dubtool import fillers as fillers_mod

            defaults_map = {
                **{k.lower(): str(v) for k, v in (fillers_mod.load_settings().get("vocative") or {}).items()},
                **{k.lower(): str(v) for k, v in (fillers_mod.load_settings().get("discourse") or {}).items()},
            }
            return _run_modal(
                lambda: FillersDialog(suggestions, parent=self, defaults=defaults_map),
                lambda dlg: dlg.result_choices(),
            )

        def _overload_dialog(info: dict | None = None):
            # AGU11 2.3: the dialog names the currently failing model and
            # how long it has been trying.
            model = str((info or {}).get("model") or options.model)
            waited = int(float((info or {}).get("waited_s") or 0))
            return _run_modal(lambda: OverloadDialog(model=model, parent=self, waited_s=waited))

        def _language_dialog(candidates: list[dict], mixed: bool = False):
            from dubtool.app.ui.language_dialogs import LanguageUncertainDialog

            return _run_modal(
                lambda: LanguageUncertainDialog(candidates, mixed=mixed, parent=self)
            )

        started = self.service.start(
            video=self.video_path,
            output_dir=self.output_dir,
            options=options,
            secrets=secrets,
            force=force,
            jobs_dir=self._jobs_dir(),
            overload_dialog=_overload_dialog,
            fillers_dialog=_fillers_dialog,
            language_dialog=_language_dialog,
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
        self.set_busy(False)          # AGU9 2.1: also after success/error/cancel
        self.progress_group.on_finished(result)
        self._refresh_buttons()
        self._validate_output()
        if result.code == "source_is_vietnamese":
            self._handle_vietnamese_source(result)
        elif result.code == "output_exists":
            answer = QMessageBox.question(
                self,
                i18n.tr("progress.header"),
                i18n.tr("generate.conflict") + f"\n{result.out_video}",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.start_run(force=True)
        elif not result.ok and result.code != "cancelled":
            # Update 9 task 3.1: a run ending in error opens the details
            # panel automatically.
            self.progress_group.log.set_open(True)
            self._show_error(result)

    def _handle_vietnamese_source(self, result: api.DubResult) -> None:
        from dubtool.app.ui.language_dialogs import VietnameseSourceDialog

        sl = result.source_language or {}
        prob = float(sl.get("probability") or 1.0)
        dlg = VietnameseSourceDialog(prob, parent=self)
        dlg.exec()
        if dlg.choice == "subtitles" and result.job_dir and self.output_dir:
            from dubtool.stages import s8_export_readable as s8

            base = (self.video_path.stem if self.video_path else "video")
            try:
                srt_p, txt_p = s8.export_subtitles_only(result.job_dir, self.output_dir, base)
                self.progress_group.log.append(
                    i18n.tr("language.vi_subtitles_done", srt=srt_p.name, txt=txt_p.name)
                )
                result.out_srt = srt_p
                result.out_readable = txt_p
                self.progress_group._last_result = result
                self.progress_group.open_btn.show()
                self.progress_group.readable_btn.show()
            except Exception as exc:
                self.progress_group.log.append(f"Error exporting subtitles: {exc}")

    def _show_error(self, result: api.DubResult) -> None:
        def _export_diag() -> None:
            try:
                diagnostics.export_diagnostics(
                    Path.home() / "Desktop" / "dubtool-diagnostics.zip",
                    jobs_dir=self._jobs_dir(),
                )
            except Exception:
                pass

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("DubTool")
        if result.code == "tts_unavailable":
            # AGU8 2.4: the dedicated error gets exactly Resume (keeps every
            # finished clip) and Open settings (voice / backup voice); never
            # the generic message or generic buttons for this case.
            # AGU9 10.7: with a large measured clock offset, one hint line
            # rides along (a possibility, not a cause).
            tts_msg = errors.message_for_code("tts_unavailable")
            try:
                from dubtool import netcheck as _netcheck

                off = _netcheck.large_offset()
                if off is not None:
                    tts_msg += "\n" + i18n.tr("error.clock_hint", n=int(round(abs(off))))
            except Exception:
                pass
            dialog.setText(tts_msg)
            resume_btn = dialog.addButton(
                i18n.tr("error.button.resume"), QMessageBox.ButtonRole.AcceptRole
            )
            settings_btn = dialog.addButton(
                i18n.tr("error.button.open_settings"), QMessageBox.ButtonRole.ActionRole
            )
            dialog.exec()
            if dialog.clickedButton() == settings_btn:
                self.open_settings()
            elif dialog.clickedButton() == resume_btn:
                self.start_run(force=False)
            return
        dialog.setText(errors.message_for_code(result.code))
        if result.code == "internal":
            # AGENT_UPDATE_7.md 4.3: an internal failure says plainly it is
            # not a connection problem, with details and diagnostics at hand.
            dialog.setInformativeText(i18n.tr("error.internal"))
        copy_btn = dialog.addButton(i18n.tr("copy_details"), QMessageBox.ButtonRole.ActionRole)
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(
                _redact(errors.detail_text(result.error, result.detail))
            )
        )
        export_btn = dialog.addButton(
            i18n.tr("app.export_diagnostics"), QMessageBox.ButtonRole.ActionRole
        )
        export_btn.clicked.connect(_export_diag)
        dialog.addButton(QMessageBox.StandardButton.Ok)
        dialog.exec()

    # ------------------------------------------------------------ menu
    def open_settings(self, provider: str | None = None) -> None:
        # Update 9 task 2.2: during a run the dialog opens read-only with a
        # banner, and re-enables itself if the run ends while it is open.
        # `provider` (when given) opens Settings at that provider's keys.
        dialog = SettingsDialog(
            self,
            read_only=self._busy,
            busy_provider=None if not self._busy else self.is_busy,
            initial_provider=provider,
        )
        dialog.exec()
        if hasattr(self, "options") and hasattr(self.options, "reload_voices"):
            self.options.reload_voices()
        self._update_mode_tips(preserve_current=True)

    def _provider_label(self, provider: str) -> str:
        return {
            "auto": i18n.tr("run.provider_auto"),
            "gemini": i18n.tr("run.provider_gemini"),
            "groq": i18n.tr("run.provider_groq"),
            "openai": i18n.tr("run.provider_openai"),
            "anthropic": i18n.tr("run.provider_anthropic"),
            "ollama": "Ollama",
        }.get(provider, provider)

    def _paid_provider_confirm(self, provider: str) -> bool:
        """The one confirmation before the first paid-provider run: what is
        sent (the video's English text), what it costs (tokens, on the
        user's own account), and a 'Không hỏi lại' checkbox. True = abort
        the run; (True, checked) is folded into _paid_provider_seen."""
        from dubtool.app.ui.paid_notice import PaidProviderConfirmDialog

        dialog = PaidProviderConfirmDialog(self, self._provider_label(provider))
        result = dialog.exec()
        remember = bool(dialog.dont_ask.isChecked())
        if result == 1 and remember:
            s = settings.load()
            seen = list(s.get("paid_notice_ok", []))
            if provider not in seen:
                seen.append(provider)
                s["paid_notice_ok"] = seen
                settings.save(s)
        return result != 1  # 1 = run; anything else = abort

    def export_diagnostics(self) -> None:
        default = Path.home() / "Desktop" / "dubtool-diagnostics.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.tr("app.export_diagnostics"), str(default), "Zip (*.zip)"
        )
        if not path:
            return
        jobs_root = self._jobs_dir()
        target = Path(path)

        from dubtool.app.ui.settings_dialog import _WorkResult

        work = _WorkResult(self)
        self._diag_work = work

        def _done(payload) -> None:
            if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "__error__":
                QMessageBox.warning(
                    self, i18n.tr("app.export_diagnostics"), _redact(str(payload[1]))
                )
            else:
                QMessageBox.information(self, i18n.tr("app.export_diagnostics"), str(payload))
            try:
                work.deleteLater()
            except RuntimeError:
                pass

        work.finished.connect(_done)
        work.fire(lambda: diagnostics.export_diagnostics(target, jobs_dir=jobs_root))

    def show_about(self) -> None:
        # Update 9 task 4: version (1.0.<update>), build id, the author,
        # and the licenses pointer. The old FFmpeg line is gone.
        from dubtool import branding
        from dubtool.app.main import _build_info_line, app_version

        build_line = _build_info_line()  # the BUILD_INFO.txt next to the exe
        QMessageBox.information(
            self,
            i18n.tr("app.about"),
            f"DubTool\n{i18n.tr('about.version')}: {app_version()}\n"
            + (build_line + "\n" if build_line else "")
            + f"{i18n.tr('about.author')}: {branding.AUTHOR}\n"
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
            # Pump Qt events while waiting for the worker thread so any pending
            # DialogBridge BlockingQueuedConnection cannot deadlock closeEvent.
            deadline = time.monotonic() + 30.0
            while self.service.running and time.monotonic() < deadline:
                QApplication.processEvents()
                self.service.wait(timeout=0.05)
        event.accept()
