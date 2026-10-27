"""App entry point (AGU4 6.2 / 11.1).

Flags usable from source and from the packaged exe:
  DubTool.exe                 -> the GUI
  --version                   -> app + bundled FFmpeg version
  --selftest [--json out.json]
  --layout-check [--scale S]  -> geometry invariant sweep, table to stdout
  --screenshots <dir> [--scale S] -> offscreen PNGs of the UI states
  --gui-smoke                 -> real-widget interaction check (AGU7 8)
  --headless-run <video> <outdir> --mode fast|accurate
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

APP_VERSION = "1.0.0"

SHOT_SIZES = [(760, 600), (900, 700), (1100, 800)]


def app_version() -> str:
    return APP_VERSION


def _build_info_line() -> str:
    """The 'build id: ...' line of BUILD_INFO.txt shipped next to the exe
    (AGU7 task 5); empty when absent (e.g. source runs)."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parents[2]
    try:
        for line in (base / "BUILD_INFO.txt").read_text(encoding="utf-8").splitlines():
            if line.startswith("build id:"):
                return line.strip()
    except OSError:
        pass
    return ""


def _print_version() -> None:
    from dubtool.app.ui.main_window import _ffmpeg_version

    print(f"DubTool {app_version()}")
    info = _build_info_line()
    if info:
        print(info)
    print(_ffmpeg_version())


def run_gui() -> int:
    from PySide6.QtWidgets import QApplication

    from dubtool.app import i18n, settings
    from dubtool.app import failguard
    from dubtool.app.paths import ensure_user_dirs
    from dubtool.app.ui.main_window import MainWindow

    # AGENT_UPDATE_7.md 2.3: slot exceptions become a visible dialog + a log
    # entry, never a silent stderr writes a windowed exe cannot show.
    failguard.install()
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

    # Tabbed Settings dialog: whole dialog + each tab. The keys tab shows
    # the AGU7 required look: 3 MASKED fake keys, one selected, one row with
    # the quota badge — via an in-memory fake store, nothing real is saved.
    from dubtool.app import secrets as secrets_for_shots

    _orig = {
        name: getattr(secrets_for_shots, name, None)
        for name in ("_migrate_legacy", "env_keys", "_stored_list", "_save_list")
    }
    fake = [f"AIzascreenshotfake-{i:02d}-aaaaaaaaaaaaaaaa" for i in range(1, 4)]
    secrets_for_shots._migrate_legacy = lambda: None
    secrets_for_shots.env_keys = lambda: []
    secrets_for_shots._stored_list = lambda: list(fake)
    secrets_for_shots._save_list = lambda keys: fake.__setitem__(slice(None), list(keys))
    settings_dialog = SettingsDialog()
    settings_dialog._badges = {1: "quota"}  # row 2 shows the quota badge
    settings_dialog._refresh_rows(select=0)  # one key selected
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
    # release the fake store patch immediately after the dialog shots
    for name, original in _orig.items():
        if original is not None:
            setattr(secrets_for_shots, name, original)

    # Privacy notice
    privacy = PrivacyDialog()
    privacy.show()
    privacy.adjustSize()
    app.processEvents()
    privacy.grab().save(str(out_dir / "dialog_first_run.png"))
    privacy.close()

    _write_screenshots_index(out_dir, scale)
    print(f"screenshots written to {out_dir}")
    return 0


