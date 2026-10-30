"""TTS failure records (AGENT_UPDATE_3.md task 6).

Every TTS failure is logged with context (segment id, chars, attempt, error,
elapsed, text truncated to 120 chars) and appended to
<job>/tts_failures.json. End-of-stage summaries report only numbers (counts,
char-stats, digit/symbol rates) — never a claimed cause.
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from pathlib import Path

from dubtool.job import read_json, write_json

log = logging.getLogger("dubtool")

MAX_TEXT_LOG_CHARS = 120

_DIGIT_OR_SYMBOL = re.compile(r"[\d\W]")


def _exc_kind(error: Exception | str) -> str:
    """AGU8 2.2: the failure is recorded by exception TYPE (e.g.
    NoAudioReceived, HTTP-status handshake errors), not just its message."""
    if isinstance(error, BaseException):
        # Fold the full cause chain into one dotted name for grouping.
        names = [type(error).__name__]
        seen = {id(error)}
        link = error.__cause__ or error.__context__
        while link is not None and id(link) not in seen:
            names.append(type(link).__name__)
            seen.add(id(link))
            link = link.__cause__ or link.__context__
        return " <- ".join(names)
    return "message"


def _edge_tts_version() -> str:
    try:
        import edge_tts

        return str(getattr(edge_tts, "__version__", "unknown"))
    except Exception:
        return "unavailable"


class TTSFailureLog:
    def __init__(self, job_dir: Path):
        self.path = Path(job_dir) / "tts_failures.json"
        self.records: list[dict] = []
        if self.path.exists():
            try:
                data = read_json(self.path)
                if isinstance(data, list):
                    self.records = data
            except Exception:
                self.records = []

    def record(
        self,
        seg_id: int,
        attempt: int,
        text: str,
        error: Exception | str,
        elapsed_s: float,
        *,
        voice: str = "",
        rate: str = "",
        details: dict | None = None,
    ) -> None:
        message = str(error)
        log.warning(
            "tts failure: kind=%s id=%s attempt=%d chars=%d elapsed=%.1fs error=%s "
            "voice=%s rate=%s edge-tts=%s text=%r",
            _exc_kind(error), seg_id, attempt, len(text), elapsed_s, message,
            voice or "?", rate or "?", _edge_tts_version(),
            text[:MAX_TEXT_LOG_CHARS],
        )
        record: dict = {
            "id": seg_id,
            "attempt": attempt,
            "chars": len(text),
            "text": text[:MAX_TEXT_LOG_CHARS],
            "error": message,
            "error_kind": _exc_kind(error),
            "voice": voice or "?",
            "rate": rate or "?",
            "edge_tts_version": _edge_tts_version(),
            "elapsed_s": round(elapsed_s, 2),
        }
        if details:
            record.update({k: v for k, v in details.items() if k not in record})
        self.records.append(record)
        self.save()

    def save(self) -> None:
        write_json(self.path, self.records)

    # ------------------------------------------------------------ summaries
    @staticmethod
    def _digit_symbol_rate(texts: list[str]) -> float:
        if not texts:
            return 0.0
        hits = sum(1 for t in texts if _DIGIT_OR_SYMBOL.search(t or ""))
        return 100.0 * hits / len(texts)

    def summary(self, all_texts: list[str], max_attempts: int = 99) -> str:
        """Numbers only — no cause is claimed, per AGENT_UPDATE_3.md."""
        total = len(self.records)
        tried_ids = {r["id"] for r in self.records}
        per_id: dict = {}
        for r in self.records:
            per_id.setdefault(r["id"], []).append(r["attempt"])
        failed_all = sum(1 for a in per_id.values() if min(a) == 1 and len({*a}) >= max_attempts)
        failing_chars = [r["chars"] for r in self.records]
        parts = [
            f"tts-failure summary: total_failures={total} segments_with_failures={len(tried_ids)} "
            f"failed_all_attempts={failed_all}",
        ]
        if failing_chars:
            parts.append(
                f"chars_of_failing mean={statistics.mean(failing_chars):.1f} "
                f"median={statistics.median(failing_chars):.1f}"
            )
            if all_texts:
                all_lens = [len(t or "") for t in all_texts]
                parts.append(
                    f"chars_of_all mean={statistics.mean(all_lens):.1f} "
                    f"median={statistics.median(all_lens):.1f}"
                )
            parts.append(
                f"digit_or_symbol_rate failing={self._digit_symbol_rate([r.get('text','') for r in self.records]):.0f}% "
                f"all={self._digit_symbol_rate(all_texts):.0f}%"
            )
        return " | ".join(parts)


class Timer:
    """Elapsed-seconds helper for failure records."""

    def __init__(self) -> None:
        self.start = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.start
