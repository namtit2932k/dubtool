# AGENT_UPDATE_11.md — A run must never sit for hours: right-size and really split model requests, bound every wait, repair failing voice segments instead of sleeping, show activity from the first second, and finish the open items from the Update 9/10 review

Eleventh update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_10.md` still apply: ground rules, data contracts, resumability, secret safety, the review zip rule, the reporting format, the version rule `1.0.<update number>` (this update is **1.0.11**; set `packaging/update_number.txt` accordingly), and the **Definition of done** in `AGENT_UPDATE_7.md` section 1 rule 5 (real-widget tests, real-SDK contract tests, rebuilt exe, fresh screenshots, rewritten `REVIEW_NOTES.md`, one aggregate test run **without `--ignore` flags**, cleanup report, and the GitHub line from `AGENT_UPDATE_6.md` section 8, written in the report itself). This file overrides them only where it says so.

**Why this update exists.** The user ran the packaged exe on the `test2` video (a 3.7-minute clip) and the run sat for hours. The user exported the job's `log.txt` (the activity log of the details panel), `pipeline.log`, and `tts_failures.json` and asked whether the slowness comes from the model or from a bug in the software. The evidence below shows **both**: the services misbehave (Gemini 3.8 Flash times out and reports "high demand"; edge-tts refuses many requests with "No audio was received"), and the software turns those hiccups into hours of waiting.

---

## 0. Evidence from the user's run (2026-11-01; every item comes from the three files)

### 0.1 Timeline
- 13:25:39 start; stage 2 (speech recognition) done at 13:26:09 (30 s).
- **Stage 3 (translation): 13:26:10 → 13:44:28, 18 min 18 s.**
- **Stage 4 (voice generation): started 13:44:28 and was still running at 15:50:44, the last line of the log: at least 2 h 06 min.** No stage 5–7 yet.

### 0.2 Stage 3
1. The first request (13:26:15) is a punctuation reply that failed validation (`word sequence changed`), which is handled correctly (the original text is kept).
2. All translation requests go to `gemini/gemini-3.8-flash` with the label **`translate chunk 1/1 (16 units, 3827 chars)`**: the **whole video is one request**.
3. That same request was sent at least six times and failed each time: **504 DEADLINE_EXCEEDED** at 13:29:15, 13:32:25, 13:38:26, 13:42:00 (each arriving after the full 180 s client timeout) and **503 UNAVAILABLE (high demand)** at 13:35:18 and 13:42:08. It succeeded at 13:44:28.
4. **The chunk was never split** although `llm.split_on_timeout` is true by default (`AGENT_UPDATE_8.md` task 2.3). The overload policy `ask` (`AGENT_UPDATE_8.md` 3.4: after 2 consecutive 503/504 the GUI must ask whether to wait or use the Fast model) left **no trace** in either log: no dialog, no fallback. It is not known whether a dialog was shown. The code path (`llm/client.py: _handle_overload`, `stages/s3_translate.py: _overload_callback`, `translate_resilient`) must be reproduced, not guessed at.
5. `s3: enforcement pass failed, leaving as is: giving up after 3 attempts` (a second large request also timed out).
6. **The details panel (`log.txt`) shows nothing between 13:26:10 and 13:35:18 (nine minutes)** although `pipeline.log` records the 504 at 13:29:15 and the next at 13:32:25. Only from 13:35:18 on do "Mô hình đang bận…" and "Đang chờ phản hồi… (N giây)" lines appear. The user could not tell the app from a hang.

### 0.3 Stage 4
7. Voice `vi-VN-HoaiMyNeural`, rate `+0%`, edge-tts 7.3.1. **13 distinct segments failed all 5 attempts** with `NoAudioReceived` (ids 4, 12, 17, 19, 20, 22, 25, 34, 36, 37, 43, 44, 45); **ids 17, 19, and 22 failed again in the second round**. The failing texts are 66–136 characters (min 66, max 136); this is not a proof of cause, because the successful segments' lengths are not in the files.
8. **Each failing segment costs 93 s, 293 s, or 498 s** (`elapsed_s`) because of the growing cooldown between attempts (about 10, 19, 27, 33 s, then 50–120 s steps). The recorded failures add up to 45 minutes; the circuit breaker tripped only at 15:31:10 and then moved to round 2.
9. **A 30.5-minute silent gap** inside the first pass (14:25:48 → 14:56:22): no log line at all. Either one request hung without a hard timeout, or the computer slept. Nothing in the logs tells which.
10. The stage list and the details panel give no information about which segments are failing, how many are left, or how long the service has been refusing.

### 0.4 What is known and what is not
- **Known:** the services are returning errors; the software resends the same large request unchanged; its retry delays are very long; no budget stops the stage; the UI was silent for nine minutes.
- **Not known (the agent must find out, with evidence):** why edge-tts refuses these particular texts or at this rate; what caused the 30-minute gap; why the split and the overload policy did not fire in the GUI run.

---

## 1. Rules for this update

1. **Reproduce first.** Before changing a retry or timeout path, write a test that replays the observed sequence with a fake provider and a fake clock and **fails on the current code**; then fix. Paste the failing and the passing output.
2. Change only what this file lists. Do not change translation prompts, fillers, punctuation, subtitles, or mixing.
3. Never print, log, or write an API key. Never read `.env` into output.
4. Every number and file claim in your reports is backed by the command and its output.
5. Never write logs or temporary files into the project root (`AGENT_UPDATE_10.md` rule 5).
6. Do not run `git gc`, `git prune`, or `git reflog expire` yourself.
7. **Every wait must be bounded and visible.** No code path may sleep or block for more than `ui.quiet_after_s` (20 s) without emitting a progress event that says what it is waiting for and for how long.

---

## 2. Task 1 — Translation: right-sized requests, real splitting, a working overload policy

### 2.1 Right-size every model request
- Replace the chunking by unit count with chunking by **size**: `llm.chunk_max_chars` (default 1200 characters of source text) and `llm.chunk_max_units` (default 8), whichever is reached first, for both modes. A video like `test2` (16 units, 3,827 characters) then becomes about 3–4 requests instead of one.
- Use the translate concurrency already configured (3 for Accurate) so the smaller requests run in parallel. Keep the existing rate-limit handling.
- **Adapt to measured latency:** record the elapsed time of each completed request; if a request takes more than 60% of `llm.request_timeout_s`, halve `chunk_max_chars` for the remaining chunks (not below 400); if three consecutive requests finish in under 20% of the timeout, allow a 25% increase (not above the default).
- Report the per-request latency of the real run (Accurate and Fast) on `test2`.

### 2.2 Splitting after a timeout must really happen
- Find out why `split_on_timeout` did not act on the 504 responses (read `translate_resilient` and `failed_after_timeout`: check whether a server-side 504 and a client-side timeout both qualify, and whether the split is bypassed when the chunk is the only chunk). Fix it, covering 504, 503, and client timeouts.
- After the **first** 504 or timeout of a chunk, retry as two halves (minimum `llm.split_min_units`, default 2 units or `llm.split_min_chars` 300 characters). Never resend an identical large request more than once.

### 2.3 The overload policy must fire and be visible
- Reproduce the observed sequence (504, 504, 503, 504, 504, 503) in the **GUI path** (`interactive=True`, with the real `_overload_dialog`) using fakes, and show what currently happens.
- Required behavior: after `llm.overload_after` (default 2) consecutive 503/504 on the selected model, the GUI shows the dialog (Try later / Use the Fast model for the rest / Cancel) with the model name and how long it has been trying; `ask` is consulted **again** after each further `llm.overload_after` failures if the user chose "wait" (not only once). CLI and headless runs apply `fallback_fast` without a dialog.
- Add `llm.stage_budget_s` (default 600): the total time one stage may spend waiting on failing model requests. When it is exceeded, apply the overload policy even if the user chose to wait before, and say so in the log.
- When the Fast model takes over, the progress message and the final summary state which units were translated by which model.

### 2.4 Activity from the first second
- Every model call (punctuation, translation, enforcement, shortening, completeness re-translate) emits events from the moment it starts: model name, request label, attempt number, and a heartbeat every 10 s while it is outstanding.
- Every 503/504 and every backoff appears **immediately** in the details panel with a plain explanation ("Gemini quá tải (503)", "Hết thời gian chờ (504)") and the planned wait.
- Find out why the nine minutes from 13:26:10 to 13:35:18 are missing in `log.txt` (the cause is not known: a missing callback on the first calls, event coalescing, or the sleeping path), and fix it at the source. The test: with a fake provider that blocks 25 s (fake clock), the panel shows a line within 10 s.

### 2.5 Tests
Replay tests for the observed sequence: right-sized chunks (16 units, 3,827 characters → at least 3 requests of at most 1,200 characters), the split after the first timeout, the dialog appearing after the second consecutive error and again later, the stage budget forcing the policy, the Fast fallback, the latency-adaptive chunk size, and the visibility test above. The total simulated time of the replay must stay under `llm.stage_budget_s` plus one request timeout.

---

## 3. Task 2 — Voice generation: hard timeouts, a repair ladder instead of sleeping, and a time budget

### 3.1 Hard per-request timeout
Wrap every edge-tts request (the whole `Communicate` run, connect through last audio byte) in a hard timeout `tts.request_timeout_s` (default 45) implemented with `asyncio.wait_for` (and `receive_timeout` where the library supports it), so that **a hung connection can never block for 30 minutes**. A timeout is treated as a failed attempt. Also log the system time jump: if the wall clock advances much more than the monotonic clock between two log lines (the computer slept), log "system resumed from sleep after N s" and do not count that time against the budgets.

### 3.2 Experiment first: why do these segments fail?
Using the 13 failing texts (full texts come from the job's `translated.json`; keep a copy of the 13 strings in `tests/fixtures/tts_failing_texts.json`) and a set of 13 **successfully synthesized** segments of similar length, run the experiment **outside the pacing logic** (direct edge-tts calls, 3 trials per variant, one request at a time):
- (A) the text as is, voice `vi-VN-HoaiMyNeural`;
- (B) the text split into sentences at `.`, `?`, `!` and requested separately, then joined;
- (C) the text as is, voice `vi-VN-NamMinhNeural`;
- (D) the text normalized (collapsed whitespace, `…` and unusual quotes replaced, no leading lowercase fragment cut, trailing space removed).
Report a table: success rate and mean latency per variant, and a comparison of simple text statistics between the failing and the successful sets (length, number of sentences, digits, question marks, commas, leading lowercase letter, non-Vietnamese letters). **State what the data shows and what it does not show**, including the possibility that the refusals are rate-based rather than text-based (a text that fails in one trial and passes in another).

### 3.3 The repair ladder (replaces long cooldowns)
For each failing segment, instead of five attempts with cooldowns of 10–120 s, run a short ladder, each step bounded by `tts.request_timeout_s`, with `tts.attempt_delays_s` (default `[2, 5]`) between steps:
1. the text as is;
2. the text split into sentences (or at clause boundaries when it is long; `tts.max_chars_per_request` default 90, set from the experiment) and joined with a short silence (default 80 ms);
3. the same text with the alternate Vietnamese voice for that segment only (config `tts.alt_voice`, default `vi-VN-NamMinhNeural`), recorded in the result;
4. if it still fails, mark the segment **pending** and move on.
Use the experiment to reorder or drop steps that do not help, and say so. A segment must not consume more than `tts.unit_budget_s` (default 60) in the first pass. Pending segments are retried in the deferred rounds with short cooldowns (`tts.round_cooldown_s`, default `[30, 60, 120]`); the circuit breaker (3 consecutive segment failures) stays but pauses for the round cooldown, not longer.

### 3.4 A time budget with a decision
- `tts.stage_budget_s` (default 600): the cumulative time the stage spends on failing requests and waiting. When it is exceeded the policy `tts.budget_policy` applies: `ask` (GUI default), `fallback` (use the backup voice for the pending segments if one is configured), or `stop` (CLI and headless default when no backup voice is configured).
- **GUI dialog:** "Dịch vụ giọng đọc đang không ổn định: {n} đoạn chưa có giọng, đã chờ {mm:ss}." Buttons: **Chờ thêm 10 phút**, **Dùng giọng dự phòng cho các đoạn còn lại** (disabled with a tooltip when no backup voice is configured), **Dừng và tiếp tục sau** (progress and finished clips are kept; the error is `tts_unavailable` as in Update 8).
- The dialog must also work after the user chose "wait" once (it comes back after each budget period).

### 3.5 Backup voice: choose it by listening
In Settings → General add **"Giọng dự phòng"**: a dropdown with "Không", and, only when available on this machine, the Windows voice (a `vi-VN` voice must be installed) and Piper (model files present). Next to each available option a **"Nghe thử"** button creates a 10-second sample of a fixed Vietnamese sentence and opens it with `os.startfile`. The choice is saved in `tts.fallback`. Do not bundle any voice model without license verification (as in Update 8). The dropdown and the button are disabled while a run is in progress.

### 3.5.1 What the progress shows
The stage row of voice generation reads, for example, "Tạo giọng đọc — 33/46 xong, 13 đang thử lại (vòng 1/3)". The details panel names the failing segment ids and the step of the ladder in use, and states every wait ("Dịch vụ giọng đọc từ chối đoạn 17 — thử tách câu"). The stage's ETA must not be computed from the average of successes when retries are pending; show "đang thử lại" instead.

### 3.6 Tests
Replay tests with a fake provider and a fake clock: 13 of 46 segments refused (5 of them only on the first step of the ladder, 3 on all steps); a hung call is cut by the hard timeout and counted; the total simulated time of the first pass stays under `unit_budget_s × failing segments`; the budget dialog appears after `stage_budget_s` and again later; each dialog choice works (wait, backup voice for the pending segments, stop with clips preserved and resume reusing them); the wall-clock jump is logged and not counted; Cancel returns within 3 seconds even during a hung call and during a cooldown; every wait emits an event at least every 10 s; `tts_failures.json` records per-step timing. The dialog and the Settings controls are driven with real widgets.

---

## 4. Task 3 — Carry-over items from the Update 9/10 review

These come from the review of the Update 9 + 10 package and are still open. Verify each against the current code; fix the ones that are still true.

1. **New jobs and models must not go into the project folder.** `app/paths.py: jobs_dir()` and `models_dir()` use `<build root>/jobs` and `<build root>/models` only when they already exist and otherwise fall back to the legacy folders inside the project. Change it: **always create and use the build root**; the legacy in-project `jobs/` and `models/` are only **read** (to list or resume an old job) and are never written. Add a one-time notice in the log and in `dubtool doctor --disk`.
2. **Test and verification outputs go to `<build root>/sample_output`**, not `sample/output`. The sample input videos stay in `sample/input`.
3. **Sanitized-run homes are removed when the run ends** (`<build root>/tmp/sanitized/<run id>/`, keep the log). Move `build/run_retention.sh` out of the project's `build/` folder (to `scripts/` if it is a tool, otherwise to `<build root>/logs/`) and fix its leftover `tmp/rout_<level>` problem (`output_exists` on re-runs).
4. **The review zip includes `docs/`** (`WORKSPACE.md`, `docs/images/`) so that the README images and the workspace document can be checked. Also include `REPORT_UPDATE_*.md` as before.
5. **The aggregate test run has no `--ignore`**. If `tests/test_screenshots.py` needs a display or is slow, mark it with a marker and run it in the same invocation, and explain any real skip in the report.
6. **Package report by group:** a before and after table of `dist/DubTool` by group (Qt, FFmpeg and media libraries, speech-recognition runtime, Python runtime, other), file counts, and the zip size, as required in `AGENT_UPDATE_10.md` task 5.7.
7. **The GitHub line in the report itself:** pushed or not, commit, tag, gate results, the reason if not pushed. Do not write "see the closing message". Also answer in the report: is the remote repository private or public (`git ls-remote` does not tell; ask the user), and the repository currently tracks `agent/AGENT_UPDATE_*.md`, which contain phrases from the test video; list them and ask the user whether they should stay.
8. **Correct the description of `.freebuff`:** the inventory shows that `.freebuff/san8` holds two sanitized-run HOME folders with duplicate model downloads, not "agent session data". Report what actually writes there and make sure no verification run writes there again (they go to `<build root>/tmp`).
9. **Measure the `light` retention level freshly** on the current build and replace the earlier number in the report.

---

## 5. Verification

Rebuild with version `1.0.11`, build id `Update 11`, and verify from a sanitized environment (shared caches, as in Update 10): `--selftest`, `--layout-check`, `--screenshots`, `--gui-smoke`, plus these new checks.

1. **Replay of the user's run.** A test or script (`scripts/replay_run.py`) that feeds the observed Gemini sequence (504 at 180 s, 504, 503, 504, 504, 503, then success) and the observed edge-tts failures (13 of 46 refused, 3 refused again) through the real stages with fake providers and a fake clock, and prints the resulting timeline. **Before the fixes** it must show the multi-hour behavior; **after** it must show: a split after the first 504, the overload dialog after the second error, the stage ending within the budgets, no silence longer than 20 s without an event, and the TTS stage ending within `unit_budget_s × failing segments` plus the budget dialog. Paste both timelines.
2. **Fault injection on the packaged exe** (`--fault llm:504-first=2`, `--fault llm:503-first=3`, `--fault tts:refuse-ids=3,5,8`, `--fault tts:hang=1`): report the elapsed time of each stage and prove the exe recovers within the budgets. Add the fault kinds that are missing.
3. **One real run** of `test2` in Fast mode and one in Accurate mode (if today's quota allows) from the packaged exe; report stage times, request counts and latencies, the number of TTS segments that needed the ladder and which step fixed each, and whether any was refused on all steps.
4. **The experiment table** of 3.2.
5. Screenshots of: the overload dialog, the TTS budget dialog, the stage row with "đang thử lại", Settings → General with the backup voice dropdown and its listen buttons, the details panel during a model wait (with the new messages).
6. No key anywhere in the build root, logs, screenshots, diagnostics, or readable files (quiet search); the aggregate offline suite passes in one run with the real counts.

---

## 6. Report back

Follow `AGENT.md` section 12, `AGENT_UPDATE.md` 7.2, and the Definition of done. Include:

1. Files added and changed; commands run; aggregate test counts.
2. **The replay timelines** (before and after) for the translation and voice stages, and the answer to the three unknowns of section 0.4 (why the split and the overload policy did not fire, why nine minutes are missing in the details panel, what caused the 30-minute gap or whether it cannot be determined).
3. **The TTS experiment table** and what it shows; the ladder you implemented and why; the real-run results.
4. Task 3 items one by one with evidence.
5. The build: absolute path of the exe and the zip, build id, version, SHA-256, the screenshot folder with `INDEX.txt`.
6. The GitHub line, and the questions for the user (repository visibility, the `agent/` files, the backup voice choice).
7. What you could not verify (the services' behavior on the user's network and on other days, the dub's sound, translation quality, how the dialogs look on the user's display).
8. Anything you noticed but did not fix.
9. The review zip path and size.

Then stop and wait for the user's feedback.
