"""Stage 3: translate transcript to Vietnamese in chunks via the LLM wrapper.

Modes (AGENT_UPDATE_3.md task 7):
- `translate.unit_mode: sentence` (default): segments are grouped into
  sentence-level units (units.py), whole units are translated, and the
  Vietnamese is divided back across the original segments with
  split_translation. The translated.json contract is unchanged: one entry
  per segment with the same fields.
- `translate.unit_mode: segment`: the previous per-segment behavior.

Both modes keep: providers resolved through dubtool.llm.get_provider, one-shot
max_chars enforcement pass (tolerance translate.budget_overshoot), crash-safe
incremental writes, and the final summary.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from dubtool.job import StageCancelled, read_json, write_json
from dubtool.llm import LLMClient, get_provider
from dubtool.units import build_units, split_translation
from dubtool.llm.base import RateLimitError

log = logging.getLogger("dubtool")

CONTEXT_SEGMENTS = 3


def build_translated(transcript: list[dict[str, Any]], chars_per_sec: float) -> list[dict[str, Any]]:
    """Fresh translated.json entries from transcript.json (section 4 shape)."""
    entries: list[dict[str, Any]] = []
    for seg in transcript:
        max_chars = int((seg["end"] - seg["start"]) * chars_per_sec)
        entries.append(
            {
                "id": seg["id"],
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "vi": None,
                "max_chars": max_chars,
                "reviewed": False,
                "tts_duration": None,
                "fit_status": "pending",
            }
        )
    return entries


def merge_translations(
    entries: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> None:
    """Apply [{"id","vi"}] updates in place (never touching reviewed lines)."""
    by_id = {e["id"]: e for e in entries}
    for upd in updates:
        entry = by_id.get(upd["id"])
        if entry is None:
            log.warning("s3: translation for unknown id %d ignored", upd["id"])
            continue
        if entry.get("reviewed"):
            continue  # human-edited lines are never overwritten
        entry["vi"] = upd["vi"]


def apply_unit_translation(
    entries: list[dict[str, Any]], unit: dict[str, Any], vi_text: str
) -> bool:
    """Divide one unit's Vietnamese across its segments (returns True when
    at least one entry changed). Reviewed segments keep their vi.

    Raises RuntimeError when the unit's text comes back empty.
    """
    seg_ids = unit["seg_ids"]
    by_id = {e["id"]: e for e in entries}
    segments = [by_id[i] for i in seg_ids if i in by_id]
    if not (vi_text or "").strip():
        raise RuntimeError(f"unit {unit['unit_id']} translated to empty text")
    parts = split_translation(vi_text, segments)
    changed = False
    for seg, part in zip(segments, parts):
        if seg.get("reviewed"):
            continue
        if not part or not part.strip():  # never write an empty translation
            continue
        seg["vi"] = part.strip()
        changed = True
    return changed


def chunks_of(entries: list[dict[str, Any]], size: int):
    for i in range(0, len(entries), size):
        yield entries[i : i + size]


def run_chunks_parallel(
    chunks: list[list[dict[str, Any]]],
    chunk_fn: Callable[[Any], list[dict[str, Any]]],
    concurrency: int,
    apply: Callable[[int, list[dict[str, Any]]], None] | None = None,
    cancel: threading.Event | None = None,
):
    """AGU4 4.1: translate chunks with up to `concurrency` parallel calls,
    preserving output order (results are applied by chunk index).

    On a rate limit (429), back off for the provider's retry hint and reduce
    the concurrency by one for the rest of the run (never below 1); the chunk
    is re-submitted and retried under the existing retry rules. On any other
    failure, the remaining futures of the batch still finish, their results
    are applied, and the error is re-raised (resume-safe like a sequential
    run). `cancel` (AGU4 6.3) is checked between chunks, after each completed
    future, and while waiting on a retry-after hint.
    """

    def _check_cancel() -> None:
        if cancel is not None and cancel.is_set():
            raise StageCancelled("s3: cancelled by user")

    def deliver(idx: int, updates) -> None:
        if apply is not None:
            apply(idx, updates)

    if concurrency <= 1:
        for i, chunk in enumerate(chunks):
            _check_cancel()
            deliver(i, chunk_fn(chunk))
        return

    workers = max(1, concurrency)
    pending = {i: chunk for i, chunk in enumerate(chunks)}
    next_to_apply = 0
    collected: dict[int, Any] = {}

    def flush_in_order() -> None:
        nonlocal next_to_apply
        while next_to_apply in collected:
            _check_cancel()
            deliver(next_to_apply, collected.pop(next_to_apply))
            next_to_apply += 1

    guard = 0
    while pending and guard < 10 * len(chunks) + 10:
        guard += 1
        _check_cancel()
        batch = dict(list(pending.items())[:workers])
        for idx in batch:
            del pending[idx]
        rate_limited: dict[int, Any] = {}
        wait_hint = 0.0
        first_error: Exception | None = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(chunk_fn, chunk): idx for idx, chunk in batch.items()}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    collected[idx] = future.result()
                except RateLimitError as exc:
                    rate_limited[idx] = batch[idx]
                    hint = float(getattr(exc, "retry_after", 0) or 0)
                    wait_hint = max(wait_hint, hint)
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
                    log.warning("s3: translation chunk %s failed: %s", idx, exc)

        # A 429 reduces concurrency for the rest of the run, never below 1.
        if rate_limited:
            workers = max(1, workers - 1)
            log.warning(
                "s3: HTTP 429 — reducing translation concurrency to %d for the rest of the run",
                workers,
            )
        pending.update(rate_limited)  # retried under the existing retry rules

        # Apply successful results strictly in chunk order.
        flush_in_order()

        if rate_limited and wait_hint > 0:
            # Sleep only until the hint expires or the user cancels.
            remaining = max(0.0, min(wait_hint, 300.0))
            while remaining > 0:
                _check_cancel()
                step = min(1.0, remaining)
                time.sleep(step)
                remaining -= step
        if first_error is not None:
            raise first_error
    flush_in_order()
    if pending:
        raise RuntimeError(
            f"s3: {len(pending)} translation chunk(s) did not complete after repeated rate limits"
        )


def count_over_budget(
    entries: list[dict[str, Any]], tolerance: float = 1.15
) -> list[dict[str, Any]]:
    """Segments whose vi exceeds max_chars * tolerance, in id order."""
    return [
        e
        for e in entries
        if e.get("vi") and not e.get("reviewed") and len(e["vi"]) > int(e["max_chars"] * tolerance)
    ]


def units_for(entries: list[dict[str, Any]], units_cfg: dict) -> list[dict[str, Any]]:
    """Group the translated entries into units; each unit carries the sum of
    its segments' max_chars and its segments list."""
    segs = [
        {"id": e["id"], "start": e["start"], "end": e["end"], "text": e.get("text") or ""}
        for e in entries
    ]
    units = build_units(
        segs,
        max_segments=int(units_cfg.get("max_segments", 4)),
        max_duration_s=float(units_cfg.get("max_duration_s", 14.0)),
        max_gap_s=float(units_cfg.get("max_gap_s", 1.0)),
    )
    by_id = {e["id"]: e for e in entries}
    for u in units:
        members = [by_id[i] for i in u["seg_ids"] if i in by_id]
        u["segments"] = members
        u["max_chars"] = sum(int(m["max_chars"]) for m in members)
    return units


