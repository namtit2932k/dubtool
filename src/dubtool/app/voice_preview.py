"""Offline Vietnamese voice preview helper for MainWindow & SettingsDialog.

Synthesizes a short sample sentence with VieNeu-TTS v3 ONNX on a background
thread, caches the WAV under ``<local_data>/voice_previews/`` so repeat clicks
play instantly, and plays audio asynchronously via ``winsound`` on Windows.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
from pathlib import Path

from dubtool.app import paths
from dubtool.tts.vieneu import VieNeuTTSProvider, normalize_mode, resolve_voice

log = logging.getLogger("dubtool")

SAMPLE_TEXT = "Xin chào, đây là giọng đọc mẫu tiếng Việt của DubTool."


def preview_cache_dir() -> Path:
    d = paths.app_data_root() / "voice_previews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def preview_wav_path(mode: str, voice: str) -> Path:
    m = normalize_mode(mode)
    v = resolve_voice(voice, m)
    key = hashlib.sha256(f"{m}\x00{v}\x00{SAMPLE_TEXT}".encode("utf-8")).hexdigest()[:16]
    return preview_cache_dir() / f"preview_{m}_{key}.wav"


def ensure_preview_wav(mode: str, voice: str, *, provider_factory=None) -> Path:
    """Return a cached WAV sample for ``(mode, voice)``, synthesizing if needed."""
    m = normalize_mode(mode)
    v = resolve_voice(voice, m)
    out = preview_wav_path(m, v)
    if out.is_file() and out.stat().st_size >= 400:
        return out
    part = out.with_name(f"{out.stem}.{threading.get_ident()}.part{out.suffix}")
    part.unlink(missing_ok=True)
    provider = provider_factory(m) if provider_factory else VieNeuTTSProvider(mode=m)
    try:
        provider.synthesize(SAMPLE_TEXT, v, part)
        if part.is_file() and part.stat().st_size >= 400:
            part.replace(out)
        else:
            raise RuntimeError("voice preview synthesis produced empty audio")
    finally:
        part.unlink(missing_ok=True)
        close_fn = getattr(provider, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                pass
    return out


def stop_playback() -> None:
    """Stop any currently playing asynchronous preview sound."""
    if sys.platform == "win32":
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass


def play_wav_async(path: Path) -> None:
    """Play a WAV file asynchronously without blocking the UI thread."""
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return
    # Non-Windows fallback: open with default handler.
    from dubtool import proc

    proc.run(["xdg-open", str(path)], capture=False, gui=True)


def synthesize_in_background(mode: str, voice: str, on_done) -> threading.Thread:
    """Run ``ensure_preview_wav`` on a daemon thread and call ``on_done(path, err)``."""
    def _worker() -> None:
        try:
            wav = ensure_preview_wav(mode, voice)
            on_done(wav, None)
        except Exception as exc:
            log.warning("voice preview failed (mode=%s, voice=%s): %s", mode, voice, exc)
            on_done(None, exc)

    t = threading.Thread(target=_worker, daemon=True, name="dubtool-voice-preview")
    t.start()
    return t


def make_preview_button(get_mode_and_voice, parent=None):
    """Create a QPushButton that synthesizes (off-thread) and plays a voice sample."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QMessageBox, QPushButton
    from dubtool.app import i18n

    class _Bridge(QObject):
        done = Signal(object, object)

    btn = QPushButton(i18n.tr("options.preview_voice"), parent)
    btn.setObjectName("preview_voice_btn")
    bridge = _Bridge(btn)
    btn._preview_bridge = bridge  # keep alive with button
    btn._preview_busy = False

    def _on_done(wav, err) -> None:
        btn._preview_busy = False
        btn.setEnabled(True)
        btn.setText(i18n.tr("options.preview_voice"))
        if err is not None or wav is None:
            QMessageBox.warning(
                btn.window(),
                i18n.tr("options.preview_voice"),
                i18n.tr("options.preview_error", err=str(err or "unknown")),
            )
            return
        try:
            play_wav_async(Path(wav))
        except Exception as exc:
            QMessageBox.warning(
                btn.window(),
                i18n.tr("options.preview_voice"),
                i18n.tr("options.preview_error", err=str(exc)),
            )

    bridge.done.connect(_on_done)

    def _clicked() -> None:
        if btn._preview_busy:
            return
        stop_playback()
        mode, voice = get_mode_and_voice()
        btn._preview_busy = True
        btn.setEnabled(False)
        btn.setText(i18n.tr("options.preview_loading"))
        def _safe_emit(wav, err) -> None:
            try:
                import shiboken6

                if not shiboken6.isValid(bridge):
                    return
            except Exception:
                pass
            try:
                bridge.done.emit(wav, err)
            except RuntimeError:
                pass  # button/dialog closed while synthesizing

        synthesize_in_background(mode, voice, _safe_emit)

    btn.clicked.connect(_clicked)
    return btn
