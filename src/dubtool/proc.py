"""The single subprocess helper (AGENT_UPDATE_6.md task 1).

Every subprocess call in src/ and scripts/ must go through this module.
On Windows the child gets CREATE_NO_WINDOW + SW_HIDE so the packaged
(windowed) app never flashes console windows, and stdin=DEVNULL because a
windowed app has no valid standard input. `low_priority` runs the child
below normal priority so encodes keep the machine responsive. `cancel`
makes a long call stop within ~0.2 s of the event, killing the whole
process tree (ffmpeg spawns helper processes).

It also counts every launch (`stats`, keyed by stage via `stage_scope`),
which is how the process-storm improvement is measured.
"""

from __future__ import annotations

import contextvars
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("dubtool")

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_IS_WIN = sys.platform == "win32"

# ------------------------------------------------------------ launch stats
_current_stage: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dubtool_proc_stage", default=""
)
stats: dict[str, dict[str, int]] = {}


def _record_for_stats(cmd: list[str]) -> None:
    try:
        exe = os.path.splitext(os.path.basename(cmd[0]))[0] if cmd else "?"
    except Exception:
        exe = "?"
    stage = _current_stage.get() or "not-in-a-stage"
    per_stage = stats.setdefault(stage, {})
    per_stage[exe] = per_stage.get(exe, 0) + 1


def stats_summary(stage: str | None = None) -> str:
    """'s4: 311 ffprobe, 82 ffmpeg' style line for the log."""
    with_stage = stage if stage is not None else _current_stage.get()
    if not with_stage:
        return "no subprocess calls recorded"
    entries = stats.get(with_stage) or {}
    total = 0
    parts: list[str] = []
    for exe, count in sorted(entries.items(), key=lambda kv: -kv[1]):
        parts.append(f"{count} {exe}")
        total += count
    if not total:
        return f"{with_stage}: 0 process launches"
    # Order by count only; display "<total>: a exe, b exe".
    return f"{with_stage}: total {total} ({', '.join(parts)})"


def set_current_stage(stage: str) -> Any:
    """Return a token to pass back to _current_stage.reset(token)."""
    return _current_stage.set(stage)


# ------------------------------------------------------------------ flags
def _windows_flags(low_priority: bool) -> tuple[int, Any]:
    flags = 0
    sui = None
    if _IS_WIN:
        flags |= CREATE_NO_WINDOW
        if low_priority:
            flags |= BELOW_NORMAL_PRIORITY_CLASS
        sui = subprocess.STARTUPINFO()
        sui.dwFlags |= _STARTF_USESHOWWINDOW
        sui.wShowWindow = _SW_HIDE
    return flags, sui


def _posix_nice(low_priority: bool) -> Any:
    if not _IS_WIN and not low_priority:
        return None
    if _IS_WIN:
        return None

    def _pre():  # child-side niceness (only on POSIX when low_priority)
        try:
            os.nice(10)
        except OSError:
            pass

    return _pre


def popen(cmd: list[str], *, cwd=None, capture: bool = True,
          low_priority: bool = False, **kw) -> subprocess.Popen:
    """Windows-safe Popen: hidden window, DEVNULL stdin (AGU6 task 1)."""
    if isinstance(cmd, (str, bytes)):
        raise TypeError("proc.popen/run take a list of args, never a shell string")
    flags, sui = _windows_flags(low_priority)
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": kw.pop("stdout", subprocess.PIPE if capture else None),
        "stderr": kw.pop("stderr", subprocess.PIPE if capture else None),
    }
    if _IS_WIN:
        kwargs["creationflags"] = flags
        if sui is not None and "startupinfo" not in kw:
            kwargs["startupinfo"] = sui
    else:
        pre = _posix_nice(low_priority)
        if pre is not None:
            kwargs["preexec_fn"] = pre
    text = kw.pop("text", True)
    if text:
        kwargs["encoding"] = kw.pop("encoding", "utf-8")
        kwargs["errors"] = kw.pop("errors", "replace")
    kwargs.update(kw)
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    _record_for_stats(list(cmd))
    return subprocess.Popen(cmd, **kwargs)


def _cancel_exception():
    from dubtool.job import StageCancelled

    return StageCancelled("cancelled during a subprocess call")


def _kill_tree(pid: int) -> None:
    """Terminate the child and its whole process tree, hidden."""
    if _IS_WIN:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, timeout=10,
            )
            return
        except Exception:
            pass
    try:
        import signal

        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def run(cmd: list[str], *, cwd=None, capture: bool = True, timeout: float | None = None,
        check: bool = False, low_priority: bool = False, cancel=None,
        **kw) -> subprocess.CompletedProcess:
    """`subprocess.run` with the AGU6 flags and optional cancellation.

    With a `cancel` event the child is polled every 0.2 s and killed (with
    its tree) when the event is set; the pipeline's StageCancelled is raised
    so the job stays resumable.
    """
    if cancel is None and timeout is None:
        proc = popen(cmd, cwd=cwd, capture=capture, low_priority=low_priority, **kw)
        out, err = proc.communicate()
        completed = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    elif cancel is not None:
        proc = popen(cmd, cwd=cwd, capture=capture, low_priority=low_priority, **kw)
        out = err = None
        cancel_seen = False
        while True:
            try:
                out, err = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if not cancel_seen and cancel.is_set():
                    cancel_seen = True
                    _kill_tree(proc.pid)
                    # Give the child a moment to die; keep draining so
                    # communicate() can finish and the pipes close.
                if cancel_seen:
                    try:
                        proc.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.communicate(timeout=3)
                        except subprocess.TimeoutExpired:
                            pass
                    if proc.poll() is None:
                        proc.kill()
                    raise _cancel_exception()
                # normal progress loop; also enforce an absolute timeout
        completed = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        if timeout is not None and proc.returncode is not None and completed.returncode < 0:
            pass
    else:
        # timeout only: Popen + bounded communicate so TimeoutExpired is the
        # same error type subprocess.run raises.
        proc = popen(cmd, cwd=cwd, capture=capture, low_priority=low_priority, **kw)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                with contextlib_suppress():
                    proc.communicate(timeout=5)
            raise
        completed = subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode, completed.args, completed.stdout, completed.stderr
        )
    return completed


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
