"""TTSProvider interface (AGENT.md section 5, stage 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TTSFailure(RuntimeError):
    """Raised when a provider cannot produce usable audio."""


class TTSUnavailableError(RuntimeError):
    """AGENT_UPDATE_8.md 2.4: every segment kept failing through all
    retry rounds and no fallback was configured. The stage raises this
    dedicated type (never a generic RuntimeError) after PRESERVING every
    finished clip, so the job stays resumable.

    `ids`: the segment ids still without audio; `finished`: how many clips
    were kept."""

    def __init__(self, ids: list[int], finished: int):
        super().__init__(
            f"the voice service did not respond for segment(s) {ids[:5]}"
            f"{'…' if len(ids) > 5 else ''}; {finished} finished clips are "
            "saved — the run can be resumed in a few minutes, the voice can "
            "be changed in Settings, or a backup voice can be enabled"
        )
        self.ids = ids
        self.finished = finished


class TTSProvider(Protocol):
    def synthesize(self, text: str, voice: str, out_path: Path) -> None: ...
