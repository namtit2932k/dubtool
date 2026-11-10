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
from dubtool.llm.base import (
    LLMError,
    OverloadFallbackFast,
    SplitRequiredError,
    failed_after_timeout,
)
from dubtool.llm.budget import BudgetExhausted
from dubtool.units import build_units, split_translation
from dubtool.llm.base import RateLimitError
from dubtool.stages._units_runner import run_units_parallel  # noqa: F401 (AGU11 2.1; re-exported)

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
    entries: list[dict[str, Any]], unit: dict[str, Any], vi_text: str,
    derived: bool = False,
) -> bool:
    """Divide one unit's Vietnamese across its segments (returns True when
    at least one entry changed). Reviewed segments keep their vi.

    `derived` (Update 12 task 3.1) marks the per-segment texts as derived
    from the unit's whole sentence: display-only; the dub unit record
    carries the sentence, and stages 4-7 in sentence mode read it, not
    these fragments.

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
        if derived:
            seg["derived"] = True
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
        pause_split_s=float(units_cfg.get("pause_split_s", 0.5)),
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
    entries: list[dict[str, Any]], client: LLMClient, tolerance: float,
    policy: str | None = None,
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
            shortened = client.shorten_batch(items, policy=policy)
            merge_translations(entries, shortened)
        except Exception as exc:  # enforcement is best-effort; stage 5 recovers
            log.warning("s3: enforcement pass failed, leaving as is: %s", exc)
    over_after = count_over_budget(entries, tolerance)
    return len(over_before), len(over_after)


def _filter_glossary(glossary: dict | None, texts: list[str]) -> dict | None:
    """AGU8 5.4: only the glossary entries that occur in the chunk are sent
    (saves tokens). "_"-prefixed comment keys never reach the prompt."""
    if not isinstance(glossary, dict):
        return glossary
    blob = " \n".join(str(t or "").lower() for t in texts)
    out: dict = {}
    for term, value in glossary.items():
        key = str(term or "").strip().lower()
        if not key or key.startswith("_"):
            continue
        if key in blob:
            out[term] = value
    return out


def _words_in_range(words: list[dict], start: float, end: float) -> list[dict]:
    """The word entries whose midpoint falls inside [start, end]."""
    out: list[dict] = []
    for w in words or []:
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws))
        except (TypeError, ValueError):
            continue
        mid = (ws + we) / 2.0
        if start - 0.05 <= mid < end + 0.05:
            out.append(w)
    return out


def _filler_state(job, config, transcript) -> dict:
    """AGU8 task 4: per-video suggestions, saved/doalog choices, the per-token
    actions and the base policy text for the run.

    Suggestions are always computed fresh runs (and stored in the job dir as
    suggested_fillers.json). The GUI dialog is asked only when
    `fillers.ask` is on, the run is app-interactive, no choice is already
    saved for this video, and a `config.fillers_dialog` callback exists
    (set by api.run_dub). CLI/headless runs never ask.
    """
    from dubtool import fillers as fillers_mod
    from dubtool.punctuate import read_words

    fill_cfg = getattr(config, "fillers", {}) or {}
    settings = fillers_mod.load_settings()
    words = read_words(job) or []
    scanned: list[dict] = []
    for seg in transcript:
        s = dict(seg)
        s["words"] = _words_in_range(words, float(s.get("start", 0.0)), float(s.get("end", 0.0)))
        scanned.append(s)
    suggestions = fillers_mod.suggest_candidates(scanned)
    fillers_mod.write_suggestions(job.dir, suggestions)
    log.info(
        "s3: filler suggestions: %d token(s) above the threshold — %s",
        len(suggestions), ", ".join(
            f"{s['token']} x{s['count']}" for s in suggestions[:5]) or "none",
    )

    user_choices: dict | None = None
    video = getattr(config, "fillers_video", None)
    if suggestions and video is not None:
        saved = fillers_mod.load_video_choices(video)
        if saved:
            user_choices = dict(saved)
            log.info("s3: using saved filler choices for this video (%s)", sorted(saved))
    if suggestions and user_choices is None:
        interactive = bool(getattr(config, "llm_interactive", False))
        if bool(fill_cfg.get("ask", True)) and interactive:
            dialog = getattr(config, "fillers_dialog", None)
            if callable(dialog):
                choices = dialog(suggestions)
                if choices:
                    user_choices = dict(choices)
                    if video is not None:
                        fillers_mod.save_video_choices(video, user_choices)
                    log.info("s3: filler dialog choices saved: %s", sorted(user_choices))

    actions = fillers_mod.resolve_actions(suggestions, settings, user_choices)
    policy = fillers_mod.render_policy(None, actions, settings) if actions else ""
    if policy:
        # Stage 5 shortening reads the same run-scoped policy.
        try:
            config.fillers_policy = policy
        except Exception:
            pass
    return {
        "settings": settings,
        "actions": actions,
        "policy": policy,
        "words": words,
        "suggestions": suggestions,
        "count": len(suggestions),
    }


def _completeness_pass(job, entries, config, client, cancel) -> list[dict]:
    """AGU8 6.2: flag probable truncations, re-translate each flagged unit
    ONCE in a single batch (+30% allowance), keep the old text when the
    re-translation is still flagged, and write quality_report.json (time,
    English, Vietnamese, reason). Returns the still-flagged units."""
    from dubtool import complete as complete_mod
    from dubtool.llm import parsing as llm_parsing

    tr = getattr(config, "translate", {}) or {}
    conn = tr.get("connectors") or None
    flags = complete_mod.flagged_entries(entries, connectors=conn)
    if not flags:
        return []
    log.info(
        "s3: completeness pass: %d unit(s) flagged (%s)", len(flags),
        {f["reason"] for f in flags},
    )
    by_id = {e["id"]: e for e in entries}
    items = [
        {"id": f["id"], "text": f["en"], "vi": f["vi"],
         "max_chars": max(1, int(f.get("max_chars") or 1))}
        for f in flags
    ]
    try:
        if cancel is not None and cancel.is_set():
            raise StageCancelled("s3: cancelled during the completeness pass")
        ids = [i["id"] for i in items]
        user = complete_mod.render_retranslate_batch(items)

        def _validate(raw):
            return llm_parsing.parse_json_array(raw, ids)

        def _note(exc):
            return ("Your previous reply was invalid: %s. Follow the format "
                    "exactly and reply again with only the JSON array." % exc)

        upd = client._call_and_validate(
            complete_mod.RETRANSLATE_SYSTEM, user, _validate, _note,
        )
    except Exception as exc:  # best effort: keep the old text, still report
        log.warning("s3: completeness re-translation failed (%s) — the flagged units are kept", exc)
        upd = []
    ok_ids: set[int] = set()
    for r in upd:
        entry = by_id.get(r.get("id"))
        new_vi = str(r.get("vi") or "").strip()
        if entry is None or not new_vi or entry.get("reviewed"):
            continue
        if complete_mod.flag_unit(entry["text"], new_vi, conn) is None:
            entry["vi"] = new_vi
            ok_ids.add(entry["id"])
    final = complete_mod.flagged_entries(entries, connectors=conn)
    flag_ids = {f["id"] for f in final}
    # AGU8 6.2: the entries the readable file marks. Stale flags from an
    # earlier run clear here (the write below persists the current truth).
    for e in entries:
        if e.get("uncertain") and e["id"] not in flag_ids:
            e.pop("uncertain", None)
    for f in final:
        by_id[f["id"]]["uncertain"] = True
    report = [
        {
            "time": by_id[f["id"]]["start"],
            "en": f["en"],
            "vi": f["vi"],
            "reason": f["reason"],
        }
        for f in final
    ]
    try:
        write_json(job.dir / "quality_report.json", report)
    except Exception:
        pass
    total = len(flags)
    fixed = total - len(final)
    log.info(
        "s3: completeness re-translation: %d/%d fixed, %d kept flagged",
        fixed, total, len(final),
    )
    return final


def fast_model_id(config) -> str | None:
    """The cheap mechanical-step model: the user's configured fallback
    model when one is set, else the shipped default
    (gemini-3.5-flash-lite) — mechanical steps (punctuation, fillers,
    completeness, shorten) always use it."""
    try:
        from dubtool.app import settings as settings_mod

        fallback = str((settings_mod.load() or {}).get("fallback_model") or "").strip()
        if fallback:
            return fallback
    except Exception as exc:
        log.debug("s3: fallback model lookup failed (%s)", exc)
    from dubtool.llm.catalog import DEFAULT_MODEL_ID

    return DEFAULT_MODEL_ID


def _selected_model(config) -> str | None:
    """The model id the run selected (api.run_dub stores it in
    config.llm_model); None keeps the provider's configured model."""
    return getattr(config, "llm_model", None) or None


