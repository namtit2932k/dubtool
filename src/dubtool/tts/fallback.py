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

import base64
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
        self.label = f"Piper ({path.stem})"
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


def detect_winrt_voices() -> list[tuple[str, str]]:
    """Voices installed for Windows WinRT SpeechSynthesis (OneCore), returned
    as `(display_name, language)` pairs with Vietnamese voices listed first."""
    try:
        lines = _powershell(
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
            "$null=[Windows.Media.SpeechSynthesis.SpeechSynthesizer,"
            "Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]; "
            "[Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | "
            'ForEach-Object { "$($_.DisplayName)|$($_.Language)" }'
        ).splitlines()
    except Exception:
        return []
    voices: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        name, _, lang = line.partition("|")
        name, lang = name.strip(), lang.strip()
        if name and name not in seen:
            seen.add(name)
            voices.append((name, lang))
    voices.sort(
        key=lambda item: (
            0 if item[1].lower().startswith("vi") else 1,
            item[1].lower(),
            item[0].lower(),
        )
    )
    return voices


class WindowsWinRtProvider:
    """Local Windows OneCore / WinRT voice via PowerShell SpeechSynthesizer.

    Unlike legacy .NET System.Speech (`WindowsSapiProvider`), WinRT exposes
    modern Windows language-pack voices such as Microsoft An (vi-VN) without
    modifying the registry.
    """

    name = "windows_winrt"
    rate_limit_s = 0.05

    def __init__(self, voice_name: str = ""):
        self.voice_name = str(voice_name or "").strip()
        installed = detect_winrt_voices()
        if not installed:
            raise TTSFailure(
                "the Windows WinRT speech voices are not available "
                "(Settings → Time & Language → Speech)"
            )
        by_name = {name: lang for name, lang in installed}
        if self.voice_name and self.voice_name not in by_name:
            raise TTSFailure(
                f"Windows WinRT voice {self.voice_name!r} is not installed; "
                f"installed: {', '.join(list(by_name)[:6])}"
            )
        if not self.voice_name:
            self.voice_name = installed[0][0]
        lang = by_name.get(self.voice_name, "")
        self.label = f"{self.voice_name} ({lang})" if lang else self.voice_name

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".wav":
            target, convert = out_path, False
        else:
            target, convert = out_path.with_suffix(".wav"), True
        # Base64 UTF-8 prevents shell quoting bugs and Windows codepage mangling
        # of Vietnamese diacritics across python -> powershell arguments.
        voice_b64 = base64.b64encode(self.voice_name.encode("utf-8")).decode("ascii")
        text_b64 = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
        out_b64 = base64.b64encode(str(target.resolve()).encode("utf-8")).decode("ascii")
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
            "$null=[Windows.Media.SpeechSynthesis.SpeechSynthesizer,"
            "Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]; "
            "$null=[Windows.Storage.Streams.DataReader,"
            "Windows.Storage.Streams,ContentType=WindowsRuntime]; "
            "$asTaskGeneric=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { "
            "$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and "
            "$_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
            "function Await($WinRtTask,$ResultType){ "
            "$netTask=$asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null,@($WinRtTask)); "
            "$netTask.Wait(-1) | Out-Null; $netTask.Result }; "
            "$synth=New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer; "
            f"$vname=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{voice_b64}')); "
            "$match=[Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | "
            "Where-Object { $_.DisplayName -eq $vname } | Select-Object -First 1; "
            'if (-not $match) { throw "Voice not found: $vname" }; '
            "$synth.Voice=$match; "
            f"$txt=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{text_b64}')); "
            "$stream=Await ($synth.SynthesizeTextToStreamAsync($txt)) "
            "([Windows.Media.SpeechSynthesis.SpeechSynthesisStream]); "
            "$size=[uint32]$stream.Size; "
            "$reader=New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0)); "
            "$null=Await ($reader.LoadAsync($size)) ([uint32]); "
            "$bytes=New-Object byte[] $size; "
            "$reader.ReadBytes($bytes); "
            f"$outPath=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{out_b64}')); "
            "[IO.File]::WriteAllBytes($outPath,$bytes); "
            "$reader.Dispose(); $stream.Dispose(); $synth.Dispose()"
        )
        try:
            _powershell(script)
            if not target.exists() or target.stat().st_size < 400:
                raise TTSFailure("the Windows WinRT backup produced no audio")
            if convert:
                _wav_to_mp3(target, out_path)
        finally:
            if convert:
                target.unlink(missing_ok=True)


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
        self.label = f"{self.voice_name} (Windows SAPI)" if self.voice_name else "Windows SAPI"

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


def tts_overrides_for_backup(setting_value: str) -> dict[str, str]:
    """Map a Settings `tts_backup_voice` string to `config.tts` fields."""
    raw = str(setting_value or "").strip()
    if raw.startswith("windows_winrt:"):
        return {"fallback": "windows_winrt", "winrt_voice": raw.split(":", 1)[1]}
    if raw.startswith("windows_sapi:"):
        return {"fallback": "windows_sapi", "sapi_voice": raw.split(":", 1)[1]}
    if raw.startswith("piper:"):
        return {"fallback": "piper", "piper_voice": raw.split(":", 1)[1]}
    return {}


def _build_backup_from_cfg(tts_cfg: dict) -> TTSProvider | None:
    mode = str((tts_cfg or {}).get("fallback", "none")).lower()
    if mode in ("", "none"):
        return None
    if mode == "windows_winrt":
        return WindowsWinRtProvider(str(tts_cfg.get("winrt_voice", "") or ""))
    if mode == "windows_sapi":
        return WindowsSapiProvider(str(tts_cfg.get("sapi_voice", "") or ""))
    if mode == "piper":
        return PiperTTSProvider(str(tts_cfg.get("piper_voice", "") or ""))
    raise TTSFailure(f"unknown tts.fallback value: {mode!r}")


def build_backup_for_setting(setting_value: str) -> TTSProvider:
    """Build a backup provider directly from a Settings `tts_backup_voice`
    key, raising TTSFailure if unconfigured or unavailable (used by Settings Listen)."""
    tts_cfg = tts_overrides_for_backup(setting_value)
    provider = _build_backup_from_cfg(tts_cfg)
    if provider is None:
        raise TTSFailure("no backup voice is selected")
    return provider


def build_backup(config) -> TTSProvider | None:
    """The configured backup provider (config tts.fallback), or None when it
    is 'none' or cannot be built (logged — never fatal)."""
    tts_cfg = getattr(config, "tts", {}) or {}
    if str(tts_cfg.get("fallback", "none")).lower() in ("", "none"):
        return None
    try:
        return _build_backup_from_cfg(tts_cfg)
    except Exception as exc:
        log.warning("tts: backup voice unavailable, continuing with the primary only: %s", exc)
        return None
