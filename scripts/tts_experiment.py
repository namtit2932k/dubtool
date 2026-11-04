"""The interleaved voice-request experiment (Update 12 task 5).

This is a thin wrapper around `dubtool.tts_probe.run_experiment` — the
packaged exe's `--tts-probe` runs the SAME code, so the source-side and
exe-side runs are directly comparable. Old Update 11 behavior (one
condition after another, run from source only) is replaced.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dubtool.config import Config
from dubtool.tts_probe import run_experiment

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    run_experiment(Config(), out_path=out)
