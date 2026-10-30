"""PipelineService: runs api.run_dub in a Qt worker thread.

The service owns the worker QThread; run_dub executes inside it and progress
events cross back over signals. Cancel sets a threading.Event that the
pipeline checks between items and stages (AGU4 6.3 / 12).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from dubtool import api
from dubtool.api import DubOptions, DubResult, ProgressEvent


class PipelineService(QObject):
    """One dub at a time. The worker thread calls api.run_dub directly."""

    progressed = Signal(str, str, int, int, str, float)   # stage, state, done, total, message, elapsed
    finishedRun = Signal(object)                          # DubResult

    def __init__(self):
        super().__init__()
        self._thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.running = False
        self._current: tuple[Path, Path, DubOptions, bool] | None = None
        self._stage_started = 0.0
        self._eta_known: tuple[str, float] | None = None

    def start(
        self,
        video: Path,
        output_dir: Path,
        options: DubOptions,
        secrets: api.SecretsProvider,
        *,
        force: bool = False,
        jobs_dir: Path | None = None,
        extra_overrides: dict | None = None,
        overload_dialog=None,
        fillers_dialog=None,
    ) -> bool:
        """Start a run (resume happens automatically via the stable job id).
        Returns False if a run is already active.

        `overload_dialog` (AGU8 3.4) and `fillers_dialog` (AGU8 5.3) are the
        thread-safe callables that raise the GUI dialog and block until the
        user answers; they are only called twice for the whole run."""
        if self.running:
            return False
        self.cancel_event = threading.Event()
        self.running = True
        video, output_dir = Path(video), Path(output_dir)

        def emit(pe: ProgressEvent) -> None:
            eta = None
            if pep_eta := self._eta_hint(pe):
                eta = pep_eta
            self.progressed.emit(
                pe.stage, pe.state,
                pe.done if pe.done is not None else -1,
                pe.total if pe.total is not None else -1,
                pe.message or "", eta or float("nan"),
            )

        def worker() -> None:
            result: DubResult = api.run_dub(
                video,
                output_dir,
                options=options,
                secrets=secrets,
                progress=emit,
                cancel=self.cancel_event,
                jobs_dir=jobs_dir,
                force=force,
                extra_overrides=extra_overrides,
                overload_dialog=overload_dialog,
                fillers_dialog=fillers_dialog,
            )
            self.running = False
            self.finishedRun.emit(result)

        self._thread = threading.Thread(target=worker, name="dubtool-run", daemon=True)
        self._thread.start()
        return True

    def _eta_hint(self, pe: ProgressEvent) -> float | None:
        """Best-effort ETA for the current stage from the progress fraction."""
        if pe.state == "start":
            self._stage_started = time.monotonic()
            self._eta_known = None
        if pe.state == "progress" and pe.done is not None and pe.total:
            if pe.done <= 0 or pe.total <= pe.done:
                return None
            elapsed = time.monotonic() - self._stage_started
            return round(elapsed * (pe.total - pe.done) / pe.done, 1)
        return None

    def cancel(self) -> None:
        self.cancel_event.set()

    def wait(self, timeout: float = 60.0) -> bool:
        t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()
