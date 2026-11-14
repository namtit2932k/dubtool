"""Live voice validation experiment for AGENT_UPDATE_15 Task 4 (4.1 - 4.3).

Runs Condition A (Update 14 settings) and Condition B (Update 12 baseline)
on the 1.0.15 packaged exe and from source, interleaved in the same time
window (60 requests per condition and per environment + 1 control request
every 10 requests), computes exact two-sided Fisher exact test p-values,
and evaluates the written decision rule for `config.yaml`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from dubtool import proc, workspace  # noqa: E402


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test p-value for a 2x2 contingency table:
        [[a, b],
         [c, d]]
    Implemented with exact hypergeometric probabilities via math.comb.
    """
    r1 = a + b
    r2 = c + d
    c1 = a + c
    n = r1 + r2
    if n == 0:
        return 1.0

    denom = math.comb(n, c1)

    def prob(x: int) -> float:
        return (math.comb(r1, x) * math.comb(r2, c1 - x)) / denom

    p_obs = prob(a)
    min_x = max(0, c1 - r2)
    max_x = min(r1, c1)
    p_val = sum(prob(x) for x in range(min_x, max_x + 1) if prob(x) <= p_obs + 1e-12)
    return min(1.0, max(0.0, p_val))


def _merge_bucket(dst: dict, src: dict) -> None:
    dst["requests"] += int(src.get("requests", 0))
    dst["ok"] += int(src.get("ok", 0))
    dst["handshake_503"] += int(src.get("handshake_503", 0))
    dst["NoAudioReceived"] += int(src.get("NoAudioReceived", 0))
    dst["timeout"] += int(src.get("timeout", 0))
    dst["retried_ok"] += int(src.get("retried_ok", 0))
    dst["breaker_pauses"] += int(src.get("breaker_pauses", 0))
    dst["elapsed_s"].extend(src.get("elapsed_s") or [])
    for k, v in (src.get("failures_by_kind") or {}).items():
        dst["failures_by_kind"][k] = dst["failures_by_kind"].get(k, 0) + int(v)


def _new_bucket() -> dict:
    return {
        "requests": 0,
        "ok": 0,
        "handshake_503": 0,
        "NoAudioReceived": 0,
        "timeout": 0,
        "failures_by_kind": {},
        "elapsed_s": [],
        "retried_ok": 0,
        "breaker_pauses": 0,
    }


def _fmt_other_kinds(b: dict) -> str:
    extra = {
        k: v
        for k, v in sorted((b.get("failures_by_kind") or {}).items())
        if k not in ("handshake_503", "NoAudioReceived", "timeout") and v > 0
    }
    if not extra:
        return "none (0)"
    return ", ".join(f"{k}={v}" for k, v in extra.items())


def _mean_s(b: dict) -> float:
    el = b.get("elapsed_s") or []
    return round(statistics.mean(el), 3) if el else 0.0


def _median_s(b: dict) -> float:
    el = b.get("elapsed_s") or []
    return round(statistics.median(el), 3) if el else 0.0