def fast_client(config, pid: str, api_keys, cancel, on_overload=None):
    """A cheap-model LLMClient for the mechanical steps (punctuation,
    re-translation); its requests count in the shared run budget."""
    llm_cfg = getattr(config, "llm", {}) or {}
    options = {
        "server_error_backoff_s": llm_cfg.get("server_error_backoff_s"),
        "overload_after": llm_cfg.get("overload_after", 2),
        "overload_policy": "wait",  # mechanical steps never switch models
    }
    from dubtool.llm import catalog as catalog_mod

    mech_model = fast_model_id(config)
    mech_pid = catalog_mod.require(mech_model).provider
    provider = get_provider(
        config, mech_pid, api_keys=api_keys, cancel=cancel,
        model_override=mech_model,
    )
    return LLMClient(provider, cancel=cancel, options=options, on_overload=on_overload)


def _prepare_sentences(job, config, transcript, pid, api_keys, cancel):
    """AGU8 task 3 pre-translation step (fresh runs only).

    1. `punctuate.enabled: auto` triggers punctuation restoration when more
       than 30% of segments lack sentence-final punctuation or any run of
       more than 25 words does; `on` forces it, `off` skips.
    2. `asr.sentence_resegment` cuts the segments at sentence-final marks
       using words.json (only when the word times cover the text).
    Returns the (possibly updated) transcript or None when unchanged.
    """
    from dubtool.punctuate import (
        needs_punctuation,
        read_words,
        resegment_by_sentences,
        restore_punctuation,
    )

    changed = False
    punct = getattr(config, "punctuate", {}) or {}
    enabled = str(punct.get("enabled", "auto")).lower()
    if enabled != "off" and (
        needs_punctuation(
            transcript,
            max_missing_ratio=float(punct.get("max_missing_ratio", 0.30)),
            max_unpunct_run=int(punct.get("max_unpunct_run", 25)),
        ) or enabled == "on"
    ):
        client = fast_client(config, pid, api_keys, cancel)
        try:
            accepted, rejected = restore_punctuation(transcript, client, punct)
        except BudgetExhausted as exc:
            log.warning("punctuate: %s — the transcription text stays as is", exc)
            accepted = rejected = 0
        log.info(
            "s3: punctuation restore: %d window(s) accepted, %d rejected",
            accepted, rejected,
        )
        changed = accepted > 0
    if bool(getattr(config, "asr", {}).get("sentence_resegment", True)):
        words = read_words(job)
        if words:
            reseg = resegment_by_sentences(transcript, words)
            if reseg is not None:
                transcript = reseg
                changed = True
                log.info(
                    "s3: resegmented into %d sentence segments from word times",
                    len(reseg),
                )
        else:
            log.info("s3: no words.json — sentence resegmentation skipped")
    if not changed:
        return None
    write_json(job.transcript_path, transcript)
    return transcript


