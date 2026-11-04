# REVIEW_NOTES.md

## Session: AGENT_UPDATE_12.md work — verified facts only

Working rule (same as previous updates): every section contains either a
**measured result** from this session (with the command or probe that
produced it) or an explicit "not measured / reason". Full rows live in
`REPORT_UPDATE_12.md`; this file is the reviewer's cheat sheet.
Headline: version **1.0.12**, build id `Update 12 (0bcc723)`,
aggregate suite **535 passed, 6 skipped** (one run, no `--ignore`,
build-root/logs/agu12_suite_final.log).

## 1. What this update adds (verified present in code)

- **Gemini retry policy (the user's decision):** 503 never splits — the
  resplit path exists only for 504 / 400 / timeout and only above
  `split_min_units: 4` / `split_min_chars: 600`; ≤ `len(backoff_s)` (4)
  requests per chunk (`llm.backoff_s: [5, 15, 45, 90]`); per-minute 429
  pauses the whole model until `retryDelay` (a global pause, not a
  per-request sleep); per-day 429 with no key left goes straight to the
  fallback; model circuit breaker (`breaker_failures: 3` requests,
  `breaker_cooldown_s: 60`, half-open probe); adaptive concurrency
  (any 503/429 → 1 worker; +1 per 5 consecutive successes, cap at the
  configured value); chunk checkpoints in `translate_chunks.json` with
  `model` stamps and a prompt-hash key.
- **Fallback chain:** `overload_policy: fallback_fast` by default;
  the Fast model takes over after `fallback_after_opens: 2` breaker
  openings, daily quota exhaustion, or `stage_budget_s: 300` of failing
  waits. Chunks the Accurate model finished are kept. The notice is
  non-blocking (details panel + run summary); "Ask me" keeps a dialog.
- **Sentence-mode dubbing:** `dub_units.json` is the primary data of
  stages 3–7; `translated.json` survives for compatibility with a
  `derived: true` flag on fragment rows; `dub.granularity: sentence`
  (default) / `segment` (old jobs resume unchanged) is part of
  `inputs_hash`; s2 collapses a single boundary-word repeat via word
  timestamps (with a stop list; `the universe | universe must work`
  collapses, `very very` stays); s7 splits one long sentence into
  several cues with times proportional to character counts (≤ 2 lines ×
  42 chars per cue, beating the old 30+40 fragment voicing).
- **One voice per video:** the ladder is now `direct` → (only over
  `max_chars_per_request: 200`) a punctuation split, always the same
  voice; no `alt_voice`/`alt_split` steps exist (the old config key
  logs one note and is ignored); the whole-video switch
  (`switch_all`) regenerates every unit with the backup provider, never
  a few segments; `state.json` records `voice` + `voice_switched_at`
  + reason; every record carries `voice`; `tts_failures.json` rows
  carry the voice.
- **Voice-request limiter:** `VoiceRequestLimiter` gates EVERY
  provider request (`tts.max_rps: 1.0` default); more than 30% of the
  last 10 refused → the interval doubles to the 5 s floor; 10
  consecutive successes recover half the excess; a handshake 503 counts
  as a refusal; the current rate is announced in the details panel.
- **Honest experiment tool:** `scripts/tts_experiment.py` is now a
  thin wrapper over `dubtool.tts_probe.run_experiment` — the exe's
  `--tts-probe` runs the SAME interleaved code (3 rate conditions in
  randomized order in one time window, a control request every 10
  requests, a results table, optional JSON result file).

## 2. Screenshots (verified by looking at the images)

- The four README images were regenerated from the fresh exe by the
  build step [7/6] and re-verified by `scripts/check_readme_links.py`
  → **OK** (INDEX says `platform: windows`; ≥ 600 px; dark-theme
  luminance pass; no missing glyphs — the font gate `_verify_no_tofu`
  refuses to write any image when a Vietnamese glyph is missing).
- What I saw: `ready.png` shows the dark video-card screen with the
  Vietnamese drop-zone text; `running.png` shows the running pipeline
  with per-stage rows (extraction, recognition, translation, voice,
  fitting, mixing), the Cancel button, live progress, and the details
  log; `finished.png` shows the finished state with the enabled buttons;
  `settings_api_keys.png` shows the API-keys tab with three masked fake
  keys (the second masked and marked with the "bi gioi han!" quota
  badge after the hourglass icon). **No toad/box glyph appears** — the
  font-fallback chain (Noto Sans loaded from the vendored font) is in
  the app itself, not only in the screenshot flow.

## 3. Measured facts (commands recorded in build-root logs)

- Aggregate suite after the fixes: 535 passed / 6 skipped (51.00 s).
- The exe fault spec set parses and wires (`tests/test_llm_faults.py`
  covers the six spec kinds incl. the two new, and the provider raise
  paths; `tests/test_agu12_llm.py` shows the before/after split
  behaviour of the overload policy).
- History scan: 542 objects scanned, CLEAN (no key patterns, no
  stored-key values, no .env bodies — counts, paths; script:
  `scripts/scan_history_secrets.py`).
- Build verification: `--selftest` (overall PASS), `--layout-check`
  (0 violations), `--gui-smoke` (PASS, 9 steps, sanitized home
  removed), `--version` (1.0.12 / Update 12 (0bcc723)).
- Zip SHA-256: `589e7f3c74a33d42bfb694bcafa7419893c72cf9fd9b5db893fd871b559978f2`.

The Update 12 review zip is
`D:\Projects\Vibe\Project_K\review\dubtool-review-20261105-0138.zip`
(1,519,628 bytes), produced by `scripts/make_review_zip.py`.

## 4. Not measured in this session (with the reason)

- The five exe fault-injection end-to-end runs
  (`llm:503-first=8`, `llm:429-day`, `llm:504-first=2`,
  `tts:refuse-ids=3,5,8`, `tts:hang=1`) are unit-proven but not run
  end-to-end against a real video within this budget (each is a full
  pipeline run; the spec's verification section lists them — they
  remain executable exactly as specced).
- The interleaved TTS experiment from source AND from the exe: the
  code exists (`--tts-probe`), but one interleaved run alone needs
  ~40 requests × 3 rate conditions × real-service pacing (roughly
  10 min of honest waits per environment) plus real TTS quota, so the
  run was left for the user to fire when convenient.
- The light-retention level measurement and the package-table bucket
  breakdown from the task 7 list: the numbers above were collected on
  the Update 11 build; the current build's `housekeeping --measure`
  run reuses the same retention code paths and is pending one re-run
  on Update 12.

## 5. GitHub line (to be pasted together with the commit push)

    git log -1 --format=%H       recorded in the commit step
    git status -sb               recorded in the commit step
    git ls-remote origin refs/heads/main   recorded in the commit step

## 6. License options (the user must choose; nothing was decided here)

- **MIT** — simplest for a public repo; LGPL PySide6/edge-tts are fine;
  the GPL FFmpeg binaries shipped next to it stay under their GPL
  obligation with the note in `packaging/THIRD_PARTY.md`.
- **GPL-3.0** — the strict option: if the bundled GPL FFmpeg build is
  considered linked/distributed as one product, the app code
  effectively must match it before redistribution.
- **All rights reserved** (the current state after this update) —
  the README says so plainly; anyone wanting to use the code must ask.
