"""AGENT_UPDATE_8 task 8: translation-quality evaluation harness.

`dubtool eval-translation <job_id> [--variants current,punctuated,faithful]`
writes `eval/<timestamp>.md` with, per unit: the time, the English, the
Vietnamese for each variant, and the flags (unpunctuated source, run-on
length, dangling end / incomplete / shrunk, shrink ratio, filler spans with
their actions, glossary hits, idioms matched).

It reuses the job's own `units.json` (the same units stage 3 translated) and
counts its LLM requests in the run budget like every other stage. It never
scores or ranks anything: the file is for the user to read and compare.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from dubtool.complete import flag_unit
from dubtool.job import read_json
from dubtool.punctuate import ends_sentence
from dubtool.units import build_units

log = logging.getLogger("dubtool")

EVAL_DIRNAME = "eval"
_RUN_ON_WORDS = 25


def _load_unit_times(job_dir: Path) -> dict[int, dict]:
    """{segment id: {"start", "end"}} from units.json when it exists."""
    path = job_dir / "units.json"
    if not path.exists():
        return {}
    times: dict[int, dict] = {}
    try:
        units = read_json(path)
    except Exception:
        return {}
    for u in units or []:
        try:
            if isinstance(u, dict) and "start" in u and "end" in u:
                times[int(u["unit_id"])] = {
                    "start": float(u["start"]),
                    "end": float(u["end"]),
                }
        except (KeyError, TypeError, ValueError):
            continue
    return times


def glossary_hits(en: str, glossary: dict | None) -> list[str]:
    """Glossary keys that occur in the English (case-insensitive literal)."""
    hits: list[str] = []
    if not isinstance(glossary, dict):
        return hits
    lowered = (en or "").lower()
    for key in glossary:
        if str(key).startswith("_"):
            continue
        if str(key).lower() in lowered:
            hits.append(str(key))
    return hits


def idioms_matched(en: str, glossary: dict | None) -> list[str]:
    """Members of a glossary `idioms` subsection that occur in the text."""
    idioms = (glossary or {}).get("idioms")
    if not isinstance(idioms, dict):
        return []
    lowered = (en or "").lower()
    return [k for k in idioms if str(k).lower() in lowered]


def filler_spans(en: str, settings: dict | None = None) -> list[dict]:
    """Detected filler/vocative spans for this unit's text."""
    try:
        from dubtool.fillers import tag_fillers

        return tag_fillers(en, None, settings)
    except Exception:
        return []


def unit_flags(en: str, vi: str, glossary: dict | None = None,
               fillers_settings: dict | None = None) -> dict:
    """All structural flags for one unit (AGU8 task 8) — never a score."""
    words = re.findall(r"[\w'’]+", en or "")
    flags: list[str] = []
    if (en or "").strip() and not ends_sentence(en):
        flags.append("unpunctuated_source")
        if len(words) > _RUN_ON_WORDS:
            flags.append("run_on")
    reason = complete_reason(en, vi)
    if reason:
        flags.append(reason)
    shrink = None
    if (vi or "").strip() and (en or "").strip():
        shrink = round(len(vi) / max(len(en), 1), 2)
    return {
        "flags": flags,
        "shrink_ratio": shrink,
        "fillers": filler_spans(en, fillers_settings),
        "glossary_hits": glossary_hits(en, glossary),
        "idioms_matched": idioms_matched(en, glossary),
    }


def complete_reason(en: str, vi: str) -> str | None:
    """The completeness flag (incomplete | dangling | shrunk) or None."""
    try:
        return flag_unit(en, vi)
    except Exception:
        return None


def collect_units(job, config) -> list[dict]:
    """The job's own translated rows joined with unit times. Each item:
    {unit_id, start, end, en, vi_current, max_chars}."""
    job_dir = Path(job.dir)
    translated = read_json(job.translated_path)
    if not isinstance(translated, list) or not translated:
        raise RuntimeError(f"no translated.json rows in {job_dir}")
    entries = [{str(e.get("id")): e} for e in translated if isinstance(e, dict)]
    by_id: dict[str, dict] = {}
    for e in entries:
        for k, v in e.items():
            by_id[k] = v
    times = _load_unit_times(job_dir)
    chars_per_sec = float(getattr(config, "chars_per_sec", 12.5) or 12.5)

    # One row per translated entry (units are the translation units; times
    # come from units.json when the job has it, otherwise rebuilt).
    rows: list[dict] = []
    for e in translated:
        if not isinstance(e, dict) or not e.get("text"):
            continue
        key = str(e.get("id"))
        t = times.get(_int_or_none(e.get("id")) , {})
        rows.append({
            "unit_id": key,
            "start": t.get("start"),
            "end": t.get("end"),
            "en": str(e.get("text") or ""),
            "vi_current": str(e.get("vi") or ""),
            "max_chars": e.get("max_chars"),
        })
    if not any(r["start"] is not None for r in rows) and (job_dir / "transcript.json").exists():
        # Older jobs without units.json: rebuild units to get times.
        transcript = read_json(job_dir / "transcript.json")
        seg_by_id = {str(s.get("id")): s for s in transcript if isinstance(s, dict)}
        units_cfg = getattr(config, "units", {}) or {}
        units = build_units(
            transcript,
            max_segments=int(units_cfg.get("max_segments", 4)),
            max_duration_s=float(units_cfg.get("max_duration_s", 14.0)),
            max_gap_s=float(units_cfg.get("max_gap_s", 1.0)),
        )
        for u in units:
            parts = [str(seg_by_id[i].get("text") or "") for i in u.get("seg_ids", []) if i in seg_by_id]
            rows_timespan = {"start": u.get("start"), "end": u.get("end"), "en": " ".join(parts)}
            joined = " ".join(parts)
            vi_join = " ".join(
                str(seg_by_id[i].get("vi") or "") for i in u.get("seg_ids", []) if i in seg_by_id
            )
            existing = [r for r in rows if r["unit_id"] in {str(i) for i in u.get("seg_ids", [])}]
            if not existing:
                continue  # never invent rows the job did not translate
            for r in existing:
                r["en"] = joined
                r["vi_current"] = vi_join
                r["start"] = rows_timespan["start"]
                r["end"] = rows_timespan["end"]
    return rows


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _variant_choices(spec: str | None) -> list[str]:
    allowed = ("current", "punctuated", "faithful")
    if not spec:
        return ["current"]
    out = [v.strip() for v in spec.split(",") if v.strip() in allowed]
    return out or ["current"]


