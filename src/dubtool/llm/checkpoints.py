"""Update 12 task 2.3: per-chunk translation checkpoints.

After each chunk completes, the stage writes its result atomically to
`translate_chunks.json` in the job folder: a record per chunk with the
unit ids, a hash of the source text, the model, the hash of the prompt +
glossary + pronoun settings, the Vietnamese results, and a timestamp.

On resume (after an error, a cancel, or a closed app) every finished
chunk of this job is reused and only the missing ones are requested.
A failed chunk never discards the others. "Start over" clears the file.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from dubtool.job import write_json

_LOCK = threading.Lock()


def source_hash(payloads: list[dict]) -> str:
    """A stable hash of the chunk's source (unit ids + English text)."""
    payload = [
        [int(p["id"]), (p.get("text") or "")] for p in payloads
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def chunk_prompt_hash(glossary: dict | None, pronouns: dict | None,
                      policy: str | None = None) -> str:
    """A stable hash of everything that shapes the prompt: glossary,
    pronoun settings, and the filler/loyalty policy text."""
    payload = {
        "glossary": glossary or {},
        "pronouns": {k: v for k, v in (pronouns or {}).items() if v},
        "policy": policy or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ChunkCheckpointStore:
    """Thread-safe, atomically written chunk store for one job."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = loaded
            except (ValueError, OSError):
                self._data = {}

    # ------------------------------------------------------------- lookups
    def get(self, chunk_no: int, payloads: list[dict],
            prompt_hash: str) -> dict | None:
        """The finished chunk's record, or None (a different chunk number,
        source, or prompt shape never matches)."""
        with _LOCK:
            rec = self._data.get(str(int(chunk_no)))
        if not isinstance(rec, dict):
            return None
        if rec.get("source_hash") != source_hash(payloads):
            return None
        if rec.get("prompt_hash") != prompt_hash:
            return None
        return rec

    # -------------------------------------------------------------- writes
    def save(self, chunk_no: int, payloads: list[dict], *, model: str,
             prompt_hash: str, vi: list[dict]) -> None:
        record = {
            "chunk": int(chunk_no),
            "unit_ids": [int(p["id"]) for p in payloads],
            "source_hash": source_hash(payloads),
            "model": model,
            "prompt_hash": prompt_hash,
            "vi": list(vi),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with _LOCK:
            self._data[str(int(chunk_no))] = record
            self._flush()

    def clear(self) -> None:
        """'Start over': the store forgets every chunk."""
        with _LOCK:
            self._data = {}
            self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.path, self._data)
