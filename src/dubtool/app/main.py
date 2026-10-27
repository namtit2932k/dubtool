"""App entry point (AGU4 6.2 / 11.1).

Flags usable from source and from the packaged exe:
  DubTool.exe                 -> the GUI
  --version                   -> app + bundled FFmpeg version
  --selftest [--json out.json]
  --layout-check [--scale S]  -> geometry invariant sweep, table to stdout
  --screenshots <dir> [--scale S] -> offscreen PNGs of the UI states
  --headless-run <video> <outdir> --mode fast|accurate
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

APP_VERSION = "1.0.0"

SHOT_SIZES = [(760, 600), (900, 700), (1100, 800)]


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


def run_layout_check(scale: float = 1.0) -> int:
    """The AGU5 task 4 sweep, from source or the packaged exe. Uses the
    normal window platform (real fonts); pass a scale for the 125/150/175%
    passes (separate processes)."""
    from dubtool.app.layout_check import run_layout_check as _run

    return _run(scale=scale)


def run_screenshots(out_dir: Path, scale: float = 1.0) -> int:
    """Render the UI states offscreen (AGU4 11.1, extended by AGU5 task 4):
    every window state at three sizes, then dialogs and each Settings tab.
    Any scale other than 1.0 must be passed in a fresh process (it has to
    be set before the Qt platform exists). No key ever appears: the key
    field is blank and the key status text cannot contain key material.
    """
    if scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(scale)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication

    from dubtool.app import i18n, settings
    from dubtool.app.layout_check import apply_state
    from dubtool.app.paths import ensure_user_dirs
    from dubtool.app.service import PipelineService
    from dubtool.app.ui.first_run import PrivacyDialog
    from dubtool.app.ui.main_window import MainWindow
    from dubtool.app.ui.settings_dialog import SettingsDialog

    ensure_user_dirs()
    settings.load()  # key state stays honest
    i18n.set_language("vi")
    app = QApplication.instance() or QApplication([])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    window = MainWindow(PipelineService())
    window.service.running = False
    window.service._thread = None

    # Window states at three sizes: <size>_<state>.png
    for size in SHOT_SIZES:
        window.resize(QSize(size[0], size[1]))
        app.processEvents()
        for state in ("idle", "video", "running", "finished", "error"):
            apply_state(window, state)
            app.processEvents()
            window.grab().save(str(out_dir / f"{size[0]}x{size[1]}_{state}.png"))
    window.hide()

    # Error dialog
    from dubtool.app import errors as _errors

    import PySide6.QtWidgets as QW

    dialog = QW.QMessageBox()
    dialog.setIcon(QW.QMessageBox.Icon.Critical)
    dialog.setWindowTitle("DubTool")
    dialog.setText(_errors.message_for_code("quota"))
    dialog.show()
    app.processEvents()
    dialog.adjustSize()
    dialog.grab().save(str(out_dir / "dialog_error.png"))
    dialog.hide()

    # Tabbed Settings dialog: whole dialog + each tab
    settings_dialog = SettingsDialog()
    settings_dialog.show()
    app.processEvents()
    settings_dialog.adjustSize()
    settings_dialog.grab().save(str(out_dir / "dialog_settings.png"))
    for index in range(settings_dialog.tabs.count()):
        settings_dialog.tabs.setCurrentIndex(index)
        app.processEvents()
        tab_name = settings_dialog.tabs.tabText(index).replace("/", "-").replace(" ", "_")
        settings_dialog.grab().save(str(out_dir / f"dialog_settings_tab{index}_{tab_name}.png"))
    settings_dialog.close()

    # Privacy notice
    privacy = PrivacyDialog()
    privacy.show()
    privacy.adjustSize()
    app.processEvents()
    privacy.grab().save(str(out_dir / "dialog_first_run.png"))
    privacy.close()

    print(f"screenshots written to {out_dir}")
    return 0


def run_headless(video: Path, outdir: Path, mode: str, profile: bool = False) -> int:
    """Automated end-to-end verification without a window (AGU4 11.1).
    With `profile` (AGU6 4.1), a 1 Hz sampler writes profile.csv in the job
    directory: elapsed, stage, app/children/system CPU%%, live children."""
    import threading

    from dubtool import api
    from dubtool.app import secrets
    from dubtool.api import DubOptions

    options = DubOptions(mode=mode)
    current_stage = {"name": ""}

    def show(event) -> None:
        current_stage["name"] = event.stage
        line = f"{event.stage} {event.state}"
        if event.done is not None and event.total:
            line += f" {event.done}/{event.total}"
        if event.elapsed_s is not None:
            line += f" ({event.elapsed_s:.1f}s)"
        print(line, flush=True)

    sampler = None
    rows = None
    if profile:
        from dubtool.app.profile import ProfileSampler

        job_dir = api._open_or_create_job(
            api._stable_job_id(Path(video), options),
            api._default_jobs_dir(),
            Path(video),
        ).dir
        csv_path = job_dir / "profile.csv"
        sampler = ProfileSampler(csv_path, stage_getter=lambda: current_stage["name"])
        sampler.start()
    try:
        result = api.run_dub(
            Path(video),
            Path(outdir),
            options=options,
            secrets=secrets,
            progress=show,
            cancel=threading.Event(),
        )
    finally:
        if sampler is not None:
            rows = sampler.stop()
    if rows is not None:
        print(f"profile: {len(rows)} samples -> {csv_path}")
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
    parser.add_argument("--layout-check", action="store_true")
    parser.add_argument("--scale", type=float, default=1.0, metavar="S")
    parser.add_argument("--headless-run", nargs=2, metavar=("VIDEO", "OUTDIR"))
    parser.add_argument("--profile", action="store_true",
                        help="with --headless-run: sample CPU once per second into profile.csv")
    parser.add_argument("--mode", choices=("fast", "accurate"), default="fast")
    args = parser.parse_args(argv)

    if args.version:
        _print_version()
        return 0
    if args.selftest:
        from dubtool.app.selftest import run_selftest

        return run_selftest(Path(args.json) if args.json else None)
    if args.layout_check:
        return 0 if run_layout_check(args.scale) == 0 else 1
    if args.screenshots:
        return run_screenshots(Path(args.screenshots), args.scale)
    if args.headless_run:
        return run_headless(Path(args.headless_run[0]), Path(args.headless_run[1]), args.mode,
                            profile=args.profile)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