def translate_resilient(
    holder: dict, translate_call: Callable[..., list],
    payloads: list, context_payload: list, label: str, *,
    on_fallback: Callable[[str], None], split_on_timeout: bool,
    split_min_units: int, stats: dict, depth: int = 0,
    split_min_chars: int = 300, allow_split: bool = True,
) -> list:
    """The external resilience layer for one translation chunk.

    - OverloadFallbackFast (the overload policy said 'use the Fast model'):
      rebuild the client via on_fallback, then retry the same chunk once.
      The exception is otherwise left untouched by the wrapper.
    - SplitRequiredError (the client raised it for a 504/timeout/400-size
      failure of a chunk larger than the minimum sizes): split the chunk
      in two halves and translate each with the same policy — once. A
      half is never split again (allow_split=False), and a chunk below
      the split minimums is never split at all (the client decides that).
    """
    client = holder["client"]
    # Agreed chunk label echoed on the request logs. Concurrent threads
    # share the client, so a label can be overwritten by the next call;
    # it stays informational.
    client.request_label = label
    client.request_units = len(payloads)
    client.request_chars = sum(len(p.get("text") or "") for p in payloads)
    try:
        return translate_call(client, payloads, context_payload)
    except OverloadFallbackFast as exc:
        if depth >= 8:
            raise
        on_fallback(exc.from_model)  # swaps holder['client'] to the Fast model
        return translate_resilient(
            holder, translate_call, payloads, context_payload, label,
            on_fallback=on_fallback, split_on_timeout=split_on_timeout,
            split_min_units=split_min_units, stats=stats, depth=depth + 1,
            split_min_chars=split_min_chars, allow_split=allow_split,
        )
    except SplitRequiredError as exc:
        if depth >= 8 or not allow_split:
            # A half is never split again — the failure propagates and the
            # normal resume flow covers it.
            raise
        stats["splits"] = stats.get("splits", 0) + 1
        log.warning(
            "s3: %s failed (%s) — splitting %d payloads in two once "
            "(error kind splits once, halves are final)",
            label, exc.status or "timeout", len(payloads),
        )
        mid = _split_index(payloads)
        out: list = []
        for k, part in enumerate((payloads[:mid], payloads[mid:]), start=1):
            out.extend(translate_resilient(
                holder, translate_call, part, context_payload,
                f"{label} [part {k}/2]", on_fallback=on_fallback,
                split_on_timeout=split_on_timeout,
                split_min_units=split_min_units, stats=stats, depth=depth + 1,
                split_min_chars=split_min_chars, allow_split=False,
            ))
        return out
    except LLMError as exc:
        can_split = (
            allow_split and split_on_timeout and depth < 8
            and failed_after_timeout(exc)
            and len(payloads) >= max(2, 2 * split_min_units)
        )
        if not can_split:
            raise
        stats["splits"] = stats.get("splits", 0) + 1
        log.warning(
            "s3: %s failed after a timeout (%s) — splitting %d payloads in two",
            label, exc, len(payloads),
        )
        mid = _split_index(payloads)
        out: list = []
        for k, part in enumerate((payloads[:mid], payloads[mid:]), start=1):
            out.extend(translate_resilient(
                holder, translate_call, part, context_payload,
                f"{label} [part {k}/2]", on_fallback=on_fallback,
                split_on_timeout=split_on_timeout,
                split_min_units=split_min_units, stats=stats, depth=depth + 1,
                split_min_chars=split_min_chars, allow_split=False,
            ))
        return out


