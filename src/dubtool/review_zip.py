"""`dubtool review-zip` — bridge to scripts/make_review_zip.py (stdlib only)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "make_review_zip.py"
    spec = importlib.util.spec_from_file_location("make_review_zip", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def cmd_review_zip(tag: str | None = None) -> int:
    try:
        module = _load_script()
        out = module.make_review_zip(tag=tag)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"review zip: {out} ({out.stat().st_size} bytes)")
    print("Reminder: upload this zip for review.")
    return 0
