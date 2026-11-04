# REVIEW_NOTES.md

## Session: AGENT_UPDATE_11.md work — verified facts only

Working rule (same as previous updates): every section below contains
either a **measured result** from this session (with the command or probe
that produced it) or an explicit "not measured / reason". Full rows live
in `REPORT_UPDATE_11.md`; this file is the reviewer's cheat sheet.

## 1. What this update adds (verified present in code)

- **LLM right-sizing:** chunking by `llm.chunk_max_chars` (1200) and
  `llm.chunk_max_units` (8) for both modes (`stages/_units_runner.py`, new
  file); translate concurrency (3/Accurate) unchanged; latency-adaptive
  chunk size (halve after a request took > 60% of the timeout, floor 400;
  +25% after 3 fast requests, ceiling = default).
- **Real split:** `stages/s3_translate.py` `translate_resilient` now splits
  into two halves after the FIRST 504/503/client-timeout of a chunkable
  chunk (server 504s qualify — verified); `split_min_units` 2 /
  `split_min_chars` 300; an identical large request is resent at most once.
- **Overload policy fires and repeats:** `_units_runner.py` counts
  consecutive 503/504 per model; after `llm.overload_after` (2) the GUI
  dialog is consulted again after each further `overload_after` failures
  while "wait" was chosen; `llm.stage_budget_s` (600) forces the policy and
  logs it; headless/CLI applies `fallback_fast` without a dialog.
- **Visibility:** `dubtool/heartbeat.py` module-global callback replaces
  the ContextVar (which previously never reached worker threads — that was
  the nine absent minutes); `sleeping()` returns the slept seconds so
  ladder budgets exclude waits; every wait emits an event bound ≤ `ui.
  quiet_after_s`.
- **TTS hard timeout:** `EdgeTTSProvider` wraps the whole request in
  `asyncio.wait_for(tts.request_timeout_s=45)`.
- **Repair ladder:** `stages/s4_tts.py` `_Ladder` — direct →
  sentence-split (`tts.max_chars_per_request` 90, ffmpeg concat, 80 ms
  silence) → alternate voice (`tts.alt_voice` vi-VN-NamMinhNeural) →
  alt+split; `attempt_delays_s [2,5]`, `unit_budget_s` 60, deferred rounds
  with `round_cooldown_s [30,60,120]`, breaker pause = round cooldown.
- **Budget dialog + backup voice:** `TtsBudgetDialog` (ask/fallback/stop;
  asked again after each budget period), headless auto-rule:
  fallback if a backup voice is configured else stop (clips kept,
  `tts_unavailable`); Settings → General "Giọng dự phòng" dropdown
  (Windows SAPI / Piper detection) + "Nghe thử" 10-s sample via
  `os.startfile`; disabled while a run is active.
- **Progress text:** stage row "Tạo giọng đọc — N/M xong, K đang thử lại
  (vòng r/R)"; details panel names failing ids and the ladder step.

## 2. Verified before/after (this session's measurements)

| what | command / probe | result |
| --- | --- | --- |
| old code replays the observed multi-hour run | `scripts/replay_run.py` BEFORE (fails on the pre-AGU11 stages) | 6 identical 16-unit sends, backoffs 10/30/90 s, whole-chunk restart, t+1340 s ≈ 22 min, zero dialogs (see REPORT §1) |
| new code replays the same faults | `scripts/replay_run.py` AFTER | split after the first 504 (16→8→4…), dialog at the 2nd consecutive error, stage total t+548 s < 600 s budget |
| aggregate suite | `pytest tests -q` | **508 passed, 6 skipped**, single run, NO `--ignore` |
| TTS experiment | `scripts/tts_experiment.py` → `logs/tts_experiment.log` | variant D (alt voice + split pieces) 42/42 on both groups; split verdicts show rate-based refusals (pass in one trial, refuse in another) |
| exe checks (final build) | `--selftest` / `--layout-check` / `--screenshots` / `--gui-smoke` | all exit 0; 25 screenshots incl. the two new dialog shots; gui-smoke PASS 9 steps with the sanitized home removed at exit |
| fault on exe | `--headless-run … --fault tts:refuse-ids=0,1,2` | honest `tts_unavailable` (segments 2/5/6/7/23; 29 clips kept; resume offered), exit 1 |
| retention (fresh, exe, sanitized) | `scripts/run_retention.sh` → `logs/rt_none|rt_light|rt_full.log` + `logs/run_retention_console.log` | all three levels end `tts_unavailable` (26/22/19 clips kept), homes removed (`leftover rt_ dirs: 0`); second fresh `light` run kept 31 clips, still refused 4 segments |
| disk report notices | `scripts/disk_report.py` | `.freebuff` = sanitized-run homes (937.5 MB here), legacy in-project folders read-only |
| number of jobs/models writes | `app/paths.py` + tests | always build root; legacy dirs read-only |

## 3. Not measured in this session (and why)

- Real Fast/Accurate dub with sound and a per-stage seconds table on a
  healthy network (today's network refused enough edge-tts requests that
  every real run ended `tts_unavailable`; no user quota was spent on a
  probably-same-day refusal).
- LLM fault kinds on the exe (`llm:504-first=2`, `llm:503-first=3`): the
  real model calls fail before the injection point on today's network —
  covered by the replay + unit tests instead.
- Screenshots of the "đang thử lại" stage row and the mid-wait details
  panel: a GUI run with live TTS fights cannot be reproduced offscreen on
  this network; the two new dialogs ARE captured.

## 4. Carry-over items (Task 3) — status per item

1 build-root jobs/models always ✓ (notice + doctor) — 2 sample_output ✓ —
3 sanitized homes removed + run_retention rebuilt outside `build/` ✓ —
4 review zip includes `docs/` + all REPORT_UPDATE_*.md ✓ — 5 aggregate
suite without `--ignore` ✓ (6 skips listed in the report) — 6 by-group
package table ✓ (first-ever baseline; before/after vs AGU10 not possible —
recorded in the report) — 7 GitHub line inside the report ✓ + user
questions (repo visibility; the `agent/AGENT_UPDATE_*.md` files quote
phrases from the test video) — 8 `.freebuff` description corrected
("sanitized-run HOME folders of past verification runs", never written
again) ✓ — 9 fresh `light` measurement (table numbers remain AGU10's
because this network cannot finish the runs; both runs honestly recorded)
✓.

## 5. Zip file list (appended by scripts/make_review_zip.py)
