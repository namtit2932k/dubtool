"""Timing report numbers for AGENT_UPDATE_4.md 4.4 (measured, offline).

Usage: py scripts/timing_report.py <job_dir> <run_label>
Reads translated.before_update4.json (before enforcement), the run-final
translated.json, schedule.json and the run log; prints one table per metric.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

job = Path(sys.argv[1])
label = sys.argv[2]
job_id = job.name


def load(name: str):
    path = job / name
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def pct(ratios: list[float]) -> str:
    if not ratios:
        return "-"
    s = sorted(ratios)
    return f"{s[len(s) // 2]:.2f}"


before = load("translated.before_update4.json") or []
final = load("translated.json") or []
schedule = load("schedule.json") or []

budget: dict[int, int] = {e["id"]: e.get("max_chars", 0) for e in before}


def over_budget(entries, name: str) -> str:
    if not entries:
        return f"{name}: n/a"
    ratios, count = [], 0
    for e in entries:
        cap = budget.get(e["id"], 0) or e.get("max_chars", 0)
        if not cap or not e.get("vi"):
            continue
        r = len(e["vi"]) / cap
        ratios.append(r)
        if r > 1.0:
            count += 1
    return f"{name}: {count}/{len(ratios)} over budget (len/max_chars), median ratio {pct(ratios)}"


print(f"== {label} ({job_id}) ==")
print(over_budget(before, "s3 BEFORE enforcement"))
print(over_budget(final, "s3 AFTER enforcement"))
print()

fit = {}
for e in final:
    s = e.get("fit_status", "pending")
    fit[s] = fit.get(s, 0) + 1
print("fit_status counts after s5:", json.dumps(fit, sort_keys=True))
print()

log_file = sys.argv[3] if len(sys.argv) > 3 else None
if log_file and Path(log_file).exists():
    text = Path(log_file).read_text(encoding="utf-8", errors="ignore")
    print("s5 LLM shorten calls:", len(re.findall(r"s5: id=\d+ shortened", text)))
    print("s5 shorten attempts:", len(re.findall(r"shorten\(", text)))
    print("s4 TTS regenerations inside s5:", len(re.findall(r"s5: id=\d+ synthesized", text)))
    for m in re.finditer(r"(s\d) \([^)]+\): finished in ([\d.]+)s", text):
        print("stage", m.group(1), m.group(2) + "s")
print()

if schedule:
    shifted = [e for e in schedule if e.get("shift", 0) > 0]
    overlaps = [e for e in schedule if e.get("overlap")]
    overlap_speech = 0.0
    for i, e in enumerate(overlaps):
        # Vietnamese speech overlapping other Vietnamese speech: clip spans
        # that begin before the previous clip's play_end.
        prev = schedule[i - 1] if i > 0 else None
        if prev and e["play_start"] < prev.get("play_end", 0):
            overlap_speech += min(e.get("play_end", e["play_start"]), prev["play_end"]) - e["play_start"]
    max_shift = max((e.get("shift", 0) for e in schedule), default=0.0)
    total_shift = sum(e.get("shift", 0) for e in schedule)
    lags = [e["play_start"] - e["orig_start"] for e in schedule]
    print(f"schedule: {len(schedule)} clips, shifted {len(shifted)}, "
          f"max shift {max_shift:.2f}s, total shift {total_shift:.2f}s, "
          f"overlap:true {len(overlaps)}, Vietnamese speech overlapping {overlap_speech:.2f}s")
    print(f"lag: max {max(lags, default=0):.2f}s, at video end {lags[-1]:.2f}s")