def _split_index(payloads: list) -> int:
    """The unit boundary closest to half the total source characters
    (AGENT_UPDATE_11 2.2 — halves by volume, not by count)."""
    total = sum(len(p.get("text") or "") for p in payloads)
    acc = 0
    for i, p in enumerate(payloads[:-1], start=1):
        acc += len(p.get("text") or "")
        if acc * 2 >= total:
            return i
    return max(1, len(payloads) // 2)


class _ChunkStore:
    """Update 12 task 2.3: per-chunk translation checkpoints in
    translate_chunks.json. get() matches a chunk's unit ids, source text
    and prompt shape; a matching record reuses its Vietnamese without any
    request. 'Start over' (--force) clears the store first."""

    def __init__(self, job, force: bool):
        from dubtool.llm.checkpoints import ChunkCheckpointStore

        self._jobs = 0
        self._hits = 0
        self.store = ChunkCheckpointStore(job.translate_chunks_path)
        if force:
            self.store.clear()

    @staticmethod
    def stable_no(unit_ids: list[int]) -> int:
        """A job-stable chunk number derived from the unit ids, so resume
        matches chunks across runs (the live cut order also varies with
        concurrency)."""
        import hashlib

        raw = json.dumps([int(i) for i in unit_ids])
        return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12], 16)


