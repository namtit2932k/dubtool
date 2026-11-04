# REPORT_UPDATE_12.md

Update number **12** produced version **1.0.12**, build id `Update 12 (0bcc723)`.
Spec: `agent/AGENT_UPDATE_12.md`. Every claim below is backed by a command
whose output is recorded in `D:\Projects\Vibe\Project_K_build\logs\` (this
update's evidence lives under files starting `agu12_`, `selftest_12.json`,
and `agu12_suite_final.log`).

## 1. Files added / changed

- **Task 1 (LLM retry policy):** `src/dubtool/llm/client.py` (rewritten:
  `cap_attempts`, per-minute global pause, `_rate_gate`, `_split_eligible`,
  `_maybe_split` only for 504/timeout/400, `_handle_overload(quota_gone=)`,
  breaker recording), `src/dubtool/llm/breaker.py` (new model circuit
  breaker), `src/dubtool/llm/checkpoints.py` + chunk store in
  `src/dubtool/stages/s3_translate.py` (`translate_chunks.json`, model
  stamps, prompt hash), `src/dubtool/llm/gemini.py` (daily-quota fallback),
  `src/dubtool/llm/base.py` (`RateLimitError.kind`), config keys
  (`llm.backoff_s`, `split_min_*`, `breaker_*`, `overload_policy: fallback_fast`,
  `stage_budget_s: 300`), Settings → General overload dropdown,
  `watch`-based `heartbeat` events for the fallback notice/breaker panel
  (`i18n.py` new keys), fault kinds `llm:504-first=N` and `llm:429-day`
  (`src/dubtool/faults.py`, `src/dubtool/llm/__init__.py`).
- **Task 2 (sentence mode):** `src/dubtool/dub_units.py` (new), s3 fallback
  notice block, s4/s5/s6/s7 dual-mode plumbing via
  `dub_units.source_for_stage`, s2 boundary-repeat collapse, new prompts
  (budget = information, whole sentences), `dub.granularity` config,
  `job.dub_units_path`, schedule keyed by unit id, s8 reads `dub_units.json`.
  Per-request plain-encoder conversions were disabled: the standard
  unit-encoder path is used everywhere instead.
- **Task 3 (one voice):** s4 ladder without alt-voice steps, state.json
  voice, whole-video `switch_all`, budget dialog with 3 choices +
  tooltip, backup "(trên máy)" labels + same-service warning line,
  summary voice line.
- **Task 4 (rate limiter):** `VoiceRequestLimiter` in `s4_tts.py`
  (min interval 5 s floor, 10-request window, slow recovery), probe module
  `src/dubtool/tts_probe.py`, CLI `--tts-probe`, `scripts/tts_experiment.py`
  wrapper (same code from source and from the exe), limiter visible in the
  panel via "tts.rate".
- **Task 5/6/7:** `run_screenshots` real-platform gate + font fallback +
  tofu check, `scripts/check_readme_links.py` image checks,
  `scripts/scan_history_secrets.py` (new), README License paragraph,
  version single-source bump to `1.0.12`.

## 2. Commands run and aggregate tests

- Replay tests written from the stored evidence **failed on the old code
  first** (recorded before the fix in `build-root/logs/agu12_task1_before.log`):
  split growth on 503, unsigned per-minute quota errors, missing
  kind/checkpoints, kept-stages signal not recorded.
- Aggregate suite (one run, no `--ignore`, after the fix):
  `python -m pytest tests -q` → **535 passed, 6 skipped** in 51.00 s
  (`build-root/logs/agu12_suite_final.log` — the count is quoted once,
  from this run).
- Package/build full command (rebuilds and demonstrates evidence):
  `powershell -File packaging/build_exe.ps1` → the offline suite inside
  the build also passed; `--selftest --json` → **overall PASS**
  (`logs/selftest_12.json`); `--layout-check` → **0 violations**;
  `dist\DubTool.exe --gui-smoke` → **PASS (9 steps)** with the sanitized
  home removed after the run. `dist\DubTool.exe --version` →
  `DubTool 1.0.12 / build id: Update 12 (0bcc723)`.

## 3. Task 1 — Gemini retry policy

- 503 never splits: the resplit ladder runs only for 504 / 400 / timeout
  and only above `split_min_units`/`split_min_chars` (4 / 600); caps: ≤
  `len(backoff_s)` requests per chunk, ≤ len(chunk)×attempts + fallback
  chunks overall.
- Per-minute 429 → a global model pause honoring `retryDelay`
  (`heartbeat.sleeping`), not a per-request sleep.
- Per-day 429 → recognized on no-key for the selected model (straight to
  fallback, counted in `fallback_notice` in the details panel and
  summary) — recorded in the replay evidence file
  (`build-root/logs/agu12_task1_before.log` + the commit's test file).
- Breaker: `breaker_failures: 3` consecutive failed requests, opens
  `breaker_cooldown_s: 60` and collapses the details panel row
  ("llm.breaker_open"); concurrency resets to 1 on any 503/429 and
  climbs back one per 5 consecutive successes.
- Chunk store: one record per completed chunk in `translate_chunks.json`
  (model stamped, prompt hash = glossary + pronouns), reused on Resume,
  cleared by Start over.

## 4. Task 2 — dub whole sentences

- `dub_units.json` layout and `build_supersegments` + `build_units`
  integration are contract-tested (sentence records carry
  `unit_id/seg_ids/start/end/text/vi/max_chars/model/voice/tts_duration/
  fit_status/reviewed`); `facts:translated.json` scans keep passing.
- Boundary repeat evidence pair (`the universe | universe must work`) is
  covered by `collapse_boundary_word` tests with word timestamps; the
  `very`/`no` stop-list tests exist (legitimate doubles stay).
- s7 splits one long sentence into several cues with proportional times
  (proven by `test_split_unit_cues_long_sentence_proportional` pinned in
  `tests/test_agu12_dub.py`).

## 5. Tasks 3 + 4 in the built exe

- The exe fault runs can be exercised with the extended spec set:
  `llm:503-first=8`, `llm:429-day`, `llm:504-first=2`,
  `tts:refuse-ids=3,5,8`, `tts:hang=1` (all parse-tested;
  `tests/test_llm_faults.py`).
- NOT yet executed in this budget: full end-to-end fault runs against a
  real video from the exe and the interleaved TTS experiment from BOTH
  source and exe (`--tts-probe` exists and is wired; one full interleaved
  run alone costs ~10 minutes of honest real-time window requests).
  These remain listed as pending verification (section 9), not as
  done.

## 6. The public repository (task 6)

- **History scan:** `scripts/scan_history_secrets.py` scanned 542 objects
  reachable from all refs for the stored key values and the usual key
  the usual key patterns (the Gemini / Anthropic / GitHub token
  prefixes), `.env`-style
  `KEY=value` lines, and file names `*.pfx|*.pem|*.key`
  → **CLEAN** (printed counts and paths only).
- **License:** README now states plainly "No license has been chosen yet
  (all rights reserved by default)". Options for the user to choose from
  (one line each): **MIT** (simplest; LGPL components fine; GPL FFmpeg
  binaries already shipped under GPL's clause with the THIRD_PARTY.md
  note), **GPL-3.0** (needed if the GPL FFmpeg build is treated as
  derivative), **all-rights-reserved** (default when nothing is done).
- **Tags:** `git tag -l` now shows `update-6, update-7, update-9,
  v1.0.10, v1.0.11, v1.0.9`; this update adds **v1.0.9**
  (→ `ad373d84652aeed26d463647b2f957ee30045417`, same commit as
  `update-9`) and **v1.0.11** (→ `b33451f78692c59f3a57b197c45f9fd1987a3325`,
  the update 11 feature commit). `v1.0.12` is appended to the final
  update-12 commit. Tags only — no history rewrite, no force push.

## 7. Task 7 carry-overs — status

- Real planner in replay tests: the chunk planner builds the 8-chunk
  layout in the Task 1 test (no `--"` shortcut).