def run_validation(total_texts: int = 60, slice_size: int = 10, seed: int = 20261114) -> tuple[dict, Path]:
    build_root = workspace.build_root()
    exe_path = workspace.dist_dir() / "DubTool" / "DubTool.exe"
    if not exe_path.is_file():
        raise RuntimeError(f"Packaged exe not found at {exe_path}")

    tmp_dir = build_root / "tmp" / "voice-ab-slices"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    agg = {
        "exe": {"A": _new_bucket(), "B": _new_bucket(), "control": _new_bucket()},
        "source": {"A": _new_bucket(), "B": _new_bucket(), "control": _new_bucket()},
    }
    rng = random.Random(seed)
    started_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for start_idx in range(0, total_texts, slice_size):
        count = min(slice_size, total_texts - start_idx)
        envs = ["exe", "source"]
        rng.shuffle(envs)
        for env_name in envs:
            slice_out = tmp_dir / f"slice_{env_name}_{start_idx}.json"
            slice_out.unlink(missing_ok=True)
            if env_name == "exe":
                cmd = [str(exe_path), "--tts-probe-ab", str(slice_out), str(start_idx), str(count)]
            else:
                cmd = [
                    sys.executable,
                    "-m",
                    "dubtool.app.main",
                    "--tts-probe-ab",
                    str(slice_out),
                    str(start_idx),
                    str(count),
                ]
            print(f"[voice-ab] running {env_name} slice {start_idx}..{start_idx + count - 1} ...", flush=True)
            done = proc.run(cmd, cwd=str(ROOT), timeout=600)
            if done.returncode != 0 or not slice_out.is_file():
                raise RuntimeError(
                    f"Voice probe slice failed ({env_name} {start_idx}): exit={done.returncode}\n"
                    f"STDOUT: {done.stdout}\nSTDERR: {done.stderr}"
                )
            data = json.loads(slice_out.read_text(encoding="utf-8"))
            for key in ("A", "B", "control"):
                _merge_bucket(agg[env_name][key], data[key])

    ended_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Compute Fisher exact tests (ok vs failed)
    def _ok_fail(b: dict) -> tuple[int, int]:
        ok = int(b["ok"])
        fail = int(b["requests"]) - ok
        return ok, fail

    exe_a_ok, exe_a_fail = _ok_fail(agg["exe"]["A"])
    exe_b_ok, exe_b_fail = _ok_fail(agg["exe"]["B"])
    src_a_ok, src_a_fail = _ok_fail(agg["source"]["A"])
    src_b_ok, src_b_fail = _ok_fail(agg["source"]["B"])

    p_exe_vs_src_a = fisher_exact_two_sided(exe_a_ok, exe_a_fail, src_a_ok, src_a_fail)
    p_exe_vs_src_b = fisher_exact_two_sided(exe_b_ok, exe_b_fail, src_b_ok, src_b_fail)
    p_exe_a_vs_b = fisher_exact_two_sided(exe_a_ok, exe_a_fail, exe_b_ok, exe_b_fail)
    p_src_a_vs_b = fisher_exact_two_sided(src_a_ok, src_a_fail, src_b_ok, src_b_fail)
    p_pooled_a_vs_b = fisher_exact_two_sided(
        exe_a_ok + src_a_ok,
        exe_a_fail + src_a_fail,
        exe_b_ok + src_b_ok,
        exe_b_fail + src_b_fail,
    )

    acc_exe_a = 100.0 * exe_a_ok / max(1, agg["exe"]["A"]["requests"])
    acc_exe_b = 100.0 * exe_b_ok / max(1, agg["exe"]["B"]["requests"])
    acc_src_a = 100.0 * src_a_ok / max(1, agg["source"]["A"]["requests"])
    acc_src_b = 100.0 * src_b_ok / max(1, agg["source"]["B"]["requests"])

    ctrl_total_req = agg["exe"]["control"]["requests"] + agg["source"]["control"]["requests"]
    ctrl_total_ok = agg["exe"]["control"]["ok"] + agg["source"]["control"]["ok"]
    ctrl_acc = 100.0 * ctrl_total_ok / max(1, ctrl_total_req)

    # Written decision rule (AGENT_UPDATE_15 Task 4.3):
    # Keep Update 14 settings (A) only if A's acceptance is not lower than B's
    # by 5 points or more in either environment AND A has no more handshake 503 than B.
    keep_a = (
        (acc_exe_a >= acc_exe_b - 5.0)
        and (acc_src_a >= acc_src_b - 5.0)
        and (agg["exe"]["A"]["handshake_503"] <= agg["exe"]["B"]["handshake_503"])
        and (agg["source"]["A"]["handshake_503"] <= agg["source"]["B"]["handshake_503"])
    )
    decision = "KEEP_A (Update 14 settings)" if keep_a else "REVERT_TO_B (Update 12 baseline)"

    summary = {
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "seed": seed,
        "total_texts_per_condition_per_env": total_texts,
        "exe": agg["exe"],
        "source": agg["source"],
        "fisher_two_sided_p": {
            "exe_vs_source_A": round(p_exe_vs_src_a, 6),
            "exe_vs_source_B": round(p_exe_vs_src_b, 6),
            "exe_A_vs_B": round(p_exe_a_vs_b, 6),
            "source_A_vs_B": round(p_src_a_vs_b, 6),
            "pooled_A_vs_B": round(p_pooled_a_vs_b, 6),
        },
        "control_acceptance_pct": round(ctrl_acc, 2),
        "keep_update_14_defaults": keep_a,
        "decision": decision,
    }

    md_lines = [
        "### Live Voice Validation Experiment (1.0.15 Exe vs Source, Condition A vs Condition B)",
        "",
        f"- **Time window (UTC):** `{started_utc}` → `{ended_utc}`",
        f"- **Seed:** `{seed}` (interleaved & randomized per text pair across environments and conditions)",
        "- **Voice:** `vi-VN-HoaiMyNeural`",
        f"- **Control acceptance:** `{ctrl_total_ok}/{ctrl_total_req}` (`{ctrl_acc:.1f}%`)",
        f"- **Decision rule outcome:** **{decision}**",
        "",
        "| Environment | Condition | Requests | Accepted (`ok`) | Acceptance (%) | `handshake_503` | `NoAudioReceived` | `timeout` | Other Failure Kinds (by exact name) | Mean / Median Time per Req (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for env_label, env_key in (("1.0.15 Exe", "exe"), ("Source", "source")):
        for cond_label, cond_key in (
            ("A (Update 14)", "A"),
            ("B (Update 12 baseline)", "B"),
            ("Control (every 10 reqs)", "control"),
        ):
            b = agg[env_key][cond_key]
            reqs = b["requests"]
            ok = b["ok"]
            pct = 100.0 * ok / max(1, reqs)
            md_lines.append(
                f"| {env_label} | {cond_label} | {reqs} | {ok} | {pct:.1f}% | "
                f"{b['handshake_503']} | {b['NoAudioReceived']} | {b['timeout']} | "
                f"`{_fmt_other_kinds(b)}` | {_mean_s(b):.3f} s / {_median_s(b):.3f} s |"
            )

    md_lines += [
        "",
        "#### Two-Sided Fisher Exact Test p-values",
        "",
        "| Comparison | 2x2 Table (`[ok_1, fail_1; ok_2, fail_2]`) | Two-Sided Fisher Exact `p`-value |",
        "| --- | --- | --- |",
        f"| 1.0.15 Exe (A) vs Source (A) | `[[{exe_a_ok}, {exe_a_fail}], [{src_a_ok}, {src_a_fail}]]` | `{p_exe_vs_src_a:.4f}` |",
        f"| 1.0.15 Exe (B) vs Source (B) | `[[{exe_b_ok}, {exe_b_fail}], [{src_b_ok}, {src_b_fail}]]` | `{p_exe_vs_src_b:.4f}` |",
        f"| 1.0.15 Exe: Condition A vs B | `[[{exe_a_ok}, {exe_a_fail}], [{exe_b_ok}, {exe_b_fail}]]` | `{p_exe_a_vs_b:.4f}` |",
        f"| Source: Condition A vs B | `[[{src_a_ok}, {src_a_fail}], [{src_b_ok}, {src_b_fail}]]` | `{p_src_a_vs_b:.4f}` |",
        f"| Pooled (Exe + Source): Condition A vs B | `[[{exe_a_ok + src_a_ok}, {exe_a_fail + src_a_fail}], [{exe_b_ok + src_b_ok}, {exe_b_fail + src_b_fail}]]` | `{p_pooled_a_vs_b:.4f}` |",
        "",
        "> **What these p-values do NOT show:** A probe conducted during a healthy Microsoft Edge TTS service window only demonstrates that Condition A's higher concurrency (`2`) and rate (`1.5 req/s`) do not trigger self-inflicted throttling when the upstream service is healthy; it cannot speak for unhealthy service windows or regional Microsoft outages on other days.",
        "",
    ]

    logs_dir = build_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    json_path = logs_dir / "voice-validation-15.json"
    md_path = logs_dir / "voice-validation-15.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print("\n".join(md_lines), flush=True)
    return summary, md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Task 4 live voice validation")
    parser.add_argument("--texts", type=int, default=60)
    parser.add_argument("--slice-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261114)
    args = parser.parse_args(argv)
    run_validation(total_texts=args.texts, slice_size=args.slice_size, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
