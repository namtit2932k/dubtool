"""TTSProvider interface (AGENT.md section 5, stage 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TTSFailure(RuntimeError):
    """Raised when a provider cannot produce usable audio."""


class TTSProvider(Protocol):
    def synthesize(self, text: str, voice: str, out_path: Path) -> None: ...
