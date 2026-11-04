"""Backup-voice support (AGENT_UPDATE_8.md 2.3; AGENT_UPDATE_11 3.5).

`BackupTTS(primary, secondary)` wraps the existing TTSProvider interface: it
tries the primary provider first and only falls back to `secondary` when the
primary raises TTSFailure. Every fallback use must be counted by the caller
(count_fallback) and reported in the run summary — a different voice on a few
segments is only acceptable because the alternative is a failed job.

Backup options (AGU11 3.5 — the Settings UI lists what actually exists):
- windows_sapi: the voices installed for .NET System.Speech, driven through
  PowerShell (this machine has only en-US/de-DE voices, so the run reports
  precisely what is missing instead of pretending).
- piper: `PiperTTSProvider`, only usable when the `piper` package and a
  configured voice model are present (config tts.piper_voice); nothing is
  bundled without license verification and the user's explicit choice.
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

    @staticmethod
    def detect_piper_voices() -> list[str]:
        """AGU11 3.5 scan for installed .onnx voice models at the common
        locations (no download, no assumed licence)."""
        import os

        roots: list[Path] = []
        for env in ("LOCALAPPDATA", "APPDATA"):
            base = os.environ.get(env)
            if base:
                roots.append(Path(base) / "piper")
                roots.append(Path(base) / "piper-tts")
        roots.append(Path.cwd() / "piper")
        found: set[str] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*.onnx"):
                if path.is_file():
                    found.add(str(path))
        return sorted(found)


def _powershell(script: str) -> str:
    from dubtool import proc

    out = proc.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=60, capture=True,
    )
    if getattr(out, "returncode", 1) != 0:
        raise TTSFailure(f"Windows SAPI helper failed: {(out.stderr or '').strip()[:200]}")
    return out.stdout or ""


def detect_sapi_voices() -> list[str]:
    """Names of the voices installed for Windows SAPI (.NET System.Speech)."""
    try:
        lines = _powershell(
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
        ).splitlines()
        return [line.strip() for line in lines if line.strip()]
    except Exception:
        return []


class WindowsSapiProvider:
    """Local Windows SAPI voice through PowerShell (System.Speech).

    AGU11 3.5: the backup voice is a real option on machines that have a
    matching voice; the Settings UI lists only what exists."""

    name = "windows_sapi"
    rate_limit_s = 0.05

    def __init__(self, voice_name: str = ""):
        self.voice_name = str(voice_name or "")
        installed = detect_sapi_voices()
        if not installed:
            raise TTSFailure(
                "the Windows SAPI fallback is not available: no voices are "
                "installed (Settings → Time & Language → Speech)"
            )
        if self.voice_name and self.voice_name not in installed:
            raise TTSFailure(
                f"Windows SAPI voice {self.voice_name!r} is not installed; "
                f"installed: {', '.join(installed[:6])}"
            )

    def synthesize(self, text, voice, out_path) -> None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".wav":
            target, convert = out_path, False
        else:
            target, convert = out_path.with_suffix(".wav"), True
        select = f"$s.SelectVoice('{self.voice_name}'); " if self.voice_name else ""
        spoken = text.replace("'", "")
        _powershell(
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            + select
            + f"$s.SetOutputToWaveFile('{target.as_posix()}'); $s.Speak('{spoken}')"
        )
        if not target.exists() or target.stat().st_size < 400:
            raise TTSFailure("the Windows SAPI backup produced no audio")
        if convert:
            _wav_to_mp3(target, out_path)


def _wav_to_mp3(src: Path, dst: Path) -> None:
    """Bundle-safe wav to mp3 for the SAPI backup (bundled ffmpeg)."""
    from dubtool import audio_utils, proc

    result = proc.run(
        [audio_utils.ffmpeg_bin(), "-y", "-loglevel", "error",
         "-i", str(src), "-q:a", "4", str(dst)],
        capture=True,
    )
    if result.returncode != 0:
        raise TTSFailure(f"wav-to-mp3 conversion failed: {result.returncode}")
    src.unlink(missing_ok=True)


def build_backup(config) -> TTSProvider | None:
    """The configured backup provider (config tts.fallback), or None when it
    is 'none' or cannot be built (logged — never fatal)."""
    mode = str((getattr(config, "tts", {}) or {}).get("fallback", "none")).lower()
    if mode in ("", "none"):
        return None
    try:
        if mode == "windows_sapi":
            tts_cfg = getattr(config, "tts", {}) or {}
            return WindowsSapiProvider(str(tts_cfg.get("sapi_voice", "") or ""))
        if mode == "piper":
            voice_path = str((getattr(config, "tts", {}) or {}).get("piper_voice", "") or "")
            return PiperTTSProvider(voice_path)
        raise TTSFailure(f"unknown tts.fallback value: {mode!r}")
    except Exception as exc:
        log.warning("tts: backup voice unavailable, continuing with the primary only: %s", exc)
        return None
