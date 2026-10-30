"""Backup-voice support (AGENT_UPDATE_8.md 2.3).

`BackupTTS(primary, secondary)` wraps the existing TTSProvider interface: it
tries the primary provider first and only falls back to `secondary` when the
primary raises TTSFailure. Every fallback use must be counted by the caller
(count_fallback) and reported in the run summary — a different voice on a few
segments is only acceptable because the alternative is a failed job.

Current evaluation (see REPORT_UPDATE_8.md):
- windows_sapi: NOT implemented — this machine has no Vietnamese SAPI voice
  (only David/Zira en-US and Hedda de-DE are installed), so per the update
  spec the provider is reported and skipped.
- piper: implemented behind `PiperTTSProvider`, which is only usable when the
  `piper` package and a configured voice model are present (config
  tts.piper_voice); nothing is bundled without license verification and the
  user's explicit choice (AGU8 2.3).
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path

log = logging.getLogger("dubtool")

from dubtool.tts.base import TTSFailure, TTSProvider  # noqa: E402


class BackupTTS:
    """Primary provider with a backup (AGU8 2.3). Counts and reports uses."""

    def __init__(self, primary: TTSProvider, secondary: TTSProvider | None = None):
        self.primary = primary
        self.secondary = secondary
        self.fallback_uses = 0  # how many segments were synthesized by secondary

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        try:
            self.primary.synthesize(text, voice, out_path)
            return
        except TTSFailure as exc:
            if self.secondary is None:
                raise
            log.warning(
                "tts: primary voice failed (%s) — using the backup voice for this segment",
                exc,
            )
        # The backup voice is its own configured voice; the per-segment voice
        # argument belongs to the primary.
        try:
            self.secondary.synthesize(text, "", out_path)
        except Exception as exc2:
            raise TTSFailure(
                f"primary and backup voices both failed for this segment: {exc2}"
            ) from exc2
        self.fallback_uses += 1


class PiperTTSProvider:
    """Local offline neural TTS through the `piper` package.

    Usable only when `piper` is importable and a voice model path is given.
    The model is NOT bundled by DubTool (license + user choice first, AGU8
    2.3); the user points tts.piper_voice at an .onnx voice file.
    """

    name = "piper"

    def __init__(self, voice_path: str):
        try:
            from piper import PiperVoice  # type: ignore
        except Exception as exc:  # pragma: no cover - env dependent
            raise TTSFailure(
                f"piper fallback is configured but the 'piper' package is not "
                f"installed: {exc}. Install it with: pip install piper-tts"
            ) from exc
        path = Path(voice_path)
        if not path.exists():
            raise TTSFailure(
                f"piper fallback is configured but the voice model {path} was "
                "not found (set tts.piper_voice in config)"
            )
        self.voice_path = path
        self._voice = PiperVoice.load(str(path))
        self.rate_limit_s = 0.05

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        # piper renders its own voice; the `voice` argument is ignored.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with wave.open(str(out_path), "wb") as wav_file:
                self._voice.synthesize_wav(text, wav_file)
        except Exception as exc:
            raise TTSFailure(f"piper synthesis failed: {exc}") from exc


def build_backup(config) -> TTSProvider | None:
    """The configured backup provider (config tts.fallback), or None when it
    is 'none' or cannot be built (logged — never fatal)."""
    mode = str((getattr(config, "tts", {}) or {}).get("fallback", "none")).lower()
    if mode in ("", "none"):
        return None
    try:
        if mode == "windows_sapi":
            # Evaluation found no Vietnamese SAPI voice on this machine
            # (David/Zira en-US, Hedda de-DE only) — AGU8 2.3 says do not
            # implement it then; keep the message precise for any machine.
            raise TTSFailure(
                "the Windows SAPI fallback is not available: no Vietnamese "
                "(vi-VN) voice is installed on this machine (Settings → "
                "Time & Language → Speech can add one)"
            )
        if mode == "piper":
            voice_path = str((getattr(config, "tts", {}) or {}).get("piper_voice", "") or "")
            return PiperTTSProvider(voice_path)
        raise TTSFailure(f"unknown tts.fallback value: {mode!r}")
    except Exception as exc:
        log.warning("tts: backup voice unavailable, continuing with the primary only: %s", exc)
        return None
