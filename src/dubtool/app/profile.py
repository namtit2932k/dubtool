"""CPU profiler for --headless-run --profile (AGENT_UPDATE_6.md 4.1).

A daemon thread samples once per second and writes profile.csv in the job
directory: elapsed_s, stage, app_cpu_pct, children_cpu_pct, system_cpu_pct,
live_children. `stage_getter` returns the current stage name; run_headless
feeds it from its progress callback. Pure psutil, no guessing.
"""

from __future__ import annotations

import csv
import threading
import time
from pathlib import Path


class ProfileSampler:
    _COLUMNS = (
        "elapsed_s", "stage", "app_cpu_pct", "children_cpu_pct",
        "system_cpu_pct", "live_children",
    )

    def __init__(self, csv_path: Path, interval: float = 1.0, stage_getter=None):
        self._path = Path(csv_path)
        self._interval = interval
        self._stage_getter = stage_getter or (lambda: "")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = 0.0

    def start(self) -> None:
        import psutil

        self._proc = psutil.Process()
        self._proc.cpu_percent(interval=None)  # prime the first reading
        psutil.cpu_percent(interval=None, percpu=False)
        self._start = time.monotonic()
        rows: list[dict] = []
        self._path.parent.mkdir(parents=True, exist_ok=True)

        def sample() -> None:
            while not self._stop.is_set():
                elapsed = time.monotonic() - self._start
                try:
                    children = self._proc.children(recursive=True)
                    children_cpu = 0.0
                    for child in children:
                        try:
                            children_cpu += child.cpu_percent(interval=None)
                        except Exception:  # child died between calls
                            pass
                    row = {
                        "elapsed_s": round(elapsed, 1),
                        "stage": str(self._stage_getter() or ""),
                        "app_cpu_pct": round(self._proc.cpu_percent(interval=None), 1),
                        "children_cpu_pct": round(children_cpu, 1),
                        "system_cpu_pct": round(psutil.cpu_percent(interval=None, percpu=False), 1),
                        "live_children": len(children),
                    }
                except Exception:
                    row = {"elapsed_s": round(elapsed, 1), "stage": "",
                           "app_cpu_pct": "", "children_cpu_pct": "",
                           "system_cpu_pct": "", "live_children": ""}
                rows.append(row)
                self._stop.wait(self._interval)

        def write_out() -> None:
            try:
                with self._path.open("w", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=list(self._COLUMNS))
                    writer.writeheader()
                    writer.writerows(rows)
            except OSError:
                pass

        self._write_out = write_out
        self._rows = rows
        self._thread = threading.Thread(target=sample, daemon=True, name="dubtool-profile")
        self._thread.start()

    def stop(self) -> list[dict] | None:
        if self._thread is None:
            return None
        self._stop.set()
        self._thread.join(timeout=3)
        self._write_out()
        try:
            with self._path.open("r", encoding="utf-8", newline="") as fh:
                return list(csv.DictReader(fh))
        except OSError:
            return None
