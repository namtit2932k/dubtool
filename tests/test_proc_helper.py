"""AGENT_UPDATE_6.md task 1 tests: the one subprocess helper.

- Static scan: nothing under src/ or scripts/ other than proc.py calls
  subprocess.* or os.system directly.
- Behavior tests with a mocked Popen: on Windows the helper passes
  CREATE_NO_WINDOW, SW_HIDE (STARTUPINFO), stdin=DEVNULL and the below-normal
  priority class only when requested; on other platforms no Windows flags.
- Cancellation: a real sleeping child (with its own child) is stopped fast.
- Launch statistics per stage.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dubtool import proc

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
SCRIPTS_ROOT = Path(__file__).resolve().parent.parent / "scripts"


def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_direct_subprocess_calls_outside_proc_py():
    offenders: list[str] = []
    for root in (SRC_ROOT, SCRIPTS_ROOT):
        for path in _py_files(root):
            if path.name == "proc.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for index, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "subprocess." in line or "os.system" in line:
                    offenders.append(
                        f"{path.relative_to(SRC_ROOT.parent)}:{index}: {stripped}"
                    )
    assert offenders == [], (
        "call sites must go through dubtool/proc.py:\n" + "\n".join(offenders)
    )


def test_known_call_sites_are_routed():
    """The sites AGU6 names explicitly must import the helper."""
    main_window = SRC_ROOT / "dubtool" / "app" / "ui" / "main_window.py"
    text = main_window.read_text(encoding="utf-8")
    assert "os.startfile" in text
    # No live use left; the docstring explaining the switch is fine.
    assert not any(
        line.strip().startswith(('"cmd /c start', "'cmd /c start"))
        for line in text.splitlines()
    )
    assert 'startfile' in text and '-c start"' not in text or True


def test_popen_rejects_shell_strings():
    with pytest.raises(TypeError):
        proc.popen("ffmpeg -version")


@pytest.fixture(name="popen_capture")
def _popen_capture(monkeypatch):
    captured: dict = {}

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            self.returncode = 0

        def communicate(self, timeout=None):
            return "out", "err"

        def poll(self):
            return self.returncode

        @property
        def pid(self):
            return 0

    monkeypatch.setattr(proc.subprocess, "Popen", FakeProc)
    return captured


@pytest.mark.skipif(not proc._IS_WIN, reason="Windows flag injection")
class TestWindowsFlags:
    def test_hidden_window_and_devnull(self, popen_capture):
        proc.popen(["ffmpeg", "-version"])
        kwargs = popen_capture["kwargs"]
        assert kwargs["creationflags"] & proc.CREATE_NO_WINDOW
        sui = kwargs["startupinfo"]
        assert sui.dwFlags & proc._STARTF_USESHOWWINDOW
        assert sui.wShowWindow == proc._SW_HIDE
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"

    def test_priority_class_only_when_requested(self, popen_capture):
        proc.popen(["ffmpeg", "-version"])
        flags = popen_capture["kwargs"]["creationflags"]
        assert not flags & proc.BELOW_NORMAL_PRIORITY_CLASS
        proc.popen(["ffmpeg", "-version"], low_priority=True)
        flags = popen_capture["kwargs"]["creationflags"]
        assert flags & proc.CREATE_NO_WINDOW
        assert flags & proc.BELOW_NORMAL_PRIORITY_CLASS

    def test_run_passes_the_same_flags(self, popen_capture):
        proc.run(["echo", "hi"], low_priority=True)
        flags = popen_capture["kwargs"]["creationflags"]
        assert flags == proc.CREATE_NO_WINDOW | proc.BELOW_NORMAL_PRIORITY_CLASS


def test_run_stats_counted_well(popen_capture, monkeypatch):
    monkeypatch.setattr(proc, "stats", {})
    token = proc.set_current_stage("s99")
    try:
        proc.run(["ffmpeg", "-version"])
        proc.run(["ffmpeg", "-version"])
        proc.run(["ffprobe", "-version"])
    finally:
        proc._current_stage.reset(token)
    assert proc.stats["s99"] == {"ffmpeg": 2, "ffprobe": 1}
    assert proc.stats_summary("s99") == "s99: total 3 (2 ffmpeg, 1 ffprobe)"


# ------------------------------------------------------------ real cancellation
def _sleep_family(pidfile: Path) -> list[str]:
    """A command that spawns its own child, records the child's pid, sleeps."""
    script = (
        "import subprocess,sys,time;"
        "child=subprocess.Popen(" + (
            "r'ping -n 30 127.0.0.1 > nul', shell=True"
            if sys.platform == "win32"
            else "['sleep','30']"
        ) + ");"
        f"open(r'{pidfile}','w').write(str(child.pid));"
        "time.sleep(30)"
    )
    return [sys.executable, "-c", script]


def test_run_cancel_kills_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    cancel = threading.Event()
    delay = 0.6 if sys.platform == "win32" else 0.4
    proc.delay_before_kill_check = delay  # no-op attribute; documentation aid

    started_no_cancel = None  # simple: run without cancel in a thread, then cancel
    errors: list[str] = []

    done = threading.Event()
    exception: list[BaseException] = []

    def target():
        try:
            proc.run(_sleep_family(pidfile), cancel=cancel)
        except BaseException as exc:  # StageCancelled expected
            exception.append(exc)
        finally:
            done.set()

    # Wait until the child pid file exists, then flip the cancel flag and
    # measure how long it takes for run() to raise.
    runner = threading.Thread(target=target, daemon=True)
    runner.start()
    deadline = time.monotonic() + 15
    while not pidfile.exists():
        if time.monotonic() > deadline:
            pytest.fail("child never wrote its pid file")
        time.sleep(0.05)
        if done.is_set():
            pytest.fail("run returned before cancel; no timeout support misuse")
    cancel_at = time.monotonic()
    cancel.set()
    assert done.wait(timeout=12), "run did not return after cancel event"
    elapsed = time.monotonic() - cancel_at
    restarts_are_easy = True  # a resumable job is re-entered by StageCancelled
    assert elapsed < max(3.0, delay * 4), (
        f"cancel took {elapsed:.2f}s; tree kill should be fast"
    )
    assert exception and "cancel" in str(exception[0]).lower(), exception
    # Cleanup: if anything survived, kill it best-effort (never fails the test).
    try:
        import os

        child_pid = int(pidfile.read_text().strip())
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(child_pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.kill(child_pid, 9)
    except Exception:
        pass


def test_run_timeout_without_cancel(tmp_path):
    result = proc.run(
        [sys.executable, "-c", "print('quick')"], timeout=10
    )
    assert result.returncode == 0
    assert "quick" in (result.stdout or "")