def run_eval(job, config, variants: str | None = None, brief: str = "",
             out_dir: Path | None = None, budget=None) -> Path:
    """Write eval/<timestamp>.md, outside Git, small. Returns its path.

    `budget`, when given, is the run's RequestBudget the re-translation
    counts against (AGU8 task 8: counted, never exempt).
    """
    from dubtool.llm import LLMClient, get_provider

    variant_list = _variant_choices(variants)
    rows = collect_units(job, config)
    glossary = getattr(config, "glossary", {}) or {}
    settings = None
    try:
        from dubtool.fillers import load_settings

        settings = load_settings()
    except Exception:
        settings = None

    client_info = {"kind": "none"}
    extra_variants: dict[str, dict[str, str]] = {}
    if budget is None:
        try:
            from dubtool.llm import budget as llm_budget

            budget = llm_budget.from_config(config)
        except Exception:
            budget = None

    need_model = "punctuated" in variant_list or "faithful" in variant_list
    if need_model:
        provider = get_provider(config, getattr(config, "llm_provider", "gemini"))
        client = LLMClient(provider)
        client_info = {"kind": str(type(provider).__name__)}
        brief = brief or ""
        try:
            from dubtool.brief import resolve_brief

            brief = brief or resolve_brief(job_dir=Path(job.dir))
        except Exception:
            brief = brief or ""
        if "punctuated" in variant_list:
            extra_variants["punctuated"] = _retranslate(
                client, rows, config, brief, multiplier=1.0, budget=budget
            )
        if "faithful" in variant_list:
            extra_variants["faithful"] = _retranslate(
                client, rows, config, brief, multiplier=1.5, budget=budget
            )

    out_path = Path(out_dir) if out_dir else Path.cwd() / EVAL_DIRNAME
    out_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md = _render(rows, variant_list, extra_variants, glossary, settings, budget, job)
    target = out_path / f"{stamp}.md"
    target.write_text(md, encoding="utf-8")
    log.info("eval-translation: wrote %s (%d units, variants=%s)", target, len(rows), ",".join(variant_list))
    return target


def _retranslate(client, rows: list[dict], config, brief: str, multiplier: float,
                 budget) -> dict[str, str]:
    """Re-translate every unit once with the given character-budget
    multiplier. Budget- or network-safe: failures leave the variant out."""
    payloads = []
    context = []
    for r in rows:
        max_chars = r.get("max_chars")
        if max_chars:
            max_chars = int(float(max_chars) * multiplier)
        payloads.append({"id": r["unit_id"], "text": r["en"], "max_chars": max_chars})
        context.append({"id": r["unit_id"], "text": r["en"], "vi": r["vi_current"]})
    try:
        updates = client.translate_batch(payloads, context, getattr(config, "glossary", {}) or {}, brief=brief)
        return {str(u.get("id")): str(u.get("vi") or "") for u in updates}
    except Exception as exc:
        log.warning("eval: %s variant failed, left out: %s", multiplier, exc)
        raise


def _render(rows, variant_list, extra_variants, glossary, settings, budget, job) -> str:
    lines: list[str] = [
        f"# Translation evaluation — {job.dir.name}",
        "",
        f"variants: {', '.join(variant_list)}",
        f"llm_requests_used: {getattr(budget, 'used', 0)} / {getattr(budget, 'limit', '?')}",
        "This file never scores or ranks.",  # AGU8 9: the user reads it
        "",
    ]
    for r in rows:
        info = unit_flags(r["en"], r["vi_current"], glossary, settings)
        span = ""
        if r["start"] is not None and r["end"] is not None:
            span = f" | {r['start']:.1f}–{r['end']:.1f}s"
        lines.append(f"## unit {r['unit_id']}{span}")
        flag_bits = list(info["flags"])
        if flag_bits:
            lines.append(f"flags: {', '.join(flag_bits)}")
        if info["shrink_ratio"] is not None:
            lines.append(f"shrink_ratio: {info['shrink_ratio']}")
        if info["glossary_hits"]:
            lines.append(f"glossary_hits: {', '.join(info['glossary_hits'])}")
        if info["idioms_matched"]:
            lines.append(f"idioms_matched: {', '.join(info['idioms_matched'])}")
        for span_ in info["fillers"]:
            lines.append(
                f"filler: {span_.get('text')} ({span_.get('kind')}, {span_.get('action')})"
            )
        lines.append(f"- en: {r['en']}")
        lines.append(f"- current: {r['vi_current']}")
        for variant in ("punctuated", "faithful"):
            if variant in variant_list:
                vi = (extra_variants.get(variant) or {}).get(str(r["unit_id"]), "")
                lines.append(f"- {variant}: {vi}")
        lines.append("")
    return "\n".join(lines)