def write_units_file(job, units: list[dict[str, Any]]) -> None:
    path = job.dir / "units.json"
    write_json(
        path,
        [
            {
                "unit_id": u["unit_id"],
                "seg_ids": u["seg_ids"],
                "text": u["text"],
                "start": u["start"],
                "end": u["end"],
                "max_chars": u["max_chars"],
            }
            for u in units
        ],
    )


def enforce_budget(
    entries: list[dict[str, Any]], client: LLMClient, tolerance: float
) -> tuple[int, int]:
    """The existing one-shot enforcement pass, now with configurable tolerance."""
    over_before = count_over_budget(entries, tolerance)
    if over_before:
        items = [
            {
                "id": e["id"],
                "text": e["text"],
                "vi": e["vi"],
                "max_chars": e["max_chars"],
            }
            for e in over_before
        ]
        log.info("s3: enforcement pass on %d over-budget segments", len(items))
        try:
            shortened = client.shorten_batch(items)
            merge_translations(entries, shortened)
        except Exception as exc:  # enforcement is best-effort; stage 5 recovers
            log.warning("s3: enforcement pass failed, leaving as is: %s", exc)
    over_after = count_over_budget(entries, tolerance)
    return len(over_before), len(over_after)


def _run_segment_mode(
    job, entries, client, chunk_size, glossary, brief, force: bool,
    pronouns: dict | None = None, concurrency: int = 1,
    cancel: threading.Event | None = None,
):
    """Previous behavior: translate segment by segment."""
    todo = []
    for entry in entries:
        if entry.get("reviewed"):
            continue
        if not force and entry.get("vi"):
            continue
        todo.append(entry)

    translated = 0
    todo_chunks = list(enumerate(chunks_of(todo, chunk_size), start=1))

    def chunk_fn(chunk_with_index):
        chunk_index, chunk = chunk_with_index
        context_pool = [e for e in todo if e["id"] < chunk[0]["id"]]
        context = context_pool[-CONTEXT_SEGMENTS:] if context_pool else []
        context_payload = [
            {"id": e["id"], "text": e["text"], "vi": e.get("vi") or ""} for e in context
        ]
        return client.translate_batch(
            chunk, context_payload, glossary, brief=brief, pronouns=pronouns
        )

    def apply_chunk(idx, updates):
        nonlocal translated
        chunk_index, _chunk = todo_chunks[idx]
        merge_translations(entries, updates)
        translated += len(updates)
        log.info("s3: chunk %d done (%d/%d segments)", chunk_index, translated, len(todo))
        write_json(job.translated_path, entries)

    run_chunks_parallel(todo_chunks, chunk_fn, concurrency, apply=apply_chunk, cancel=cancel)
    return translated




