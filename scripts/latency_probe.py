"""AGENT_UPDATE_4.md 4.2: latency investigation for gemini-3.8-flash.

Sends the exact comparison prompt (same units, budgets computed with the
chars_per_sec that was in force during the comparison, empty context pinned;
no pronoun block interference — the probe uses its own prompt builder) to the
model three times with the default generation config, then once with the
documented thinking control (thinking_level='low'). Reports per call: total
seconds, input/output/thinking tokens. Default config is NOT changed.

Usage: py scripts/latency_probe.py <job_id> [--units 20] [--model gemini-3.8-flash]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dubtool.config import load_env  # noqa: E402
from dubtool.job import read_json  # noqa: E402  (cwd must be the project root)
from dubtool.llm import prompts  # noqa: E402
from dubtool.llm.base import RateLimitError  # noqa: E402
from dubtool.units import build_units  # noqa: E402

CHARS_PER_SEC_AT_COMPARISON = 8.0
REPEATS = 3


def main() -> None:
    args = sys.argv[1:]
    job_id = args[0] if args else "test1-e5df32ffda"
    model = "gemini-3.8-flash"
    if "--model" in args:
        model = args[args.index("--model") + 1]
    count = 20
    if "--units" in args:
        count = int(args[args.index("--units") + 1])
    load_env(Path.cwd())

    job = Path("jobs") / job_id
    transcript = read_json(job / "transcript.json")
    units = build_units(
        transcript,
        max_segments=4,
        max_duration_s=14.0,
        max_gap_s=1.0,
    )
    selected = units[:count]
    for u in selected:
        u["max_chars"] = sum(
            int((s["end"] - s["start"]) * CHARS_PER_SEC_AT_COMPARISON)
            for s in transcript
            if s["id"] in u["seg_ids"]
        )
    payloads = [{"id": u["unit_id"], "text": u["text"], "max_chars": u["max_chars"]} for u in selected]
    context = []  # start=0 in the comparison: no previous units
    user = prompts.render_translate_user(payloads, context, {}, brief=None)
    system = prompts.TRANSLATE_SYSTEM
    print(f"probe: {model} x{REPEATS} default + 1x thinking_level='low', {count} units")

    from dubtool.llm.gemini import GeminiProvider

    def one_call(label: str, thinking_level: str | None):
        provider = GeminiProvider(model=model)
        engine = provider.client.models.generate_content
        started = time.monotonic()
        try:
            if thinking_level:
                types = provider._types
                response = engine(
                    model=provider.model,
                    contents=[user],
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.3,
                        response_mime_type="application/json",
                        thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
                    ),
                )
            else:
                response = engine(
                    model=provider.model,
                    contents=[user],
                    config=provider._types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.3,
                        response_mime_type="application/json",
                    ),
                )
            elapsed = time.monotonic() - started
            usage = getattr(response, "usage_metadata", None)
            text = response.text
            return {
                "label": label,
                "thinking_level": thinking_level,
                "elapsed_s": round(elapsed, 2),
                "input_tokens": getattr(usage, "prompt_token_count", None),
                "output_tokens": getattr(usage, "candidates_token_count", None),
                "thinking_tokens": getattr(usage, "thoughts_token_count", None),
                "chars": len(text or ""),
                "error": None,
                "text": text,
            }
        except Exception as exc:
            elapsed = time.monotonic() - started
            return {"label": label, "thinking_level": thinking_level, "elapsed_s": round(elapsed, 2),
                    "error": str(exc)[:300], "retries_or_waits": "none yet" if False else "n/a"}

    results = []
    for i in range(REPEATS):
        print(f"call {i + 1}/{REPEATS} (default)...", flush=True)
        results.append(one_call(f"default-{i + 1}", None))
        r = results[-1]
        if r.get("error"):
            print("ERROR:", r["error"])
            r["retries_or_waits"] = "none (single raw call; on error: propagate)"
        else:
            print(f"  {r['elapsed_s']}s in={r['input_tokens']} out={r['output_tokens']} "
                  f"thinking={r['thinking_tokens']} chars={r['chars']}")

    print("call 4 thinking_level='low' ...", flush=True)
    low = one_call("thinking-low", "low")
    if low.get("error"):
        print("ERROR:", low["error"])
        low["retries_or_waits"] = "none (single raw call; propagate)"
    else:
        low["retries_or_waits"] = "none (single raw call)"
        print(f"  {low['elapsed_s']}s in={low['input_tokens']} out={low['output_tokens']} "
              f"thinking={low['thinking_tokens']} chars={low['chars']}")
    results.append(low)

    out = job / "compare" / f"latency_probe_{model.replace(':', '_')}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out}")


if __name__ == "__main__":
    main()
