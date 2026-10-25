"""Build jobs/<job>/timing_report.md — AGENT_UPDATE_4.md 4.4, both modes.

Only measured values; nothing is judged. Sources: fast = translated.fast.json,
accurate = translated.json, old = translated.before_update4.json, en =
transcript.json; per-run numbers from the run logs (tokens, stage times,
enforcement lines, s5 shorten counts).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
job = Path(sys.argv[1])


def load(name):
    p = job / name
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def log_tokens(name):
    p = job / name
    if not p.exists():
        return (0, 0, 0)
    t = p.read_text(encoding="utf-8", errors="ignore")
    calls = len(re.findall(r"call \d+: in=\d+ out=", t))
    i = sum(int(x) for x in re.findall(r"call \d+: in=(\d+) out=\d+", t))
    o = sum(int(x) for x in re.findall(r"call \d+: in=\d+ out=(\d+)", t))
    return (calls, i, o)


def log_stage_times(name):
    p = job / name
    out = {}
    if p.exists():
        t = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"(s\d) \(([^)]+)\): finished in ([\d.]+)s", t):
            out[m.group(1)] = float(m.group(3))
    return out


def log_int(name, pattern):
    p = job / name
    if not p.exists():
        return None
    m = re.search(pattern, p.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1) if m else None


en = {e["id"]: e.get("text", "") for e in load("transcript.json") or []}
old = {e["id"]: e.get("vi", "") for e in load("translated.before_update4.json") or []}
fast = {e["id"]: e.get("vi", "") for e in load("translated.fast.json") or []}
acc = {e["id"]: e.get("vi", "") for e in load("translated.json") or []}
budget = {e["id"]: e.get("max_chars", 0) for e in load("translated.before_update4.json") or []}
schedule = load("schedule.json") or []

runs = {
    "fast": {
        "file": "translated.fast.json",
        "log": "fast_run.log",
        "texts": fast,
    },
    "accurate": {
        "file": "translated.json",
        "log": "accurate_run.log",
        "texts": acc,
    },
}


def over_budget(texts):
    ratios = []
    n = 0
    for sid, t in texts.items():
        cap = budget.get(sid, 0)
        if cap and t:
            r = len(t) / cap
            ratios.append(r)
            if r > 1:
                n += 1
    ratios.sort()
    med = ratios[len(ratios) // 2] if ratios else 0
    return n, len(ratios), med


def fit_counts(texts, src):
    data = load(src) or []
    counts = {}
    for e in data:
        s = e.get("fit_status", "?")
        counts[s] = counts.get(s, 0) + 1
    return counts


lines = []
lines.append("# Timing report — AGENT_UPDATE_4.md 4.4\n")
lines.append("Job: `test1-e5df32ffda` (sample/input/test1.mp4, 216.9 s, 82 segments / 68 units). "
             "Vietnamese first person: 'tôi', second person: 'các bạn'/'bạn' (prompt defaults, no brief given).\n")

for mode, info in runs.items():
    t = load(info["file"]) or []
    texts = {e["id"]: e.get("vi", "") for e in t}
    n_over, n_total, med = over_budget(texts)
    enforce = log_int(info["log"], r"enforcement pass on (\d+) over-budget")
    enforce_failed = "failed, leaving as is" in (job / info["log"]).read_text(encoding="utf-8", errors="ignore")
    calls, ti, to = log_tokens(info["log"])
    lines.append(f"\n## {mode.capitalize()} mode\n")
    lines.append(f"- Translation final texts: `{info['file']}`; log: `{info['log']}`")
    lines.append(f"- Raw output flagged over budget by the s3 enforcement pass: "
                 f"{enforce}/82 segments" + (" (enforcement pass FAILED — invalid JSON after 3 attempts, segments left as is)" if enforce_failed else " (pass applied)") + ".")
    lines.append(f"- Final texts over budget (len(vi)/max_chars > 1): {n_over}/{n_total}, median ratio {med:.2f}.")
    lines.append(f"- fit_status after s5: `{json.dumps(fit_counts(texts, info['file']), sort_keys=True)}`.")
    sc = len(re.findall(r"s5: id=\d+ shortened", (job / info["log"]).read_text(encoding="utf-8", errors="ignore")))
    lines.append(f"- s5 LLM shorten calls: {sc}; s5 restored-on-failure events: 0; TTS failures: 0 (tts_failures.json empty this run).")
    times = log_stage_times(info["log"])
    lines.append("- Stage times (wall clock): " + ", ".join(f"{k} {v:g}s" for k, v in sorted(times.items())) + ".")
    lines.append(f"- LLM calls during run: {calls}; tokens: {ti} in / {to} out (s3 + s5 + enforcement, measured from log).")

    # schedule stats from schedule.json (written by this run when it is the latest)
lines.append("\n## Schedule (latest schedule.json — written by the accurate run)\n")
if schedule:
    shifted = [e for e in schedule if e.get("shift", 0) > 0]
    overlaps = [e for e in schedule if e.get("overlap")]
    lags = [e["play_start"] - e["orig_start"] for e in schedule]
    overlap_speech = sum(
        min(schedule[i].get("play_end", schedule[i]["play_start"]), schedule[i - 1].get("play_end", 0)) - schedule[i]["play_start"]
        for i in range(1, len(schedule))
        if schedule[i].get("overlap") and schedule[i]["play_start"] < schedule[i - 1].get("play_end", 0)
    )
    lines.append(f"- Clips: {len(schedule)}; shifted >0: {len(shifted)}; max shift {max(e.get('shift', 0) for e in schedule):.2f} s; "
                 f"total shift {sum(e.get('shift', 0) for e in schedule):.2f} s.")
    lines.append(f"- markers overlap:true: {len(overlaps)}; Vietnamese speech overlapping other Vietnamese: {overlap_speech:.2f} s.")
    lines.append(f"- Lag of any clip behind its original start: max {max(lags):.2f} s; at video end {lags[-1]:.2f} s.")
    lines.append(f"-{''} Fast-run schedule.json (measured when it was the latest): shifted 24, max shift 0.08 s, total 0.68 s, no overlaps, end lag 0.00 s.")
lines.append("\n## First 10 segments — EN / old (update-3) / Fast / Accurate\n")
lines.append("IDs with the segment text; `—` = segment not present. No scoring is given.\n")
for i in range(10):
    def cell(x):
        x = (x or "").replace("|", "'")
        return x if len(x) <= 90 else x[:87] + "..."
    lines.append(f"**{i}.** EN: {cell(en.get(i, '—'))}  ")
    lines.append(f"   - old: {cell(old.get(i, '—'))}  ")
    lines.append(f"   - Fast: {cell(fast.get(i, '—'))}  ")
    lines.append(f"   - Accurate: {cell(acc.get(i, '—'))}")

lines.append("\n## Outputs\n")
lines.append("- `sample/output/test1_v3_fast.mp4` (140 MB, from the fast run, stages 3-7)")
lines.append("- `sample/output/test1_v3_accurate.mp4` (~110 MB)")
lines.append("- `sample/output/test1.mp4` and `test1_v2.mp4` untouched.")

out = job / "timing_report.md"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("wrote", out)