def run(
    job, config, force: bool = False, provider_id: str | None = None,
    cancel: threading.Event | None = None,
) -> None:
    from dubtool.brief import resolve_brief

    if cancel is not None and cancel.is_set():
        raise StageCancelled("s3: cancelled by user")

    transcript = read_json(job.transcript_path)
    existing: list[dict[str, Any]] | None = None
    if job.translated_path.exists():
        existing = read_json(job.translated_path)
        if not isinstance(existing, list):
            log.warning("s3: translated.json has unexpected format, regenerating")
            existing = None

    if existing is None:
        entries = build_translated(transcript, config.chars_per_sec)
    else:
        entries = existing
        have = {e["id"] for e in entries}
        for seg in transcript:
            if seg["id"] not in have:
                entries.extend(build_translated([seg], config.chars_per_sec))
        entries.sort(key=lambda e: e["id"])

    pid = (provider_id or config.llm_provider).lower()
    provider = get_provider(config, pid)
    translate_cfg = config.translate or {}
    concurrency = max(1, int(translate_cfg.get("concurrency", 1)))
    pronouns = {k: v for k, v in (translate_cfg.get("pronouns") or {}).items() if v}
    client = LLMClient(provider)
    chunk_size = config.chunk_size(pid)
    log.info(
        "s3: provider=%s model=%s chunk_size=%d concurrency=%d",
        provider.name, provider.model, chunk_size, concurrency,
    )

    glossary = config.glossary
    brief = resolve_brief(job_dir=job.dir)
    if brief:
        log.info("s3: using video brief (%d chars)", len(brief))
    else:
        log.info("s3: no video brief provided (none of --brief/--brief-file/<video>.brief.txt)")

    unit_mode = str((config.translate or {}).get("unit_mode", "sentence")).lower()
    tolerance = float((config.translate or {}).get("budget_overshoot", 1.15))

    if unit_mode == "sentence":
        units = units_for(entries, config.units or {})
        write_units_file(job, units)

        # Units to (re)translate: skip any unit containing a reviewed segment;
        # otherwise translate when any non-reviewed member still lacks vi.
        # With --force every non-reviewed unit is retranslated.
        def unit_is_todo(u) -> bool:
            members = u["segments"]
            if any(m.get("reviewed") for m in members):
                return False
            if force:
                return True
            return any(not m.get("vi") for m in members)

        todo_units = [u for u in units if unit_is_todo(u)]
        translated_units = 0
        unit_chunks = list(enumerate(chunks_of(todo_units, chunk_size), start=1))

        def unit_chunk_fn(chunk_with_index):
            chunk_index, chunk = chunk_with_index
            first = chunk[0]["segments"][0]["id"]
            context_entries = [e for e in entries if e["id"] < first and e.get("vi")]
            context = context_entries[-CONTEXT_SEGMENTS:] if context_entries else []
            context_payload = [
                {"id": e["id"], "text": e["text"], "vi": e.get("vi") or ""} for e in context
            ]
            payloads = [
                {"id": u["unit_id"], "text": u["text"], "max_chars": u["max_chars"]}
                for u in chunk
            ]
            return client.translate_batch(
                payloads, context_payload, glossary, brief=brief, pronouns=pronouns
            )

        def apply_units(idx, updates):
            nonlocal translated_units
            chunk_index, chunk = unit_chunks[idx]
            by_unit_id = {u["unit_id"]: u for u in chunk}
            for upd in updates:
                unit = by_unit_id.get(upd["id"])
                if unit is None:
                    log.warning("s3: translation for unknown unit %d ignored", upd["id"])
                    continue
                apply_unit_translation(entries, unit, upd["vi"])
                translated_units += 1
            log.info(
                "s3: units chunk %d done (%d/%d units)",
                chunk_index, translated_units, len(todo_units),
            )
            write_json(job.translated_path, entries)
            write_units_file(job, units)

        run_chunks_parallel(unit_chunks, unit_chunk_fn, concurrency, apply=apply_units, cancel=cancel)
        translated_count = translated_units
    else:
        translated_count = _run_segment_mode(
            job, entries, client, chunk_size, glossary, brief, force,
            pronouns=pronouns, concurrency=concurrency, cancel=cancel,
        )

    # One-shot enforcement pass (soft-budget tolerance, AGU3 task 7).
    over_before, over_after = enforce_budget(entries, client, tolerance)
    write_json(job.translated_path, entries)

    summary = (
        f"s3 summary: provider={provider.name} model={provider.model} "
        f"mode={unit_mode} units_translated={translated_count if unit_mode == 'sentence' else translated_count} "
        f"over_max_chars_before={over_before} over_max_chars_after={over_after}"
    )
    if client.total_input_tokens or client.total_output_tokens:
        summary += f" tokens(in={client.total_input_tokens}, out={client.total_output_tokens})"
    log.info(summary)
    print(summary)