def _run_segment_mode(
    job, entries, translate_payloads: Callable[[list, list, str], list],
    chunk_size, glossary, brief, force: bool,
    pronouns: dict | None = None, concurrency: int = 1,
    cancel: threading.Event | None = None,
    chunk_store=None, prompt_hash: str = "", stats: dict | None = None,
    holder: dict | None = None,
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
    n_chunks = len(todo_chunks)

    def chunk_fn(chunk_with_index):
        chunk_index, chunk = chunk_with_index
        context_pool = [e for e in todo if e["id"] < chunk[0]["id"]]
        context = context_pool[-CONTEXT_SEGMENTS:] if context_pool else []
        context_payload = [
            {"id": e["id"], "text": e["text"], "vi": e.get("vi") or ""} for e in context
        ]
        payloads = [
            {"id": e["id"], "text": e["text"], "max_chars": e.get("max_chars") or 0}
            for e in chunk
        ]
        total_chars = sum(len(s.get("text") or "") for s in chunk)
        label = (
            f"translate chunk {chunk_index}/{n_chunks} "
            f"({len(chunk)} segments, {total_chars} chars)"
        )
        # Update 12: chunk checkpoints serve the segment mode as well.
        stable_no = _ChunkStore.stable_no([e["id"] for e in chunk])
        hit = chunk_store.store.get(stable_no, payloads, prompt_hash) if chunk_store else None
        if hit is not None:
            model_name = hit.get("model") or "?"
            if stats is not None:
                stats["models"][model_name] = (
                    stats["models"].get(model_name, 0) + len(payloads)
                )
                stats["checkpoint_hits"] = int(stats.get("checkpoint_hits", 0)) + 1
            log.info("s3: %s reused from the chunk checkpoints (no request)", label)
            return [
                dict(upd, model=model_name) for upd in (hit.get("vi") or [])
            ]
        updates = translate_payloads(chunk, context_payload, label)
        model_name = "?"
        if holder is not None:
            model_name = getattr(
                getattr(holder.get("client"), "provider", None), "model", "?"
            )
        if chunk_store is not None and model_name != "?":
            try:
                chunk_store.store.save(
                    stable_no, payloads, model=model_name, prompt_hash=prompt_hash,
                    vi=[{"id": u["id"], "vi": u["vi"]} for u in updates],
                )
            except Exception:
                pass
        if stats is not None:
            stats["models"][model_name] = stats["models"].get(model_name, 0) + len(payloads)
            for upd in updates:
                upd["model"] = model_name
        return updates

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
    api_keys: list[str] | None = None,
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

    pid = (provider_id or config.llm_provider).lower()

    if existing is None:
        # AGU8 4.2/4.3: fresh runs may restore punctuation and re-cut the
        # segments into sentences BEFORE translations are built. Resume runs
        # keep the saved ids intact (translated.json already exists) so the
        # step is skipped there.
        _prep = _prepare_sentences(job, config, transcript, pid, api_keys, cancel)
        if _prep is not None:
            transcript = _prep
        entries = build_translated(transcript, config.chars_per_sec)
    else:
        entries = existing
        have = {e["id"] for e in entries}
        for seg in transcript:
            if seg["id"] not in have:
                entries.extend(build_translated([seg], config.chars_per_sec))
        entries.sort(key=lambda e: e["id"])

    # AGU8 3.5: the per-mode request timeout (Fast mode uses the shorter
    # llm.request_timeout_s_fast). config.llm_mode is set by api.run_dub;
    # the CLI keeps the legacy single timeout.
    llm_cfg = getattr(config, "llm", {}) or {}
    mode = getattr(config, "llm_mode", None)
    interactive = bool(getattr(config, "llm_interactive", False))
    provider = get_provider(
        config, pid, api_keys=api_keys, cancel=cancel,
        model_override=_selected_model(config),
    )
    translate_cfg = config.translate or {}
    concurrency = max(1, int(translate_cfg.get("concurrency", 1)))
    pronouns = {k: v for k, v in (translate_cfg.get("pronouns") or {}).items() if v}
    # AGU8 6.3 fidelity preset: the meaning-vs-timing trade-off.
    priority = str(translate_cfg.get("priority", "balanced")).lower()
    priority_mult = {"faithful": 1.5, "timing": 0.9}.get(priority, 1.0)
    # AGU8 task 2: the retry knobs come from config llm; the stage owns the
    # overload policy callback and the split-on-timeout resilience layer.
    split_on_timeout = bool(llm_cfg.get("split_on_timeout", True))
    # Update 12 2.1: a split needs a chunk larger than these minimums (a
    # 504/timeout/400-size failure splits once; 503s never split).
    split_min_units = max(1, int(llm_cfg.get("split_min_units", 4) or 4))
    split_min_chars = max(100, int(llm_cfg.get("split_min_chars", 600) or 600))
    # Update 12 2.4: the default policy is fallback_fast for the GUI and
    # the CLI (the user's choice in Settings can return 'ask'/'wait').
    overload_policy = str(
        llm_cfg.get("overload_policy", "fallback_fast")
    ).lower()
    # The split rule, the breaker, the rate limiters and the shared
    # stage-wait budget ride in the client options, so the Fast-model
    # rebuild shares the same stage budget.
    stage_wait_state: dict = {"waited_s": 0.0, "consulted_at": 0.0, "consults": 0}
    client_options = {
        "backoff_s": llm_cfg.get("backoff_s"),
        "server_error_backoff_s": llm_cfg.get("server_error_backoff_s"),
        "overload_after": llm_cfg.get("overload_after", 2),
        "overload_policy": overload_policy,
        "split_on_timeout": split_on_timeout,
        "split_min_units": split_min_units,
        "split_min_chars": split_min_chars,
        "stage_budget_s": float(llm_cfg.get("stage_budget_s", 300.0) or 300.0),
        "stage_wait_state": stage_wait_state,
        "breaker_failures": llm_cfg.get("breaker_failures", 3),
        "breaker_cooldown_s": llm_cfg.get("breaker_cooldown_s", 60),
        "fallback_after_opens": llm_cfg.get("fallback_after_opens", 2),
        "max_rpm": llm_cfg.get("max_rpm"),
    }
    # Update 12 task 2.3: per-chunk translation checkpoints. A finished
    # chunk is never re-requested; 'Start over' (force) clears the store.
    checkpoint_store = _ChunkStore(job, force)
    stats: dict = {"splits": 0, "fallback": False, "fast_model": None, "models": {}}
    # AGU8 task 4: suggestions + per-video choices + the filler policy.
    fill_state = _filler_state(job, config, transcript)
    base_policy = fill_state["policy"]

    def _overload_callback(info: dict | None = None) -> str:
        # AGU8 3.4. interactive asks the dialog; 'ask' without a dialog and
        # 'wait' keep backing off; 'fallback_fast' makes the wrapper raise
        # OverloadFallbackFast, which translate_resilient turns into the
        # Fast-model switch below. AGU11 2.3: the callback receives the
        # client's info (model, consecutive failures, waited seconds).
        if overload_policy == "ask":
            dialog = getattr(config, "llm_overload_dialog", None)
            if interactive and callable(dialog):
                info_out = dict(info or {})
                info_out.setdefault("model", provider.model)
                action = str(dialog(info_out) or "").lower()  # may raise (dialog Cancel)
                if action in ("cancel", "cancelled"):
                    raise StageCancelled("s3: overload — cancelled by user")
                return "fallback_fast" if action in (
                    "fallback_fast", "fallback-fast", "fast", "switch",
                ) else "wait"
            # AGU8 3.4: 'ask' is a GUI-only policy — with no dialog (CLI,
            # --headless-run) it degrades to fallback_fast instead of waiting
            # out a real overload (which would only end in a give-up).
            return "fallback_fast"
        return "fallback_fast" if overload_policy in (
            "fallback_fast", "fallback-fast"
        ) else "wait"

    def _fallback_choice() -> tuple[str, str] | None:
        """(provider, model) of the configured fallback model — None when
        the user chose no fallback (the overload path then waits/gives up
        instead of switching to the same or an unusable model)."""
        fallback_model = str(getattr(config, "llm_fallback_model", "") or "").strip()
        if not fallback_model:
            return None
        if fallback_model == provider.model:
            return None  # no point switching to the already-failing model
        from dubtool.llm import catalog as catalog_mod

        return catalog_mod.require(fallback_model).provider, fallback_model

    def switch_to_fast_model(_from_model: str) -> None:
        choice = _fallback_choice()
        if choice is None:
            from dubtool.llm.base import LLMError

            raise LLMError(
                "overload policy wants to switch models but no fallback model "
                "is configured (Settings → General → Mô hình dịch)"
            )
        fallback_pid, fallback_model = choice
        fallback_provider = get_provider(
            config, fallback_pid, api_keys=api_keys, cancel=cancel,
            model_override=fallback_model,
        )
        holder["client"] = LLMClient(
            fallback_provider, cancel=cancel, options=client_options,
            on_overload=_overload_callback,
        )
        stats["fallback"] = True
        stats["fast_model"] = fallback_provider.model
        log.info(
            "s3: overload policy switched the rest of stage 3 to the fallback "
            "model %s", fallback_provider.model,
        )
        try:
            from dubtool.app import i18n

            heartbeat_emit = None
            from dubtool import heartbeat as hb_mod

            hb_mod.emit(i18n.tr("llm.fallback_to_fast", model=fast_provider.model))
            _ = heartbeat_emit
        except Exception:
            pass

    holder: dict = {"client": LLMClient(
        provider, cancel=cancel, options=client_options, on_overload=_overload_callback,
    )}

    def translate_payloads(payloads, context_payload, label: str,
                           policy: str | None = None,
                           glossary_override: dict | None = None) -> list:
        def do_translate(client, payloads_, ctx):
            # AGU8 6.3: the preset scales the per-unit character budget sent
            # to the model ('balanced' keeps the current numbers).
            scaled = [
                dict(p, max_chars=max(1, int(round((p.get("max_chars") or 0) * priority_mult))))
                if priority_mult != 1.0 else p
                for p in payloads_
            ]
            return client.translate_batch(
                scaled, ctx,
                glossary if glossary_override is None else glossary_override,
                brief=brief, pronouns=pronouns, policy=policy or base_policy,
            )
        return translate_resilient(
            holder, do_translate, payloads, context_payload, label,
            on_fallback=switch_to_fast_model,
            split_on_timeout=split_on_timeout,
            split_min_units=split_min_units, stats=stats,
            split_min_chars=split_min_chars,
        )

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
    # The prompt shape for the chunk checkpoints (glossary + pronouns; the
    # filler policy varies per chunk by design and is excluded).
    from dubtool.llm.checkpoints import chunk_prompt_hash

    prompt_hash = chunk_prompt_hash(glossary, pronouns)

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
        from dubtool.stages._units_runner import adaptive_state

        adaptive = adaptive_state(config)
        unit_by_id = {u["unit_id"]: u for u in todo_units}

        def unit_chunk_fn(chunk, label):
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
            # Update 12 task 2.3: a finished chunk of THIS job reuses its
            # Vietnamese — no request goes out for it.
            stable_no = _ChunkStore.stable_no([u["unit_id"] for u in chunk])
            hit = checkpoint_store.store.get(stable_no, payloads, prompt_hash)
            if hit is not None:
                model_name = hit.get("model") or "?"
                stats["models"][model_name] = (
                    stats["models"].get(model_name, 0) + len(payloads)
                )
                stats["checkpoint_hits"] = int(stats.get("checkpoint_hits", 0)) + 1
                log.info("s3: %s reused from the chunk checkpoints (no request)", label)
                return [
                    dict(upd, model=model_name) for upd in (hit.get("vi") or [])
                ]
            # AGU8 5.4: per-chunk detected spans + glossary filtering.
            chunk_spans: list[dict] = []
            from dubtool import fillers as fillers_mod
            chunk_texts = [u["text"] for u in chunk]
            for u in chunk:
                uw = _words_in_range(
                    fill_state["words"], float(u["start"]), float(u["end"])
                )
                chunk_spans.extend(fillers_mod.tag_fillers(
                    u["text"], uw, fill_state["settings"],
                    float((getattr(config, "fillers", {}) or {}).get("pause_s", 0.25)),
                ))
            chunk_policy = fillers_mod.render_policy(
                chunk_spans, fill_state["actions"], fill_state["settings"]
            ) or base_policy
            chunk_glossary = _filter_glossary(
                glossary, chunk_texts + [s["text"] for s in chunk_spans]
            )
            updates = translate_payloads(
                payloads, context_payload, label,
                policy=chunk_policy, glossary_override=chunk_glossary,
            )
            # The final summary states which units were translated by which
            # model (the Fast fallback serves later chunks); the chunk
            # checkpoint records the model too.
            model_name = getattr(getattr(holder["client"], "provider", None), "model", "?")
            stats["models"][model_name] = stats["models"].get(model_name, 0) + len(payloads)
            try:
                checkpoint_store.store.save(
                    stable_no, payloads, model=model_name, prompt_hash=prompt_hash,
                    vi=[{"id": upd["id"], "vi": upd["vi"]} for upd in updates],
                )
            except Exception:
                log.exception("s3: chunk checkpoint write failed (non-fatal)")
            for upd in updates:
                upd["model"] = model_name
            return updates

        unit_vi_by_id: dict[int, str] = {}   # Update 12: the dub units' sentences

        def apply_units(updates):
            nonlocal translated_units
            for upd in updates:
                unit = unit_by_id.get(upd["id"])
                if unit is None:
                    log.warning("s3: translation for unknown unit %d ignored", upd["id"])
                    continue
                apply_unit_translation(entries, unit, upd["vi"], derived=True)
                unit_vi_by_id[int(upd["id"])] = (upd.get("vi") or "").strip()
                if upd.get("model"):
                    unit["model"] = upd["model"]  # per-unit model record (2.4)
                translated_units += 1
            write_json(job.translated_path, entries)
            write_units_file(job, units)

        try:
            run_units_parallel(
                todo_units, unit_chunk_fn, apply=apply_units,
                concurrency=concurrency, cancel=cancel, adaptive=adaptive,
            )
        except BudgetExhausted as exc:  # 5.2: finish with what exists
            log.warning("s3: %s — the rerun fills the remaining units", exc)
        translated_count = translated_units
    else:
        try:
            translated_count = _run_segment_mode(
                job, entries, translate_payloads, chunk_size, glossary, brief, force,
                pronouns=pronouns, concurrency=concurrency, cancel=cancel,
                chunk_store=checkpoint_store, prompt_hash=prompt_hash,
                stats=stats, holder=holder,
            )
        except BudgetExhausted as exc:  # 5.2: finish with what exists
            log.warning("s3: %s — the rerun fills the remaining segments", exc)

    # Update 12 task 3: the dub units file — the primary data of stages
    # 4-7 in sentence mode. One record per sentence unit with the COMPLETE
    # Vietnamese sentence (never fragments).
    if unit_mode == "sentence" and units:
        from dubtool.dub_units import build_dub_units, write_dub_units

        translations: dict[int, dict] = {}
        for u in units:
            uid = int(u["unit_id"])
            vi = unit_vi_by_id.get(uid)
            if vi is None:
                # Resume of an older sentence-mode run: rebuild the sentence
                # from its derived segments with the boundary-dedupe join.
                from dubtool.stages.s8_export_readable import _join_deduped

                seg_vi = {e["id"]: (e.get("vi") or "") for e in entries}
                segs = [seg_vi.get(sid, "") for sid in u["seg_ids"]]
                vi = _join_deduped([t for t in segs if t.strip()])
            translations[uid] = {"vi": vi, "model": u.get("model")}
        write_dub_units(job, build_dub_units(units, translations))
        log.info("s3: dub_units.json written (%d sentence units)", len(units))

    # Update 12 task 2.4: a non-blocking fallback notice — in the details
    # panel and the run summary naming how many units the Fast model took.
    if stats.get("fallback") and translated_count:
        fast_count = int((stats.get("models") or {}).get(stats.get("fast_model") or "", 0))
        try:
            from dubtool.app import i18n
            from dubtool import heartbeat as hb_mod

            hb_mod.emit(
                i18n.tr(
                    "llm.fallback_notice",
                    model=stats.get("fast_model") or "?",
                    n=fast_count,
                    total=int(translated_count),
                )
            )
        except Exception:
            pass
        log.info(
            "s3: fallback notice: %d/%d translated by the Fast model %s "
            "(the units by the Accurate model are kept)",
            fast_count, translated_count, stats.get("fast_model"),
        )

    final_client = holder["client"]
    # One-shot enforcement pass (soft-budget tolerance, AGU3 task 7).
    # In sentence mode the voiced unit is the whole sentence (stage 5 fits
    # and shortens per unit), so the per-segment crude enforcement here
    # would only touch derived display text — it is skipped.
    if unit_mode == "sentence":
        over_before = over_after = 0
    else:
        over_before, over_after = enforce_budget(
            entries, final_client, tolerance, policy=base_policy
        )
    write_json(job.translated_path, entries)

    # AGU8 6.2: truncation/shrink guard — one re-translation batch, then the
    # quality report for what is still flagged.
    uncertain = _completeness_pass(job, entries, config, final_client, cancel)
    if uncertain:
        write_json(job.translated_path, entries)

    cli_provider = getattr(final_client, "provider", None)
    provider_name = getattr(cli_provider, "name", "?")
    provider_model = getattr(cli_provider, "model", "?")
    summary = (
        f"s3 summary: provider={provider_name} model={provider_model} "
        f"mode={unit_mode} units_translated={translated_count} "
        f"over_max_chars_before={over_before} over_max_chars_after={over_after}"
    )
    if final_client.total_input_tokens or final_client.total_output_tokens:
        summary += (
            f" tokens(in={final_client.total_input_tokens}, "
            f"out={final_client.total_output_tokens})"
        )
    summary += f" llm_requests={final_client.total_calls}"  # AGU6 5.2 per-stage count
    if stats["splits"]:
        summary += f" timeout_splits={stats['splits']}"
    if stats["fallback"]:
        summary += f" overload_fallback=fast({stats['fast_model']})"
    # AGU11 2.3: units per model in the summary.
    model_counts = stats.get("models") or {}
    if len(model_counts) > 1:
        summary += " units_by_model=" + ",".join(
            f"{model}:{count}" for model, count in sorted(model_counts.items())
        )
    if fill_state["count"]:
        summary += f" filler_suggestions={fill_state['count']}"
    if uncertain:
        summary += f" uncertain_units={len(uncertain)}"
    log.info(summary)
    if uncertain:
        # AGU8 6.2: the plain-language line for the run summary.
        try:
            from dubtool.app import i18n
            from dubtool.audio_utils import format_mmss

            log.info(
                "%s",
                i18n.tr(
                    "s3.uncertain_plain",
                    n=len(uncertain),
                    times=", ".join(format_mmss(u["time"]) for u in uncertain[:10]),
                ),
            )
        except Exception:
            pass
    print(summary)