def _write_screenshots_index(out_dir: Path, scale: float) -> None:
    """INDEX.txt next to the images (AGU7 task 5): build id and timestamp
    straight from BUILD_INFO.txt so the screenshots test can cross-check
    them against the exe that produced the set."""
    lines: list[str] = []
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parents[2]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        raw = (base / "BUILD_INFO.txt").read_text(encoding="utf-8").replace("\ufeff", "")
    except OSError:
        raw = ""
    for key in ("git:", "built (UTC):", "build id:"):
        for line in raw.splitlines():
            if line.startswith(key):
                lines.append(line.strip())
                break
        else:
            if key == "git:":
                lines.append("git: (info not available — not built here)")
            elif key == "built (UTC):":
                lines.append(f"built (UTC):  {now} (from BUILD_INFO.txt is absent)")
            else:
                lines.append("build id: (source run, no BUILD_INFO.txt)")
    lines.insert(0, f"DubTool screenshots - {app_version()}")
    lines += [
        "images:       from the packaged exe (DubTool.exe --screenshots), offscreen",
        f"scale:        {scale} (QT_SCALE_FACTOR unset when 1.0)",
        "window sizes: 760x600, 900x700, 1100x800",
        "states:       idle, video, running, finished (with 'Mo ban dich'), error",
        "dialogs:      error, first-run privacy, Settings (whole dialog + each tab)",
        "notes:        key rows are masked (fake keys); no real key material in any image",
    ]
    (out_dir / "INDEX.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_gui_smoke() -> int:
    """AGENT_UPDATE_7.md section 8: the real widgets driven with real events
    while every side effect is faked (credential store, key test, pipeline).
    Works from source and from the packaged exe. main() points APPDATA and
    LOCALAPPDATA at a temp folder first, so nothing in this run touches the
    real app data or the real Credential Manager.

    Steps:
      1. import a generated 2-minute video through the real handler;
         assert the info shows 02:00 and NO long-video dialog appears.
      2. settings flow: add two keys through the real add dialog, select a
         row, move it up, remove one with the confirmation answered, run
         "Test selected" (fake tester); button states checked along the way.
      3. a run per mode with the (faked) pipeline through the real service
         and Generate buttons; the success state shows "Mở bản dịch" and
         both the video and the readable file exist.
    """
    import tempfile
    import threading
    import time

    from PySide6.QtWidgets import QApplication

    from dubtool import api
    from dubtool.api import DubResult, ProgressEvent
    from dubtool.app import i18n, settings
    from dubtool.app import failguard
    from dubtool import audio_utils, proc
    from dubtool.app import secrets as secrets_mod
    from dubtool.app.paths import ensure_user_dirs

    app = QApplication.instance() or QApplication([])
    failguard.install()
    ensure_user_dirs()
    s = settings.load()
    s["privacy_acknowledged"] = True  # the real first_run gate passes
    settings.save(s)
    i18n.set_language("vi")

    results: list[tuple[str, bool, str]] = []

    def step(name, fn):
        try:
            fn()
            results.append((name, True, ""))
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"))

    def wait_for(cond, timeout_s: float = 10.0, step_s: float = 0.05) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            app.processEvents()
            if cond():
                return
            time.sleep(step_s)
        app.processEvents()
        if not cond():
            raise AssertionError("condition not met within timeout")

    def click(item_or_btn, list_widget=None):
        """A real mouse click, not a direct .click() — usable on buttons and
        on list items alike."""
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        if list_widget is None:
            QTest.mouseClick(item_or_btn, Qt.MouseButton.LeftButton)
        else:
            rect = list_widget.visualItemRect(item_or_btn)
            QTest.mouseClick(
                list_widget.viewport(), Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier, rect.center(),
            )

    # ------------------------------------------------------------- fakes
    fake_store: list[str] = []
    secrets_mod._migrate_legacy = lambda: None          # no real keyring I/O
    secrets_mod.env_keys = lambda: []
    secrets_mod._stored_list = lambda: list(fake_store)
    secrets_mod._save_list = lambda keys: fake_store.__setitem__(slice(None), list(keys))

    tester_calls: list[str] = []

    def fake_key_test(key: str, model=None) -> str:
        tester_calls.append(key)
        return "ok"

    import dubtool.app.ui.settings_dialog as settings_dialog_mod
    from dubtool.app.ui.settings_dialog import _AddKeysDialog

    settings_dialog_mod.run_key_test = fake_key_test

    run_calls: list[str] = []

    def fake_run_dub(video, outdir, options, secrets=None, progress=None,
                     cancel=None, jobs_dir=None, force=False, extra_overrides=None):
        run_calls.append(options.mode)
        for name in ("s1_extract_audio", "s2_transcribe", "s3_translate",
                     "s4_tts", "s5_fit_duration", "s6_mix", "s7_subtitles"):
            if progress:
                progress(ProgressEvent(stage=name, state="start"))
                progress(ProgressEvent(stage=name, state="done"))
        video, outdir = Path(video), Path(outdir)
        suffix = 1
        out = outdir / f"{video.stem}_vi.mp4"
        while out.exists():
            suffix += 1
            out = outdir / f"{video.stem}_vi_{suffix}.mp4"
        out.write_bytes(b"smoke-video")
        readable = out.with_name(out.stem + "_translation.docx")
        readable.write_bytes(b"PK\x03\x04 smoke-docx")
        return DubResult(ok=True, code="ok", out_video=out, out_readable=readable,
                         job_id="gui-smoke", llm_requests=0)

    api.run_dub = fake_run_dub

    # ------------------------------------------------------------ video
    smoke_dir = Path(tempfile.mkdtemp(prefix="dubtool-smoke-media-"))
    outdir = smoke_dir / "out"
    outdir.mkdir()

    def make_video() -> Path:
        video = smoke_dir / "smokeclip.mp4"
        r = proc.run(
            [audio_utils.ffmpeg_bin(), "-y", "-f", "lavfi",
             "-i", "testsrc=size=160x120:rate=2:duration=120",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             str(video)],
            timeout=300,
        )
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg sample generation failed: {r.stderr[-200:]}")
        return video

    from dubtool.app.service import PipelineService
    from dubtool.app.ui.main_window import MainWindow

    window: MainWindow | None = None
    video: Path | None = None

    def do_video() -> None:
        nonlocal window, video
        video = make_video()
        window = MainWindow(PipelineService())
        window.show()
        app.processEvents()
        window.set_video(video)  # the real import handler (no file dialog)
        app.processEvents()
        # modal must still be None -> no long-video dialog for 2 minutes
        assert QApplication.activeModalWidget() is None, "unexpected modal after import"
        info = window.video_info.text()
        assert "02:00" in info, f"duration not 02:00: {info!r}"

    def do_output_dir() -> None:
        window.output_dir = outdir
        window.output_edit.setText(str(outdir))
        window._validate_output()
        window._refresh_buttons()
        assert window.fast_btn.isEnabled() and window.accurate_btn.isEnabled(), "generate buttons not ready"

    # ------------------------------------------------------------ settings
    dlg = settings_dialog_mod.SettingsDialog(window)

    def accept_add_dialog() -> None:
        modal = QApplication.activeModalWidget()
        if not isinstance(modal, _AddKeysDialog):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, accept_add_dialog)
            return
        modal.field.setPlainText(f"AIzasmokekey-ONE-{time.perf_counter_ns():x}\nAQ.smokekey-two-two\n")
        click(modal.ok_button)  # the dialog's real OK handler

    def do_add_two() -> None:
        assert dlg.key_list.count() == 0, "store should start empty"
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, accept_add_dialog)
        click(dlg.add_btn)  # opens the real add dialog (modal exec)
        wait_for(lambda: dlg.key_list.count() == 2, 10)
        assert len(fake_store) == 2, "keys not saved in order"
        for row in range(2):
            text = dlg.key_list.item(row).text()
            assert fake_store[row] not in text, "a full key leaked into the row"
            assert "\u2022\u2022\u2022\u2022" in text, f"row not masked: {text!r}"
        assert dlg.key_list.currentRow() == 1, "add should select the new last key"

    def take_click(item_index):
        click(dlg.key_list.item(item_index), dlg.key_list)

    def do_button_states_first_last() -> None:
        take_click(0)
        wait_for(lambda: dlg.key_list.currentRow() == 0, 5)
        assert not dlg.up_btn.isEnabled(), "Up enabled on the first row"
        assert dlg.down_btn.isEnabled() and dlg.remove_btn.isEnabled() \
               and dlg.test_one_btn.isEnabled(), "buttons wrong on first row"
        take_click(1)
        wait_for(lambda: dlg.key_list.currentRow() == 1, 5)
        assert dlg.up_btn.isEnabled() and not dlg.down_btn.isEnabled(), "buttons wrong on last row"

    def do_move_up() -> None:
        order_before = list(fake_store)
        click(dlg.up_btn)  # selected row 1 moves to row 0
        wait_for(lambda: fake_store == order_before[::-1], 5)
        assert dlg.key_list.currentRow() == 0, "selection did not follow the moved key"

    def accept_remove_confirm() -> None:
        from PySide6.QtWidgets import QMessageBox

        modal = QApplication.activeModalWidget()
        if not isinstance(modal, QMessageBox):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(50, accept_remove_confirm)
            return
        yes = modal.button(QMessageBox.StandardButton.Yes)
        click(yes if yes is not None else modal)

    def do_remove_one() -> None:
        from PySide6.QtCore import QTimer

        order_before = list(fake_store)
        QTimer.singleShot(50, accept_remove_confirm)
        click(dlg.remove_btn)  # row 0 selected -> its key is removed
        wait_for(lambda: len(fake_store) == 1, 10)
        assert fake_store == order_before[1:], "the wrong key was removed"
        assert dlg.key_list.currentRow() == 0, "selection not restored after remove"

    def do_test_selected() -> None:
        remaining = list(fake_store)
        take_click(0)
        click(dlg.test_one_btn)  # "Test selected": one fake call, that key only
        wait_for(lambda: tester_calls, 10)
        wait_for(lambda: not dlg._testing_rows, 10)
        assert tester_calls == remaining, f"tester called with {tester_calls!r}, not the selected key"
        assert dlg._badges.get(0) not in ("__testing__", None, "") or dlg._badges.get(0) == "ok", "badge not updated"

    def close_settings() -> None:
        dlg.accept()  # never done() with key text still around (2.1)
        app.processEvents()
        assert QApplication.activeModalWidget() is None, "settings dialog did not close"

    # ------------------------------------------------------------ runs
    def wait_run_done() -> None:
        wait_for(lambda: not window.service.running, 120)
        thread = window.service._thread
        assert thread is None or not thread.is_alive(), "worker thread still alive"
        app.processEvents()
        assert window.progress_group.cancel_btn.isHidden(), "run stuck in the running state"
        assert window.progress_group.readable_btn.isVisible(), "success state missing the M\u1edf b\u1ea3n d\u1ecbch button"
        videos = list(outdir.glob("*_vi*.mp4"))
        readables = list(outdir.glob("*_vi*_translation.docx"))
        assert videos, "no video in the output folder"
        assert readables, "no readable translation file in the output folder"

    def do_run(mode: str) -> None:
        btn = window.fast_btn if mode == "fast" else window.accurate_btn
        click(btn)
        wait_run_done()

    def do_runs() -> None:
        do_run("fast")
        do_run("accurate")
        assert run_calls == ["fast", "accurate"], f"runs: {run_calls}"

    # main flow: each step reports its own row
    step("video import (2-min, no long-video dialog)", do_video)
    step("output folder ready for the generate buttons", do_output_dir)
    step("add two keys (real add dialog)", do_add_two)
    step("button states on first/last row", do_button_states_first_last)
    step("move selected key up", do_move_up)
    step("remove row 0 with confirmation", do_remove_one)
    step("test selected key (fake tester)", do_test_selected)
    if all(ok for _n, ok, _d in results):
        step("close settings", close_settings)
        step("run fast + accurate via real buttons", do_runs)
    else:
        results.append(("pipeline skipped", False, "skipped: settings flow failed first"))

    print()
    header = f"{'step':<48} {'status':<6} detail"
    print(header)
    print("-" * len(header))
    for name, ok, detail in results:
        print(f"{name:<48} {'PASS' if ok else 'FAIL':<6} {detail}")
    ok_all = all(ok for _n, ok, _d in results)
    print(f"\ngui-smoke: {'PASS' if ok_all else 'FAIL'} ({len(results)} steps)")
    print(f"temp workspace: {smoke_dir} (deleted on exit unless a step failed)")
    if ok_all:
        import shutil

        shutil.rmtree(smoke_dir, ignore_errors=True)
    try:
        window.close()
    except Exception:
        pass
    return 0 if ok_all else 1


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
    parser.add_argument("--gui-smoke", action="store_true")
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
    if args.gui_smoke:
        # Isolate before any dubtool module has imported paths: a private
        # temp app data for this run (AGU7 8, sanitized environment).
        td = tempfile.mkdtemp(prefix="dubtool-gui-smoke-appdata-")
        for var in ("APPDATA", "LOCALAPPDATA"):
            os.environ[var] = td
        return run_gui_smoke()
    if args.screenshots:
        return run_screenshots(Path(args.screenshots), args.scale)
    if args.headless_run:
        return run_headless(Path(args.headless_run[0]), Path(args.headless_run[1]), args.mode,
                            profile=args.profile)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
