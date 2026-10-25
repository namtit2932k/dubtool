"""Offline self-checks usable from source and from the packaged exe (AGU4 11.1).

No network required (the font render test uses FFmpeg only). Exit code 0
only when every check passes; prints a table. `--json out.json` also writes
a machine-readable copy.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from dubtool.app import paths

SAMPLE_TEXT = "Chào mừng bạn, ắ ữ ề ộ ỵ"


def _ffmpeg_path() -> str:
    """CLI ffmpeg via DUBTOOL_FFMPEG/env handled by audio_utils; frozen uses vendor."""
    from dubtool import audio_utils

    try:
        from dubtool.audio_utils import FFMPEG_BIN
        return str(FFMPEG_BIN)
    except ImportError:
        return "ffmpeg"


def check_ffmpeg() -> dict:
    try:
        from dubtool import audio_utils

        audio_utils.require_ffmpeg()
        out = subprocess.run(
            [audio_utils.ffmpeg_bin(), "-version"],
            capture_output=True, text=True, timeout=30,
        )
        first = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else "unknown"
        return {"ok": out.returncode == 0, "detail": first}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def check_filters_and_encoder() -> dict:
    """libass (`subtitles`), amix, alimiter, sidechaincompress + libx264."""
    try:
        from dubtool import audio_utils

        f = subprocess.run(
            [audio_utils.ffmpeg_bin(), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=60,
        )
        e = subprocess.run(
            [audio_utils.ffmpeg_bin(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=60,
        )
        filters = f.stdout
        encoders = e.stdout
        missing = [
            name for name in ("subtitles", "amix", "alimiter", "sidechaincompress")
            if name not in filters
        ]
        if "libx264" not in encoders:
            missing.append("libx264")
        return {"ok": not missing, "detail": "all present" if not missing else f"missing: {missing}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def check_font_renders() -> dict:
    """Burn the Vietnamese sample string with the default font and measure the
    changed pixel block height as a percentage of frame height."""
    return _measure_text_block()


def _write_sample_ass(path: Path) -> None:
    path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Outline\n"
        "Style: S,Arial,36,&H00FFFFFF,&H00000000,2\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:01.00,S,{SAMPLE_TEXT}\n",
        encoding="utf-8",
    )


def _measure_text_block() -> dict:
    """Exact-frame comparison of a plain frame vs the same frame with the
    sample string burned in; the bright-pixel bounding box height / 360."""
    import subprocess
    import tempfile
    from pathlib import Path

    import numpy as np

    from dubtool import audio_utils

    try:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _write_sample_ass(base / "t.ass")
            clean, burned = base / "clean.png", base / "burned.png"
            fontsdir = str(paths.font_file().parent).replace("\\", "/") if paths.font_file() else ""
            sub_filter = "subtitles=t.ass" + (f":fontsdir={fontsdir}" if fontsdir else "")
            for name, vf in (("clean.png", None), ("burned.png", sub_filter)):
                cmd = [
                    audio_utils.ffmpeg_bin(), "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=640x360:d=1",
                    "-frames:v", "1", str(base / name),
                ]
                if vf:
                    cmd = [
                        audio_utils.ffmpeg_bin(), "-y", "-f", "lavfi",
                        "-i", "color=c=black:s=640x360:d=1",
                        "-vf", vf, "-frames:v", "1", str(base / name),
                    ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=base)
                if r.returncode != 0:
                    return {"ok": False, "detail": f"render {name} failed: {r.stderr[-200:]}"}

            def load(p):
                return subprocess.run(
                    [audio_utils.ffmpeg_bin(), "-i", str(p), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                    capture_output=True, timeout=60, cwd=base,
                ).stdout

            a = np.frombuffer(load(clean), dtype=np.uint8).reshape(-1, 640)
            b = np.frombuffer(load(burned), dtype=np.uint8).reshape(-1, 640)
            if a.shape != b.shape:
                return {"ok": False, "detail": f"frame size mismatch: {a.shape} vs {b.shape}"}
            diff = (np.abs(a.astype(int) - b.astype(int)) > 40).any(axis=1)
            rows = np.where(diff)[0]
            if len(rows) == 0:
                return {"ok": False, "detail": "no text detected in burned frame"}
            height_pct = round(float((rows.max() - rows.min() + 1) / a.shape[0] * 100), 1)
            return {"ok": True, "detail": f"text block height {height_pct}% of frame height"}
    except Exception as exc:
        return {"ok": False, "detail": f"font check failed: {type(exc).__name__} {exc}"}


def check_keyring() -> dict:
    from dubtool.app import secrets as secrets_mod

    name = secrets_mod.backend_name()
    return {"ok": name is not None, "detail": f"backend: {name or 'none (env fallback)'}"}


def check_dirs_writable() -> dict:
    results = []
    for d in (paths.app_data_root(), paths.jobs_dir(), paths.models_dir()):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".selftest"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            results.append(f"{d.name}: writable")
        except OSError as exc:
            return {"ok": False, "detail": f"{d}: {exc}"}
    return {"ok": True, "detail": "; ".join(results)}


def check_imports(modules: tuple[str, ...]) -> dict:
    missing = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            missing.append(f"{name}: {type(exc).__name__}")
    return {"ok": not missing, "detail": "all import" if not missing else "missing: " + ", ".join(missing)}


def check_qt_offscreen() -> dict:
    try:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMainWindow

        app = QApplication.instance() or QApplication([])
        win = QMainWindow()
        win.show()
        win.close()
        return {"ok": True, "detail": "offscreen QMainWindow created"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


CHECKS: dict[str, callable] = {
    "ffmpeg": check_ffmpeg,
    "filters_encoders": check_filters_and_encoder,
    "font_vietnamese": _measure_text_block,
    "keyring": check_keyring,
    "dirs_writable": check_dirs_writable,
    "imports": lambda: check_imports(
        ("faster_whisper", "ctranslate2", "av", "tokenizers", "huggingface_hub", "edge_tts")
    ),
    "qt_offscreen": check_qt_offscreen,
}


def run_selftest(json_path: Path | None = None) -> int:
    results = {name: fn() for name, fn in CHECKS.items()}
    ok = all(r["ok"] for r in results.values())
    header = f"{'check':<20} {'status':<6} detail"
    print(header)
    print("-" * len(header))
    for name, r in results.items():
        print(f"{name:<20} {'PASS' if r['ok'] else 'FAIL':<6} {r['detail']}")
    print(f"\noverall: {'PASS' if ok else 'FAIL'}")
    if json_path:
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        Path(json_path).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"json written to {json_path}")
    return 0 if ok else 1
