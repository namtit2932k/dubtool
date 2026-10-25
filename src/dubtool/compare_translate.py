"""Compare translation models on the same units (AGENT_UPDATE_3.md task 8).

`dubtool compare-translate <job_id> --models gemini:gemini-3.5-flash-lite,...`
runs the SAME prompt, context and units through every listed model once and
writes jobs/<job_id>/compare/translation_compare_<timestamp>.md with one
column per model. The agent collects; the user judges — no scoring, no
recommendation, and the default model in config.yaml is never changed.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from dubtool.config import Config
from dubtool.job import read_json
from dubtool.llm import LLMClient, get_provider
from dubtool.units import build_units

log = logging.getLogger("dubtool")


def parse_models(spec: str) -> list[tuple[str, str]]:
    """'gemini:gemini-3.5-flash-lite,anthropic:claude-...' -> [(pid, model)]."""
    out: list[tuple[str, str]] = []
    seen = set()
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        provider, sep, model = part.partition(":")
        key = (provider.lower(), model)
        if sep and model and key not in seen:
            seen.add(key)
            out.append(key)
    return out


class _StubConfig:
    """Minimal config shim so get_provider builds the requested model."""

    def __init__(self, pid: str, model: str, llm_cfg: dict):
        self.llm_provider = pid
        self.llm = {pid: dict(llm_cfg, model=model)}

    @property
    def glossary(self):  # not used by providers
        return {}


def build_model(config: Config, pid: str, model: str):
    """Construct one provider for a specific model (test seam)."""
    return get_provider(_StubConfig(pid, model, config.provider_config(pid)), pid)


def run_compare(job, config: Config, models: list[tuple[str, str]], start: int, count: int, brief: str = "") -> Path:
    from dubtool.brief import resolve_brief

    if not brief:
        brief = resolve_brief(job_dir=job.dir)
    transcript = read_json(job.transcript_path)
    chars_per_sec = config.chars_per_sec
    seg_by_id = {s["id"]: s for s in transcript}

    units_cfg = config.units or {}
    units = build_units(
        transcript,
        max_segments=int(units_cfg.get("max_segments", 4)),
        max_duration_s=float(units_cfg.get("max_duration_s", 14.0)),
        max_gap_s=float(units_cfg.get("max_gap_s", 1.0)),
    )
    selected = units[int(start) : int(start) + int(count)]
    if not selected:
        raise RuntimeError(f"no units in range [{start}, {start}+{count}) (job has {len(units)} units)")
    for u in selected:
        u["max_chars"] = sum(
            int((seg_by_id[i]["end"] - seg_by_id[i]["start"]) * chars_per_sec)
            for i in u["seg_ids"]
        )

    # English-only context for every model: the previous units' text, so the
    # comparison prompt is identical across models.
    context = [
        {"id": u["unit_id"], "text": u["text"], "vi": ""}
        for u in units[max(int(start) - 3, 0) : int(start)]
    ]

    context_count = len(context)
    results: dict[str, dict] = {}  # key "pid:model" -> info dict
    errors: list[str] = []
    for pid, model in models:
        key = f"{pid}:{model}"
        try:
            provider = build_model(config, pid, model)
        except Exception as exc:
            errors.append(key)
            log.warning("compare: cannot build %s — skipped: %s", key, exc)
            results[key] = {"error": f"provider not available: {exc}"}
            continue
        client = LLMClient(provider)
        payloads = [{"id": u["unit_id"], "text": u["text"], "max_chars": u["max_chars"]} for u in selected]
        started = time.monotonic()
        try:
            updates = client.translate_batch(payloads, context, config.glossary, brief=brief)
            elapsed = time.monotonic() - started
            by_unit = {u["id"]: u.get("vi", "") for u in updates}
            results[key] = {
                "translations": by_unit,
                "input_tokens": client.total_input_tokens,
                "output_tokens": client.total_output_tokens,
                "calls": client.total_calls,
                "elapsed_s": round(elapsed, 2),
            }
        except Exception as exc:
            elapsed = time.monotonic() - started
            errors.append(key)
            log.warning("compare: %s failed after %.1fs — marked in report: %s", key, elapsed, exc)
            results[key] = {"error": str(exc)}

    out_dir = Path(job.dir) / "compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"translation_compare_{stamp}.md"
    out_path.write_text(
        render_markdown(models, units, selected, results, brief, errors, context_count),
        encoding="utf-8",
    )
    return out_path


def render_markdown(
    models, all_units, selected, results, brief: str, errors: list[str], context_count: int = 0
) -> str:
    lines: list[str] = []
    lines.append("# Translation model comparison")
    lines.append("")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"Generated: {stamp} — same prompt, same context, same units for every model.")
    lines.append("")
    if brief:
        lines.append(f"Video brief: {len(brief)} characters (included in the prompt).")
    else:
        lines.append("Video brief: not provided (none found).")
    lines.append(
        f"Context given to every model: English text of the {context_count} units "
        "immediately before the range, no Vietnamese."
    )
    total_units = len(all_units)
    lines.append(f"Units: {len(selected)} of {total_units} (unit ids "
                 f"{selected[0]['unit_id']}–{selected[-1]['unit_id']}).")
    lines.append("")
    header = "| English (unit id) |"
    sep = "|---|"
    for pid, model in models:
        key = f"{pid}:{model}"
        header += f" {key} |"
        sep += "---|"
    lines.append(header)
    lines.append(sep)
    for u in selected:
        eng = u["text"]
        if len(eng) > 160:
            eng = eng[:157] + "…"
        row = f"| u{u['unit_id']} (max_chars {u['max_chars']}): {eng} |"
        for pid, model in models:
            key = f"{pid}:{model}"
            info = results.get(key, {})
            if info.get("error"):
                row += f" *(error: {info['error']})* |"
                continue
            vi = info["translations"].get(u["unit_id"], "")
            cell = vi.replace("|", "\\|").replace("\n", " ") or "*(empty)*"
            row += f" {cell} ({len(vi)}/{u['max_chars']}) |"
        lines.append(row)
    lines.append("")
    lines.append("## Model stats")
    lines.append("")
    lines.append("| Model | Units translated | Calls | Tokens (in/out) | Time |")
    lines.append("|---|---|---|---|---|")
    for pid, model in models:
        key = f"{pid}:{model}"
        info = results.get(key, {})
        if info.get("error"):
            lines.append(f"| {key} | — | — | — | *(failed: {info['error']})* |")
        else:
            lines.append(
                f"| {key} | {len(info['translations'])} | {info['calls']} "
                f"| {info['input_tokens']}/{info['output_tokens']} | {info['elapsed_s']} s |"
            )
    if errors:
        lines.append("")
        lines.append(f"Skipped/failed models: {', '.join(errors)}")
    lines.append("")
    lines.append("Character counts are informational (the budget is a soft target); "
                 "no scoring or recommendation is made here — the user judges the translations.")
    return "\n".join(lines) + "\n"
