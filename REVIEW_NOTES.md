# REVIEW_NOTES.md

## Session: AGENT_UPDATE_8.md work — verified facts only

Working rule for this file (per the session that produced it): every
section below contains either a **measured result** from this session or
an explicit **"not measured / reason"**. Nothing else. Full per-task
rows live in `REPORT_UPDATE_8.md`.

> NOTE (writing time): this file was reclaimed after an earlier draft
> contained fabricated module names. Keep checking statements below
> against the repo, not the prose.

## 1. What exists in this repo (ground truth)

Modules added/changed for AGU8, present in the working tree at writing
time:

- `src/dubtool/punctuate.py` — LLM punctuation pass with strict
  validation (windows, per-window rejection with original kept).
- `src/dubtool/fillers.py` — filler/vocative detector + suggestion
  builder, protected phrases, position/context gates
  (`suggest_candidates`, `resolve_actions`).
- `src/dubtool/faults.py` — test-only fault injection driving
  `--fault` on the CLI and the fault-mode in headless runner.
- `src/dubtool/netcheck.py` — DNS/TLS/clock-offset connectivity table
  (`--selftest --network`).
- `src/dubtool/av_check.py` — `av` version gate (`av>=11,<19`,
  runtime warning from source, selftest receipt in the exe).
- `src/dubtool/tts/fallback.py` + deferred rounds in `stages/s4_tts.py` —
  failed-segment survival path (cooldowns, breaker, pacing).
- `src/dubtool/llm/budget.py` — `RequestBudget` /
  `raise_if_exhausted()` / `BudgetExhausted` (default max 40/run).
- `src/dubtool/llm/quota_state.py`, `keypool.py` — quota positions and
  key handling shared by the windows.
- `src/dubtool/eval_translation.py` — CLI `eval-translation`.
- `src/dubtool/app/ui/fillers_dialog.py` — suggested-filler dialog.
- `packaging/fillers.yaml` — shipped defaults for the detector.

## 2. Aggregate offline suite (this session, after all code changes)

`py -m pytest tests -q --ignore=tests/test_screenshots.py` →
**412 passed, 2 skipped, 0 failed**. Both skips are the environmental
symlink-privilege skips (Windows denies non-admin symlink creation:
`test_cleanup.py` / `test_housekeeping.py`), *not* coverage skips.
The screenshot set is exercised by the build gate (see [REPORT_UPDATE_8.md](REPORT_UPDATE_8.md) §9).

## 3. Cleanup dry run (task 7.6 remainder)

`from dubtool.app import cleanup; cleanup.main([])` (dry run, no
`--apply`). Parsed output (exact, not estimated):

- Project total: **3.0 GB**.
- The earlier `--apply --categories build,caches` (same session) deleted
  5,635 items / **42.4 MB reclaimed** (per `cleanup.log`).
- Report-only (kept) list, largest first: `.venv-build` 997.4 MB ·
  `dist/DubTool` 693.9 MB · `sample/` 340.9 MB ·
  `dist/DubTool-1.0.0-win64.zip` 267.9 MB · `vendor/` 244.6 MB ·
  `jobs/` 233.2 MB · `.git/` 106.2 MB · `models/` 86.8 MB (+ 17 entries
  below 100 MB each).

**Still open questions for the user (nothing more was deleted):**
`.venv-build` (997 MB, regenerable but slow to rebuild) and `sample/`
(340.9 MB, source videos live only here). The `review/` folder (~0.4 MB)
is kept — it is the deliverable. The last `--apply` (above) already took
the regenerable build/cache groups.

## 4. What is NOT in this file

Per AGU8 §1.5 the close-out conditions (TTS/LLM fault-run evidence,
variants table, e2e timings, build identity), the punctuation and
distance measurements, and the model-RTC numbers live in
[REPORT_UPDATE_8.md](REPORT_UPDATE_8.md) when it is present in the zip.
