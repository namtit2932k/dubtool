"""Generate unedited evidence tables from DubTool job folders (AGENT_UPDATE_15 Task 5).

Usage:
    py scripts/evidence_table.py <job_folder> [--second-job <job_folder>] [--label1 L1] [--label2 L2]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SENTENCE_END_RE = re.compile(r'[.!?…]["”\']?\s*$')


def _ends_sentence(text: str) -> bool:
    t = (text or "").strip()
    return bool(t and SENTENCE_END_RE.search(t))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _parse_srt_cues(srt_path: Path) -> list[str]:
    if not srt_path.is_file():
        return []
    raw = srt_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return []
    blocks = re.split(r"\r?\n\r?\n+", raw)
    cues: list[str] = []
    for b in blocks:
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            cues.append("\n".join(lines[2:]))
        elif len(lines) >= 2 and "-->" in lines[0]:
            cues.append("\n".join(lines[1:]))
    return cues


def analyze_job(job_dir: Path) -> dict:
    job_dir = job_dir.resolve()
    state = json.loads((job_dir / "state.json").read_text(encoding="utf-8")) if (job_dir / "state.json").is_file() else {}
    transcript = json.loads((job_dir / "transcript.json").read_text(encoding="utf-8")) if (job_dir / "transcript.json").is_file() else []
    units = json.loads((job_dir / "units.json").read_text(encoding="utf-8")) if (job_dir / "units.json").is_file() else []
    translated = json.loads((job_dir / "translated.json").read_text(encoding="utf-8")) if (job_dir / "translated.json").is_file() else []
    dub_units_path = job_dir / "dub_units.json"
    dub_units = json.loads(dub_units_path.read_text(encoding="utf-8")) if dub_units_path.is_file() else None
    schedule = json.loads((job_dir / "schedule.json").read_text(encoding="utf-8")) if (job_dir / "schedule.json").is_file() else []

    mode = "sentence" if dub_units else "segment"
    active_units = dub_units if dub_units else translated
    num_units = len(active_units)

    # Fit status counts must sum to num_units
    fit_counts: dict[str, int] = {}
    for entry in active_units:
        st = str(entry.get("fit_status") or "pending")
        fit_counts[st] = fit_counts.get(st, 0) + 1
    assert sum(fit_counts.values()) == num_units, (
        f"fit_status counts {fit_counts} sum to {sum(fit_counts.values())} != unit count {num_units}"
    )

    # Input metadata
    orig = state.get("original") or {}
    input_path_str = orig.get("path") or str(job_dir / str(state.get("source") or "input.mp4"))
    input_path = Path(input_path_str)
    if input_path.is_file():
        input_sha256 = _sha256_file(input_path)
    else:
        input_sha256 = str(orig.get("fingerprint") or "unknown")

    duration_s = float(transcript[-1]["end"]) if transcript else 0.0
    models_used = sorted({str(u.get("model")) for u in active_units if u.get("model")})
    model_str = ", ".join(models_used) if models_used else str(state.get("model") or "gemini-3.5-flash-lite")

    num_segments = len(transcript)
    segs_not_ending_sentence = sum(1 for s in transcript if not _ends_sentence(str(s.get("text") or "")))
    tts_requests = len(active_units)
    tts_not_ending_sentence = sum(1 for u in active_units if not _ends_sentence(str(u.get("vi") or "")))

    shifted_clips = sum(1 for s in schedule if float(s.get("shift") or 0.0) > 0.001)
    overlapping_clips = sum(1 for s in schedule if bool(s.get("overlap")))
    max_shift = max((float(s.get("shift") or 0.0) for s in schedule), default=0.0)
    if schedule and active_units:
        end_lag = max(0.0, float(schedule[-1].get("play_end") or 0.0) - float(active_units[-1].get("end") or 0.0))
    else:
        end_lag = 0.0

    cues = _parse_srt_cues(job_dir / "output.vi.srt")
    cue_count = len(cues)
    longest_cue = max((len(c) for c in cues), default=0)

    # Extract first 12 units for raw JSON output
    first_12: list[dict] = []
    for u in active_units[:12]:
        uid = u.get("unit_id", u.get("id"))
        first_12.append(
            {
                "unit_id": uid,
                "seg_ids": u.get("seg_ids", [uid]),
                "start": u.get("start"),
                "end": u.get("end"),
                "text": u.get("text"),
                "vi": u.get("vi"),
                "model": u.get("model") or model_str,
                "voice": u.get("tts_voice") or u.get("voice") or state.get("voice"),
                "fit_status": u.get("fit_status"),
                "tts_duration": u.get("tts_duration"),
            }
        )

    # Corresponding old fragments from translated.json (covered by first 12 sentence units or first 12 segments)
    covered_seg_ids: set[int] = set()
    if dub_units:
        for u in dub_units[:12]:
            for sid in u.get("seg_ids") or []:
                covered_seg_ids.add(int(sid))
    else:
        for s in translated[:12]:
            covered_seg_ids.add(int(s["id"]))

    fragments = [
        {
            "id": s.get("id"),
            "start": s.get("start"),
            "end": s.get("end"),
            "text": s.get("text"),
            "vi": s.get("vi"),
            "fit_status": s.get("fit_status"),
            "tts_duration": s.get("tts_duration"),
        }
        for s in translated
        if int(s.get("id", -1)) in covered_seg_ids
    ]

    return {
        "job_dir": str(job_dir),
        "job_id": state.get("job_id") or job_dir.name,
        "mode": mode,
        "input_path": str(input_path),
        "duration_s": round(duration_s, 2),
        "input_sha256": input_sha256,
        "model": model_str,
        "num_segments": num_segments,
        "segs_not_ending_sentence": segs_not_ending_sentence,
        "num_sentence_units_built": len(units),
        "num_units": num_units,
        "tts_requests": tts_requests,
        "tts_not_ending_sentence": tts_not_ending_sentence,
        "fit_counts": fit_counts,
        "shifted_clips": shifted_clips,
        "overlapping_clips": overlapping_clips,
        "max_shift_s": round(max_shift, 3),
        "end_lag_s": round(end_lag, 3),
        "cue_count": cue_count,
        "longest_cue_chars": longest_cue,
        "stage_s": state.get("stage_s") or {},
        "first_12_units": first_12,
        "corresponding_fragments": fragments,
    }


def _fmt_fit(fc: dict[str, int]) -> str:
    keys = ["ok", "stretched", "shortened", "overflow", "pending"]
    parts = [f"{k}={fc.get(k, 0)}" for k in keys if fc.get(k, 0) > 0 or k in ("ok", "stretched", "shortened", "overflow")]
    extra = [f"{k}={v}" for k, v in sorted(fc.items()) if k not in keys]
    return ", ".join(parts + extra) + f" (sum={sum(fc.values())})"


def format_report(info1: dict, info2: dict | None = None, label1: str | None = None, label2: str | None = None) -> str:
    l1 = label1 or f"{info1['mode']} ({info1['job_id']})"
    lines: list[str] = []
    lines.append("### Input & Run Metadata")
    lines.append(f"- **Input file path:** `{info1['input_path']}`")
    lines.append(f"- **Duration:** `{info1['duration_s']} s`")
    lines.append(f"- **SHA-256:** `{info1['input_sha256']}`")
    lines.append(f"- **Model used:** `{info1['model']}`")
    lines.append("")

    if info2 is None:
        lines.append(f"### Metrics Table — {l1}")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Job ID / Mode | `{info1['job_id']}` (`{info1['mode']}`) |")
        lines.append(f"| Segments (`transcript.json`) | {info1['num_segments']} |")
        lines.append(f"| Segments not ending a sentence | {info1['segs_not_ending_sentence']} |")
        lines.append(f"| Dub units | {info1['num_units']} |")
        lines.append(f"| TTS requests (synthesized clips) | {info1['tts_requests']} |")
        lines.append(f"| TTS texts not ending a sentence | {info1['tts_not_ending_sentence']} |")
        lines.append(f"| `fit_status` counts | {_fmt_fit(info1['fit_counts'])} |")
        lines.append(f"| Shifted clips (`shift > 0`) | {info1['shifted_clips']} |")
        lines.append(f"| Overlapping clips (`overlap == true`) | {info1['overlapping_clips']} |")
        lines.append(f"| Maximum shift (s) | {info1['max_shift_s']:.3f} s |")
        lines.append(f"| Lag at end (s) | {info1['end_lag_s']:.3f} s |")
        lines.append(f"| Subtitle cue count (`.srt`) | {info1['cue_count']} |")
        lines.append(f"| Longest subtitle cue (chars) | {info1['longest_cue_chars']} |")
        lines.append("")
        lines.append(f"### First 12 Units (Raw JSON) — {l1}")
        lines.append("```json")
        lines.append(json.dumps(info1["first_12_units"], ensure_ascii=False, indent=2))
        lines.append("```")
        if info1["mode"] == "segment":
            lines.append(f"### Corresponding Segment Fragments (`translated.json`) — {l1}")
            lines.append("```json")
            lines.append(json.dumps(info1["corresponding_fragments"], ensure_ascii=False, indent=2))
            lines.append("```")
    else:
        l2 = label2 or f"{info2['mode']} ({info2['job_id']})"
        lines.append(f"### Comparison Table — {l1} vs {l2}")
        lines.append(f"| Metric | {l1} | {l2} |")
        lines.append("| --- | --- | --- |")
        lines.append(f"| Job ID / Mode | `{info1['job_id']}` (`{info1['mode']}`) | `{info2['job_id']}` (`{info2['mode']}`) |")
        lines.append(f"| Segments (`transcript.json`) | {info1['num_segments']} | {info2['num_segments']} |")
        lines.append(f"| Segments not ending a sentence | {info1['segs_not_ending_sentence']} | {info2['segs_not_ending_sentence']} |")
        lines.append(f"| Dub units | {info1['num_units']} | {info2['num_units']} |")
        lines.append(f"| TTS requests (synthesized clips) | {info1['tts_requests']} | {info2['tts_requests']} |")
        lines.append(f"| TTS texts not ending a sentence | {info1['tts_not_ending_sentence']} | {info2['tts_not_ending_sentence']} |")
        lines.append(f"| `fit_status` counts | {_fmt_fit(info1['fit_counts'])} | {_fmt_fit(info2['fit_counts'])} |")
        lines.append(f"| Shifted clips (`shift > 0`) | {info1['shifted_clips']} | {info2['shifted_clips']} |")
        lines.append(f"| Overlapping clips (`overlap == true`) | {info1['overlapping_clips']} | {info2['overlapping_clips']} |")
        lines.append(f"| Maximum shift (s) | {info1['max_shift_s']:.3f} s | {info2['max_shift_s']:.3f} s |")
        lines.append(f"| Lag at end (s) | {info1['end_lag_s']:.3f} s | {info2['end_lag_s']:.3f} s |")
        lines.append(f"| Subtitle cue count (`.srt`) | {info1['cue_count']} | {info2['cue_count']} |")
        lines.append(f"| Longest subtitle cue (chars) | {info1['longest_cue_chars']} | {info2['longest_cue_chars']} |")
        lines.append("")
        lines.append(f"### First 12 Units (Raw JSON) — {l1}")
        lines.append("```json")
        lines.append(json.dumps(info1["first_12_units"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
        lines.append(f"### First 12 Units / Corresponding Fragments (Raw JSON) — {l2}")
        lines.append("```json")
        lines.append(json.dumps(info2["first_12_units"], ensure_ascii=False, indent=2))
        lines.append("```")
        if info2["mode"] == "segment":
            lines.append("")
            lines.append(f"### Corresponding Old Segment Fragments (`translated.json`) — {l2}")
            lines.append("```json")
            lines.append(json.dumps(info2["corresponding_fragments"], ensure_ascii=False, indent=2))
            lines.append("```")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate DubTool evidence tables from job folders")
    parser.add_argument("job_folder", type=Path, help="Primary job folder")
    parser.add_argument("--second-job", type=Path, default=None, help="Optional second job folder to compare")
    parser.add_argument("--label1", default=None, help="Label for job 1")
    parser.add_argument("--label2", default=None, help="Label for job 2")
    parser.add_argument("--out", type=Path, default=None, help="Optional output file")
    args = parser.parse_args(argv)

    info1 = analyze_job(args.job_folder)
    info2 = analyze_job(args.second_job) if args.second_job else None
    report = format_report(info1, info2, label1=args.label1, label2=args.label2)
    sys.stdout.buffer.write(report.encode("utf-8"))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
