"""App entry point (AGU4 6.2 / 11.1).

Flags usable from source and from the packaged exe:
  DubTool.exe                 -> the GUI
  --version                   -> app + bundled FFmpeg version
  --selftest [--json out.json]
  --screenshots <dir>         -> offscreen PNGs of the UI states
  --headless-run <video> <outdir> --mode fast|accurate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

APP_VERSION = "1.0.0"


def app_version() -> str:
    return APP_VERSION


def _print_version() -> None:
    from dubtool.app.ui.main_window import _ffmpeg_version

    print(f"DubTool {app_version()}")
    print(_ffmpeg_version())


def run_gui() -> int:
    from PySide6.QtWidgets import QApplication

    from dubtool.app import i18n, settings
    from dubtool.app.paths import ensure_user_dirs
    from dubtool.app.ui.main_window import MainWindow

    ensure_user_dirs()
    s = settings.load()
    i18n.set_language(s.get("language") or "vi")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("DubTool")
    app.setApplicationVersion(app_version())
    window = MainWindow()
    window.show()
    return app.exec()


def run_screenshots(out_dir: Path) -> int:
    """Render the UI states offscreen (AGU4 11.1). No key ever appears: the
    key field is blank and the key status text cannot contain key material."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMainWindow

    from dubtool.app import i18n, secrets, settings
    from dubtool.app.paths import ensure_user_dirs
    from dubtool.app.service import PipelineService
    from dubtool.app.ui.main_window import MainWindow
    from dubtool.app.ui.settings_dialog import SettingsDialog
    from dubtool.app.ui.first_run import PrivacyDialog

    ensure_user_dirs()
    if not secrets.has_key():
        settings.load()  # untouched; key state stays honest
    i18n.set_language("vi")
    app = QApplication.instance() or QApplication([])
    had_key = secrets.has_key()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def snap(widget, name: str) -> None:
        widget.resize(widget.sizeHint())
        widget.show()
        widget.adjustSize()  # content added after the first show (progress area)
        if isinstance(widget, QMainWindow):
            widget.resize(780, 780)  # room for the stage list + log view
        app.processEvents()
        widget.grab().save(str(out_dir / name))
        widget.hide()

    def grab_window(name: str) -> None:
        window.adjustSize()  # content added after the first show (progress area)
        window.resize(780, 1180)  # room for the stage list + log view
        window.show()
        app.processEvents()
        window.grab().save(str(out_dir / name))

    def grab_dialog(widget, name: str) -> None:
        widget.show()
        widget.adjustSize()
        app.processEvents()
        widget.grab().save(str(out_dir / name))
        widget.hide()

    window = MainWindow()
    window.service.running = False
    window.service._thread = None

    # 1. empty
    snap(window, "01_empty.png")

    # 2. video selected (probe skipped when no sample video is available)
    sample = Path("sample/input/test1.mp4")
    if sample.exists() and secrets.has_key() is not None:
        try:
            window.set_video(sample)
        except Exception:
            pass
    window.show()
    grab_window("02_video_selected.png")

    # 3. ready (video + folder + key set) — emulate, without a real key read
    window.output_edit.setText(str(Path.home() / "Desktop"))
    window.output_dir = Path.home() / "Desktop"
    window._validate_output()
    window._refresh_buttons()
    grab_window("03_ready.png")

    # 4. running (fake service -> stage list with progress)
    window.service.running = True
    window.progress_group.show_running()
    window._refresh_buttons()
    events = [
        ("s1", "start", -1, -1, ""),
        ("s1", "done", -1, -1, ""),
        ("s2", "start", -1, -1, ""),
        ("s2", "progress", 40, 92, ""),
        ("s3", "start", -1, -1, ""),
        ("s3", "progress", 2, 5, ""),
    ]
    for stage, state, done, total, msg in events:
        window._on_progress(stage, state, done, total, msg, 12.0)
    grab_window("04_running.png")
    window.service.running = False

    # 5. finished
    from dubtool.api import DubResult

    window.progress_group.on_finished(DubResult(ok=True, code="ok", out_video=Path("out/sample_vi.mp4")))
    grab_window("05_finished.png")

    # 6. error dialog
    from dubtool.app import errors as _errors

    import PySide6.QtWidgets as QW

    dialog = QW.QMessageBox(window)
    dialog.setIcon(QW.QMessageBox.Icon.Critical)
    dialog.setWindowTitle("DubTool")
    dialog.setText(_errors.message_for_code("quota"))
    dialog.show()
    app.processEvents()
    dialog.adjustSize()
    dialog.grab().save(str(out_dir / "06_error.png"))
    dialog.hide()

    # 7. settings
    settings_dialog = SettingsDialog(window)
    settings_dialog.show()
    settings_dialog.adjustSize()
    app.processEvents()
    settings_dialog.grab().save(str(out_dir / "07_settings.png"))

    # 8. privacy notice
    privacy = PrivacyDialog(window)
    privacy.show()
    privacy.adjustSize()
    app.processEvents()
    privacy.grab().save(str(out_dir / "08_first_run.png"))

    print(f"screenshots written to {out_dir}")
    return 0


def run_headless(video: Path, outdir: Path, mode: str) -> int:
    """Automated end-to-end verification without a window (AGU4 11.1)."""
    import threading

    from dubtool import api, secrets
    from dubtool.api import DubOptions
    from dubtool.app import i18n

    options = DubOptions(mode=mode)

    def show(event: api.ProgressEvent) -> None:
        line = f"{event.stage} {event.state}"
        if event.done is not None and event.total:
            line += f" {event.done}/{event.total}"
        if event.elapsed_s is not None:
            line += f" ({event.elapsed_s:.1f}s)"
        print(line, flush=True)

    result = api.run_dub(
        Path(video),
        Path(outdir),
        options=options,
        secrets=secrets,
        progress=show,
        cancel=threading.Event(),
    )
    print(
        f"result: code={result.code} ok={result.ok} out={result.out_video}"
        + (f" error={result.error}" if result.error else "")
    )
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="DubTool")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--json", default=None, help="--selftest output file")
    parser.add_argument("--screenshots", default=None, metavar="DIR")
    parser.add_argument("--headless-run", nargs=2, metavar=("VIDEO", "OUTDIR"))
    parser.add_argument("--mode", choices=("fast", "accurate"), default="fast")
    args = parser.parse_args(argv)

    if args.version:
        _print_version()
        return 0
    if args.selftest:
        from dubtool.app.selftest import run_selftest

        return run_selftest(Path(args.json) if args.json else None)
    if args.screenshots:
        return run_screenshots(Path(args.screenshots))
    if args.headless_run:
        return run_headless(Path(args.headless_run[0]), Path(args.headless_run[1]), args.mode)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
