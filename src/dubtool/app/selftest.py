"""Offline self-checks usable from source and from the packaged exe (AGU4 11.1).

No network required (the font render test uses FFmpeg only). Exit code 0
only when every check passes; prints a table. `--json out.json` also writes
a machine-readable copy.
"""

from __future__ import annotations

import functools
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from dubtool import proc
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
        out = proc.run(
            [audio_utils.ffmpeg_bin(), "-version"],
            timeout=30,
        )
        first = (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else "unknown"
        return {"ok": out.returncode == 0, "detail": first}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@functools.lru_cache(maxsize=None)
def _check_filters_and_encoder_cached() -> tuple[bool, str]:
    """Run the -filters/-encoders probes once for the process (AGU6 3.3).

    Checks libass (`subtitles`), amix, alimiter, sidechaincompress + libx264.
    """
    try:
        from dubtool import audio_utils

        f = proc.run(
            [audio_utils.ffmpeg_bin(), "-hide_banner", "-filters"],
            timeout=60,
        )
        e = proc.run(
            [audio_utils.ffmpeg_bin(), "-hide_banner", "-encoders"],
            timeout=60,
        )
        filters = f.stdout
        encoders = e.stdout
        missing = [
            name for name in ("subtitles", "amix", "alimiter", "sidechaincompress")
            if name not in filters
        ]
        if "libx264" not in encoders:
            missing.append("libx264")
        if not missing:
            return True, "all present"
        return False, f"missing: {missing}"
    except Exception as exc:
        return False, str(exc)


def check_filters_and_encoder() -> dict:
    """Public wrapper over the process-lifetime cached probe."""
    ok, detail = _check_filters_and_encoder_cached()
    return {"ok": ok, "detail": detail}


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
    import tempfile
    from pathlib import Path

    import numpy as np

    from dubtool import audio_utils, proc

    try:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            _write_sample_ass(base / "t.ass")
            clean, burned = base / "clean.png", base / "burned.png"
            # ffmpeg filter args can't carry a Windows drive colon (even
            # escaped: the parser splits before unescaping), so run with
            # cwd=base and point libass at a *relative* dir holding a copy
            # of the bundled font — same trick as s7_subtitles.py.
            sub_filter = "subtitles=t.ass"
            if paths.font_file():
                try:
                    shutil.copy(paths.font_file(), base / paths.font_file().name)
                    sub_filter += ":fontsdir=."
                except OSError:
                    pass  # unzip-less fallback: let libass use system fonts
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
                r = proc.run(cmd, timeout=60, cwd=base)
                if r.returncode != 0:
                    return {"ok": False, "detail": f"render {name} failed: {r.stderr[-200:]}"}

            def load(p):
                # rawvideo is binary: text=False keeps stdout bytes so
                # np.frombuffer gets a bytes-like object.
                return proc.run(
                    [audio_utils.ffmpeg_bin(), "-i", str(p), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                    timeout=60, cwd=base, text=False,
                ).stdout

            # 640x360 gray → 640 bytes per row (PROC-TEXT note: text=False above
            # is what makes the reshape valid).
            a = np.frombuffer((load(clean) or b""), dtype=np.uint8).reshape(-1, 640)
            b = np.frombuffer((load(burned) or b""), dtype=np.uint8).reshape(-1, 640)
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


def check_keys_and_tabs() -> dict:
    """AGU5 8: key count as a bare number (never values), key-pool import,
    and the Settings dialog's tab titles — all without touching the UI."""
    try:
        from dubtool.app import secrets as secrets_mod
        from dubtool.llm import keypool

        keys = secrets_mod.get_gemini_keys()
        keypool.KeyPool(keys)  # must construct; count reported, never values

        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from dubtool.app.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog()
        tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        return {"ok": True,
                "detail": f"keys: {len(keys)}; pool imports; tabs: {', '.join(tabs) or 'none'}"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def check_gemini_client() -> dict:
    """AGENT_UPDATE_7.md 4.2: the REAL google-genai client must construct
    with the production timeout options (HttpOptions.timeout as an integer
    number of milliseconds) — the Update-6 string format '180000ms' failed
    validation and killed every Gemini request. No network is used."""
    try:
        import google.genai as gemini_mod

        version = str(getattr(gemini_mod, "__version__", "") or "?")
        from dubtool.llm.gemini import build_gemini_client

        client = build_gemini_client("test-key-not-a-real-key", timeout_ms=10_000)
        return {"ok": client is not None,
                "detail": f"google-genai {version}; client constructs, timeout=int ms"}
    except ImportError as exc:
        return {"ok": False, "detail": f"google-genai missing: {exc}"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def check_docx_roundtrip() -> dict:
    """AGENT_UPDATE_7.md 5.6: create a small .docx with Vietnamese text,
    read it back, and check the diacritics survive the round trip."""
    try:
        from pathlib import Path as _Path
        import tempfile as _tempfile

        from docx import Document
        from dubtool.stages import s8_export_readable as s8

        sample_vi = "Chào mừng bạn, ắ ữ ề ộ ỵ."
        meta = {
            "title": "Bản dịch video",
            "video": "selftest.mp4", "date": "01/01/2026", "duration": "01:23",
            "lang_pair": "Tiếng Anh → Tiếng Việt",
            "labels": {
                "video": "Video", "duration": "Thời lượng",
                "lang": "Ngôn ngữ", "date": "Ngày tạo",
                "col_time": "Thời gian", "col_en": "EN", "col_vi": "VI",
            },
            "disclaimer": "Bản dịch được tạo tự động bằng AI.",
            "page_word": "Trang {PAGE} / {NUMPAGES}",
        }
        rows = [{"time": 83.0, "en": "Hello there.", "vi": sample_vi}]
        with _tempfile.TemporaryDirectory() as td:
            path = _Path(td) / "roundtrip.docx"
            s8.render_docx(path, meta, rows)
            doc = Document(str(path))
            text = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
            ok = sample_vi in text
            return {"ok": ok, "detail": "docx Vietnamese text round-trips" if ok else "Vietnamese text lost"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def check_av_decode_compat() -> dict:
    """Found by AGU7's real-Gemini headless run: PyAV 19 removed the
    metadata_errors argument that faster-whisper 1.2.1's decode_audio passes
    to av.open, so an av>=19 bundle kills every transcription run with
    "open() got an unexpected keyword argument". The bundled av must accept
    the kwarg (pin: av<19 in the build requirements)."""
    try:
        import io
        import wave

        import av

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 8000)  # 1 s of silence, no ffmpeg
        buf.seek(0)
        av.open(buf, mode="r", metadata_errors="ignore")
        return {"ok": True,
                "detail": f"av {av.__version__}: metadata_errors accepted (faster-whisper decode)"}
    except TypeError as exc:
        return {"ok": False,
                "detail": f"av too new for faster-whisper decode ({exc}) — pin av<19"}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


CHECKS: dict[str, callable] = {
    "ffmpeg": check_ffmpeg,
    "filters_encoders": check_filters_and_encoder,
    "font_vietnamese": _measure_text_block,
    "keyring": check_keyring,
    "keys_and_tabs": check_keys_and_tabs,
    "dirs_writable": check_dirs_writable,
    "imports": lambda: check_imports(
        ("faster_whisper", "ctranslate2", "av", "tokenizers", "huggingface_hub", "edge_tts", "docx")
    ),
    "av_decode_compat": check_av_decode_compat,   # AGU7: faster-whisper/PyAV 19 breakage
    "qt_offscreen": check_qt_offscreen,
    "gemini_client": check_gemini_client,      # AGU7 4.2 (real SDK, no network)
    "docx_roundtrip": check_docx_roundtrip,    # AGU7 5.6
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


if __name__ == "__main__":
    raise SystemExit(run_selftest())