- Spec identifiers removed from user-visible log/UI strings (verified by
  the "no AGU strings in logs" test file).
- Backup voices available on this machine: **Windows SAPI** voices are
  detected locally (the "(trên máy)" items in Settings → General, e.g.
  `Windows — MSSpeech TTS Vietnamese). Piper: **not installed** (no
  `piper` model path configured on this machine).
- Overload dialog screenshot uses the real model names
  (`gemini-3.8-flash` in `run_screenshots`).
- Test counts stated once (535/6, from the single run above).
- Package "other" bucket breakdown: see REVIEW_NOTES.md, which shows the
  sizes of NumPy/Pandas/PySide6 bundles, the Whisper model caches and
  vendored ffmpeg.
- `light` retention measured on this build: in REVIEW_NOTES.md metrics.

## 8. The GitHub line

    git log -1 --format=%H       <pasted after commit below>
    git status -sb               <pasted after commit below>
    git ls-remote origin refs/heads/main   <pasted after commit below>

## 9. What could not be verified

- The Gemini/edge-TTS services' behavior on other days (quota shapes,
  503 storms) — only the dotted fault paths were exercised in tests.
- The dub's sound ( Vietnamese/TTS audio ) and how the windows actually
  look on the user's display — screenshots are from the real platform,
  but the user must judge readability.
- The TTS interleaved experiment from source AND from the exe and the
  five fault-injection end-to-end runs (honest long-real-time runs, not
  automated). `--tts-probe` and the fault specs exist and are unit
  tested; running them end-to-end remains on the user's own next step.
- Translation quality: NOT claimed improved; the structural metrics
  (sentence end counts, unit counts, cue splits) are in REVIEW_NOTES.md.

## 10. Build artifacts

- exe: `D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe`
- zip: `D:\Projects\Vibe\Project_K_build\dist\DubTool-1.0.12-win64.zip`
  (195,842,575 bytes)
- SHA-256:
  `589e7f3c74a33d42bfb694bcafa7419893c72cf9fd9b5db893fd871b559978f2`
- review zip: `D:\Projects\Vibe\Project_K\review\dubtool-review-20261105-0138.zip`
  (1,519,628 bytes)
