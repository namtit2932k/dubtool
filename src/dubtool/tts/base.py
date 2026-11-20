"""TTSProvider interface (AGENT.md section 5, stage 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TTSFailure(RuntimeError):
    """Raised when a provider cannot produce usable audio."""


class TTSEngineInitError(TTSFailure):
    """Raised when the local TTS engine itself cannot be loaded/initialized.

    Unlike per-segment synthesis errors, retrying every remaining segment
    cannot recover from an engine initialization failure, so stage 4 fails fast.
    """


class TTSUnavailableError(RuntimeError):
    """AGENT_UPDATE_8.md 2.4: every segment kept failing through all
    retry rounds and no fallback was configured. The stage raises this
    dedicated type (never a generic RuntimeError) after PRESERVING every
    finished clip, so the job stays resumable.

    `ids`: the segment ids still without audio; `finished`: how many clips
    were kept."""

    def __init__(self, ids: list[int], finished: int):
        super().__init__(
            f"local Vietnamese TTS failed for segment(s) {ids[:5]}"
            f"{'…' if len(ids) > 5 else ''}; {finished} finished clips are "
            "saved — check your network (for first-time model download) or "
            "switch to Nano mode in Settings"
        )
        self.ids = ids
        self.finished = finished


class TTSProvider(Protocol):
    def synthesize(self, text: str, voice: str, out_path: Path) -> None: ...
