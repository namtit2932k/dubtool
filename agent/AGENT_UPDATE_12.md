# AGENT_UPDATE_12.md — Stop making overload worse (retry policy), dub whole sentences instead of fragments, one voice per video, control the voice request rate, trustworthy README screenshots, and public-repository hygiene

Twelfth update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_11.md` still apply: ground rules, data contracts, resumability, secret safety, the review zip rule, the reporting format, the version rule `1.0.<update number>` (this update is **1.0.12**; set `packaging/update_number.txt` accordingly), and the **Definition of done** in `AGENT_UPDATE_7.md` section 1 rule 5 (real-widget tests, real-SDK contract tests, rebuilt exe, fresh screenshots, rewritten `REVIEW_NOTES.md`, one aggregate test run without `--ignore`, cleanup report, and the GitHub line written in the report itself). This file overrides them only where it says so.

**Where this update comes from.** After Update 11 the user ran a demo on a 5.8-minute video about dark energy (79 segments, 350 s) and sent four files: `pipeline_1.log` (translation), `pipeline2.log` (voice generation and fitting), `tts_failures_1.json`, and `translated.json`. The user then wrote three analyses of their own (a retry-policy critique, a translation-quality diagnosis with a proposed three-step design, and a voice-consistency proposal), reported that the README screenshots are broken, and answered the open questions about the repository. Everything below was checked against those files and against the Update 11 review.

The user's decisions and answers (do not ask again):
- The repository is **public**, and the `agent/` folder **may stay** in it.
- The user asked the assistant to decide the Gemini overload strategy. The decision is in task 1.
- The user wants **one voice per video**: if voice generation fails, switch the whole video, never a few segments (task 3).
- The user asked that the README screenshots show the real interface (task 5).

**Evidence files.** The user will place the four files in `sample/evidence/` (the folder is ignored by Git). Use them to build the replay fixtures. If they are missing, say so and use the facts in section 0, which are enough to build the replays.

---

## 0. Evidence

### 0.1 Translation (`pipeline_1.log`, 2026-11-04 11:27:23–11:33:29)
1. All requests went to `gemini/gemini-3.8-flash`. The video was already cut into **8 chunks of 5–8 units and 373–939 characters** (right-sized by Update 11), yet **every chunk's first request returned `503 UNAVAILABLE (high demand)`**: 59 lines with 503 in 66 log lines, 1 with 504, 2 with 429.
2. **The splitting rule made it worse.** The log says `translate chunk N … failed (503) — splitting 8 payloads in two after the first error` 17 times, and splits recursively up to four levels (`[part 1/2] [part 1/2] [part 1/2] [part 1/2]`). A 503 says the service is overloaded; it says nothing about the request being too large, so splitting only multiplied the number of requests (38 logged `gemini-3.8-flash` calls in about six minutes with 3 chunks in parallel). Chunks 2, 4, and 5 ended with `giving up after 3 attempts`.
3. **Quota followed.** At 11:31:11 key #1 hit a per-day quota for `gemini-3.8-flash` (429), the pool switched to key #2, key #2 was rate limited at 11:33:01 (`cooling down 60 s`), and the stage reported `rate limit was hit on every key`.
4. The log messages contain internal specification references (`AGU11 2.2: never resend the big request`). They must not appear in user-visible or diagnostic logs.
5. `translated.json` has no field that says which model translated each unit.

### 0.2 Voice generation (`pipeline2.log`, `tts_failures_1.json`, 11:35:08–12:24:48)
6. Voice generation plus fitting took **49 min 40 s** for a 350 s video.
7. **48 of 79 segments (61%) failed at least once.** `tts_failures_1.json` has 115 records: by step `direct` 55, `split` 35, `alt_voice` 16, `alt_split` 8; by kind `NoAudioReceived` 105, **`WSServerHandshakeError` 8 (HTTP `503 Invalid response status` from `speech.platform.bing.com`)**, timeouts 2. The Microsoft endpoint itself returned 503 for some handshakes, so part of the failures are service-side overload, like Gemini's.
8. **24 failure records used `vi-VN-NamMinhNeural`** (the alternate voice), so the finished video contains segments in two different voices (the user's complaint). `translated.json` has no `voice` field, so the result cannot be audited.
9. The circuit breaker paused once (12:07:48). In stage 5, segment 7's TTS failed after shortening and fell through to overflow.

### 0.3 Translation quality (`translated.json`)
10. 20 of the 79 English segments do not end a sentence, so about a quarter of sentences are spread over two or more segments, and the Vietnamese for each fragment is translated and **voiced separately**. Examples (EN | VI):
    - `Dark energy is a strange phenomenon | that scientists believe permeates the universe.` → `Năng lượng tối là hiện tượng lạ | khoa học tin rằng thấm đẫm khắp vũ trụ.` The subject "các nhà" is missing: the model compressed the sentence to fit the 30 + 40 character budgets.
    - `First of all, the universe is expanding, | and not only that, the rate of its expansion is accelerating.` → `… | chỉ vậy, tốc độ giãn nở của nó còn đang tăng nhanh.` "and not only that" became "chỉ vậy" ("only that"): **the meaning is reversed**.
    - `Heat Death or a Big Freeze | In a nutshell, … in a heat death scenario,` → `… | Khác biệt là trong kịch bản cái chết` and the next fragment starts with `nhiệt, vật chất…`: **the term "chết nhiệt" is cut in two** by the redistribution.
    - `Until 1998, we thought that the universe | universe must work…` (the recognizer repeated "universe" at the boundary) → `Trước 1998, ta tưởng vũ trụ phải | hoạt động…` The repeated word survived, "ta" was used although `AGENT_UPDATE_8.md` task 5.1 forbids it, and the cut falls inside the verb phrase "phải hoạt động".
11. The Vietnamese length tracks the budget tightly: `len(vi)/max_chars` has a median of 0.95 and 63% of segments are at or under 1.0.
12. **The user's proposed three-step design (group by sentence, translate with context, redistribute the Vietnamese over the original segments) is what the code already does** (`build_units`, unit-level translation, `split_translation`). The failures come from step 3 and from the budget: redistribution creates fragments that do not stand alone (and can cut terms), each fragment is then voiced separately, and the per-fragment budget pushes the model to drop words. The fix is to stop voicing fragments (task 2).

### 0.4 README screenshots
13. The images in `docs/images/` have a **white background and square boxes (tofu) instead of Vietnamese letters**. The Update 11 notes say the screenshots were made on the `offscreen` Qt platform (no system theme, no system fonts), unlike the real interface, which is dark and uses Segoe UI.

### 0.5 Carry-overs from the Update 11 review (all verified there)
14. The replay "AFTER" starts with a single 16-unit request, so it does not exercise the chunk planner. The TTS experiment table is internally inconsistent (variant C 27/42 while variant D, which also uses the alternate voice, 42/42 at the same speed as A) and was run from source Python, not from the exe. The GitHub line was hedged for the third time and tags are inconsistent (earlier reports mention `v1.0.9`/`v1.0.10`; the Update 11 report says "no tags"). The report does not say which backup voices exist on this machine. The report quotes 507 passed in one place and 508 in another; the package table has an "other" bucket of 312 files (188.9 MB) with no breakdown; the overload dialog screenshot uses the made-up model name `gemini-2.5-flash`.

---

## 1. Rules for this update

1. **Reproduce first** (Update 11 rule 1): write the replay test from the evidence, show that it fails on the current code, then fix.
2. Every number and file claim in your reports is backed by the command and its output.
3. Never print, log, or write an API key. Never read `.env` into output.
4. Do not claim that translations improved. Report structural metrics and show text side by side; the user judges Vietnamese quality.
5. Log and diagnostic messages must not mention specification identifiers (`AGU11`, `AGENT_UPDATE_*`, task numbers).
6. No path may write logs or temporary files into the project root (`AGENT_UPDATE_10.md` rule 5).
7. Do not run `git gc`, `git prune`, or `git reflog expire`, and never rewrite history or push with `--force`.

---

## 2. Task 1 — Gemini retry policy (the decision)

The user's critique is correct: a 503 means the model is overloaded, not that the request is too large; splitting after it multiplies requests and burns quota. The policy below replaces the one from Update 8 task 2 and Update 11 task 1. Apply it to the translation stage and to every other model call (punctuation, enforcement, shortening, completeness re-translation).

### 2.1 Error taxonomy and actions

| Signal | Meaning | Action |
|---|---|---|
| `503 UNAVAILABLE` (high demand), `500`, `502` | capacity problem, model-wide | **Never split. Never raise the request count.** Retry the same request with bounded exponential backoff and jitter (`llm.backoff_s: [5, 15, 45, 90]`, at most 4 attempts); count the failure against the **model circuit breaker** (2.2). |
| `504 DEADLINE_EXCEEDED` or a client timeout | latency | Split **once**, into two halves, and only if the chunk is larger than `llm.split_min_chars` (default 600 characters) or `llm.split_min_units` (default 4 units). Never split a chunk below that size and never split a half again. Count toward the breaker. |
| `400` for size (token or payload limit, a reply cut by `MAX_TOKENS`) | request too large | Split once, into two halves, with the same minimum sizes. |
| `429` per-minute | rate | Read `retryDelay` (and the quota id) from the error details; **pause every request to that model** until that time (a global pause, not a per-request sleep). Do not rotate to another key for a per-minute limit unless the key is known to belong to another project. |
| `429` per-day | quota | Mark `(key, model)` unavailable until the reset (existing `quota_state`). When **no key is left for the selected model**, go straight to the fallback (2.4); do not run a 3-attempt loop and do not wait hours. |
| invalid key (401/403) | key | existing behavior (skip the key). |
| validation error in a reply that arrived | content | the existing validation retry, at most 2 attempts. |

### 2.2 Circuit breaker, concurrency, and rate
- **Model circuit breaker** (per model): after `llm.breaker_failures` (default 3) consecutive failed **requests** (not attempts) with 503/504/timeout, the breaker opens for `llm.breaker_cooldown_s` (default 60): no new request goes to that model during the cooldown. A half-open probe (one request) decides whether to close it. While it is open the log and the details panel say so.
- **Adaptive concurrency:** start at the configured value (Accurate 2, Fast 3; replace the current 3 for Accurate). After any 503/429, set the concurrency to 1; after 5 consecutive successes raise it by one, up to the configured value.
- **Rate limiter:** a token bucket per model, `llm.max_rpm` (default: unset). When a `429` carries a `retryDelay`, set the limiter so that the next requests respect it. Never send a burst larger than the concurrency.

### 2.3 Chunk checkpoints
After **each** chunk completes, write its result atomically to `translate_chunks.json` in the job folder: a record per chunk with the unit ids, a hash of the source text, the model, the hash of the prompt + glossary + pronoun settings, the Vietnamese results, and a timestamp. On resume (after an error, a cancel, or a closed app), reuse every finished chunk of this job and request only the missing ones. A failed chunk never discards the others. "Start over" clears the file.

### 2.4 Fallback chain (the decision)
- Default policy `llm.overload_policy: fallback_fast` for the GUI as well as the CLI. When the breaker has opened `llm.fallback_after_opens` (default 2) times, or the per-day quota of the selected model is gone for every key, or the stage has waited `llm.stage_budget_s` (default 300; it was 600) on failing requests, **the remaining chunks are translated with the Fast model**. Chunks already translated by the Accurate model are **kept**.
- Do not block the user with a modal dialog by default. Show a non-blocking notice in the details panel and in the run summary ("Model Chính xác quá tải: 5/16 câu được dịch bằng model Nhanh"). Keep the dialog available through the setting (below).
- Add Settings → General: **"Khi model Chính xác quá tải hoặc hết hạn mức"**: *Tự động chuyển sang model Nhanh* (default) / *Hỏi tôi* (the existing dialog) / *Chờ* (keep backing off). The dropdown is disabled while a run is in progress.
- Record `model` for every unit in the job data (translated units and the chunk store), and print the count per model in the summary.

### 2.5 Tests (replay from the evidence)
- Fake provider that reproduces the observed sequence: 8 chunks of 5–8 units (373–939 characters), first request of each chunk answers 503, a 504 for one small chunk, a per-day 429 for key #1 and a per-minute 429 for key #2. **Before the fix** the test must show the splitting growth (17 splits, recursion to four levels, more than 38 requests). **After** it must show: no split on any 503 or 429; no split of a chunk under the minimum size; requests per chunk at most `len(backoff_s)`; the breaker opening and the remaining chunks going to the Fast model; completed chunks kept; the stage ending within `llm.stage_budget_s` plus one request timeout; and the total request count at most `chunks × attempts + fallback chunks`.
- Resume after a cancel reuses finished chunks (no request for them); "Start over" clears them.
- 429 per-minute pauses all requests until `retryDelay`; per-day goes straight to the fallback when every key is exhausted.
- The per-unit `model` field and the summary counts; the Settings dropdown with real widgets (and disabled while busy).
- No log line contains `AGU` or `AGENT_UPDATE`.

---

## 3. Task 2 — Dub whole sentences, not fragments (the decision)

The user's three-step plan (group, translate with context, redistribute) already exists. Keep steps 1 and 2. **Replace step 3 for dubbing**: the translation of a sentence is no longer cut into fragments that are voiced separately. The unit of dubbing becomes the sentence unit from `build_units`. Redistribution over segments is kept only where it is needed for display.

### 3.1 The dub unit and its data
Add `dub_units.json` in the job folder (the primary data of stages 3–7 in sentence mode), one record per unit: `unit_id`, `seg_ids`, `start` (first segment start), `end` (last segment end), `text` (English), `vi` (the complete Vietnamese sentence), `max_chars` (sum of its segments), `model` (which model translated it), `voice` (which voice spoke it), `tts_duration`, `fit_status`, `reviewed`, and the cache keys. `translated.json` is still written, per segment, **for compatibility only**, with each segment's `vi` derived by the existing `split_translation` and a `derived: true` flag; no stage 4–7 code reads it in sentence mode.

Config `dub.granularity: sentence | segment`, default `sentence`. `segment` keeps the old behavior and is used to resume old jobs. The granularity is part of `inputs_hash`.

### 3.2 Translation prompt for sentence mode
- Translate each unit as **one complete, natural Vietnamese sentence** (or the same number of sentences as the source). Never produce fragments, and never end a unit mid-clause.
- The character budget is information, not an order: **do not drop words, subjects, negations, or constructions such as "not only … but also" to meet it**. Compression is allowed only for fillers and exact repetitions. Do not use the literary pronoun "ta" (the `translate.pronouns` setting decides).
- Protected phrases and glossary terms are translated as a whole (for example "heat death" → "chết nhiệt", never split).
- Default `translate.priority` in sentence mode: measure `balanced` and `faithful` on the demo video (see 3.7) and set the default to `faithful` **only if** the measured end-of-video lag is at most 6 s and no Vietnamese clip overlaps another; otherwise keep `balanced`. State the numbers.

### 3.3 Stage 4 — voice per unit
One TTS request per unit with the complete sentence. For a sentence longer than `tts.max_chars_per_request` (default 200), split **only at clause punctuation** (comma, semicolon, colon, dash) into the fewest pieces, with the same voice, and join the clips with a short silence (default 80 ms). Fewer requests than segments is expected (the demo video has 79 segments and about 59 sentence endings); report the numbers.

### 3.4 Stage 5 — fit and shorten per unit
The slot of a unit is from its first segment's start to the next unit's start (or the end of its last segment plus the allowed slack). Stretch up to `max_stretch` (1.15). Shortening works on **whole sentences**: ask the model to shorten one sentence while keeping it grammatical and keeping subject, negation, and key terms (the batch request of Update 6 task 4, per unit). Never leave a fragment. If it still does not fit, keep the longer clip as overflow.

### 3.5 Schedule, mix, subtitles
- `schedule.json` is keyed by `unit_id` (`play_start`, `play_end`, `shift`, `overlap`), with the same rules and `max_shift_s`.
- Stage 6 places each unit clip at its `play_start`.
- Stage 7 builds subtitle cues from the units: wrap at 42 characters per line and 2 lines per cue; a unit that needs more is split into several consecutive cues at punctuation or spaces, with times proportional to character counts across the unit's `play_start`–`play_end`, each cue at least 1.0 s where the span allows. The `.srt` file and the burned subtitles use these cues.
- The readable translation file already works from units; make it use `dub_units.json` and the schedule (no change in layout).

### 3.6 Recognizer boundary repeats
Collapse an identical word repeated across a segment boundary (`… the universe | universe must work …`) using the word timestamps: when the last word of segment *i* and the first word of segment *i+1* are identical (case-insensitive) and their time ranges overlap or are less than 0.1 s apart, drop the second. Skip a stop-list of words that are legitimately doubled (`very`, `that`, `had`, `no`, `so`, `bye`, and similar). Log each removal. This complements the existing rule of three or more repeated words.

### 3.7 Evaluation on the demo video
Use the user's demo video if it is in `sample/input/` (the dark-energy video, 79 segments); otherwise `test2`. Report, for sentence mode against segment mode, with the same translation model: the number of TTS requests, the number of TTS texts that do not end a sentence (must be 0 in sentence mode), the `fit_status` counts, the number of shifted and overlapping clips, the maximum shift and the lag at the end, the number of subtitle cues and their longest duration, and the first 12 units as text (English, old Vietnamese fragments, new Vietnamese sentence). Do not say which is better; the user listens and reads.

### 3.8 Tests
- A fixture with the user's pairs: `Dark energy is a strange phenomenon | that scientists believe permeates the universe.` → one unit, one TTS text ending with a full stop; `Heat Death … | In a nutshell … in a heat death scenario, | Matter stays intact …` → no unit text cut inside "chết nhiệt".
- The boundary-repeat rule on `the universe | universe must work`, and on a legitimate double (`very very`) that must stay.
- Sentence-mode schedule, mix placement, and subtitle splitting (a long sentence becomes several cues with proportional times; every cue within 2 lines of 42 characters).
- `dub.granularity: segment` still runs an old job unchanged; sentence mode never reads `translated.json`.
- The prompt text contains the rules of 3.2 (no dropping of negations, no "ta"), checked with a fake provider.

---

## 4. Task 3 — One voice per video (the user's proposal, with refinements)

The proposal is adopted: a finished video never contains two voices unless the user asked for it.

1. **Remove the `alt_voice` and `alt_split` steps from the ladder.** The ladder is: the sentence as is → (only when the text is longer than `tts.max_chars_per_request`) split at clause punctuation → a short backoff → mark pending. Always the same voice. The deferred rounds and the breaker stay, with the same voice.
2. **Record the voice**: one video-wide voice in `state.json` and a `voice` field on every dub unit and in `tts_failures.json`.
3. **Switching is an explicit, whole-video action.** When `tts.stage_budget_s` is exceeded (Update 11 task 2.4), the GUI dialog offers: **Chờ thêm 10 phút**, **Đổi cả video sang "{backup voice}" (tạo lại toàn bộ giọng)**, **Dừng và tiếp tục sau**. The switch button is disabled, with a tooltip, when no backup voice is configured. Switching regenerates **every** unit with the backup voice (clips are cached per `(text, voice)`, so switching back reuses the earlier clips) and updates the video-wide voice. Headless and CLI: `tts.budget_policy: stop | switch_all` (default `stop`).
4. **Prefer a backup voice that does not use the same service.** In Settings → General, the "Giọng dự phòng" dropdown labels each option **"(trực tuyến)"** or **"(trên máy)"**. If the chosen backup uses the same Microsoft service (for example the other Vietnamese neural voice), show a one-line warning that it may fail for the same reason. Report which backup voices are available on this machine (the Windows voice and Piper: present or not).
5. **The summary states the voice**: "Giọng: vi-VN-HoaiMyNeural" and, if a switch happened, when and why. A test asserts that a finished result has exactly one distinct `voice`, except after an explicit switch (which regenerates all units).
6. Tests with the fake provider, driven by the observed pattern (61% of units refused at least once, some with a handshake 503): no alternate voice is ever used automatically; the budget dialog and each of its three choices with real widgets; `switch_all` regenerates all units and the result has one voice; stop keeps finished clips and a resume continues with the same voice.

---

## 5. Task 4 — Voice request rate control and an honest experiment

1. **Global limiter for voice requests.** A token bucket for all edge-tts requests (`tts.max_rps`, default 1 per second), adaptive: when more than 30% of the last 10 requests were refused, halve the rate (minimum one request per 5 s) and log it; recover slowly after 10 consecutive successes. Failures with a handshake 503 count as refusals. The limiter and its current rate are visible in the details panel.
2. **Redo the experiment properly**, because the Update 11 table was inconsistent and was run from source only:
   - **Interleave** all conditions per text in randomized order, in the same time window, and include a **control request** (a short fixed sentence) every 10 requests to measure the state of the service.
   - Conditions: request rate (1 per second, 1 per 3 s, 1 per 10 s) × the same text, same voice. At least 40 requests per condition.
   - Run it **twice in the same window: from source Python and from the packaged exe** (add `DubTool.exe --tts-probe` for this). Report acceptance per condition and per environment, the number of handshake 503 and of `NoAudioReceived`, and the control-request results.
   - Draw conclusions only from the data, and state what it cannot show (for example a service-side outage that affects everything).
3. Set `tts.max_rps` from the data (or keep the default and say why). Do not add an alternate voice or sentence splitting as a retry step on the strength of this experiment unless the interleaved data support it.
4. Tests: the limiter (fake clock) halves on refusals and recovers; handshake 503 counts as a refusal; the probe command prints a table.

---

## 6. Task 5 — Screenshots that show the real interface

1. **`--screenshots` must use the real `windows` Qt platform.** If the platform is not `windows`, stop with a clear error; do not produce images. Keep `offscreen` only for unit tests that do not produce README images. Record the platform in `INDEX.txt`.
2. **The application sets its own fonts.** Load a bundled Vietnamese-capable font (the OFL font already used for subtitles) with `QFontDatabase.addApplicationFont` and put it in the application's font fallback chain, so that Vietnamese letters can never turn into boxes, on any machine or platform.
3. **Image checks (automatic, in `scripts/check_readme_links.py` and in the tests):**
   - the sidecar `docs/images/INDEX.txt` says `platform: windows`;
   - the mean luminance of the window area matches the theme actually applied (the dark theme must not produce a mostly white image);
   - **no missing glyphs:** for every string shown in the states being captured, check each character with `QFontMetrics.inFontUcs4` against the widget's real font and fail if any is missing;
   - the PNG is at least 600 px wide and not blank.
4. Regenerate the README images from the new build (ready, running with the details panel open, finished, Settings → API keys), commit them under `docs/images/`, and make sure the README links resolve to tracked files.
5. **Look at the images.** If you can view images, say what each one shows (theme, language, whether any letter is a box). If you cannot, say so. Give the paths so that the user can check.

---

## 7. Task 6 — Public repository hygiene

The repository is public. Treat everything ever committed as published.

1. **Full-history secret scan:** scan **all commits and all refs** (not only the working tree), using the exact values of the stored API keys, the usual key patterns (the Gemini / Anthropic / GitHub token prefixes, private-key headers), `.env`-style `KEY=value` lines, and file names such as `*.pfx`, `*.pem`, `*.key`. Report counts and file paths only, never values. If anything is found, stop pushing, tell the user to revoke the secret first, and do not rewrite history.
2. **License:** do not create a `LICENSE` file. The README must say plainly "No license has been chosen yet (all rights reserved by default)". In the report, list the user's options with one line each and the main constraint (PySide6 and `edge-tts` are LGPL, the FFmpeg binaries that are bundled in the exe are GPL), and ask the user to choose.
3. **Evidence for the push:** in the GitHub line print `git log -1 --format=%H`, `git status -sb`, and `git ls-remote origin refs/heads/main`; they must show that the remote head equals the local head. If you cannot run `git ls-remote` (no network or credentials), say so and do not claim a push.
4. **Tags:** tag every update `v1.0.N` (`v1.0.12` now). List the existing tags (`git tag -l` and `git ls-remote --tags origin`) and, for the updates 9–11 that have no tag, add tags to the commits that match their reports (tags only; no history rewrite). Say which commit each tag points to.
5. `agent/` stays in the repository (the user's decision). Do not ask again.

---

## 8. Task 7 — Carry-overs from the Update 11 review

1. The replay of the translation stage must start from the **real chunk planner** (the test of 2.5 does).
2. Remove specification identifiers from every log and UI message (rule 5).
3. Report which backup voices are available on this machine (task 3.4).
4. The overload dialog screenshot uses the real model names (`gemini-3.5-flash-lite`, `gemini-3.8-flash`).
5. State the test counts once, from one aggregate run (no `--ignore`), and make every mention agree.
6. Break the "other" bucket (312 files, 188.9 MB) of the package table into named groups (for example NumPy and its libraries, ONNX runtime, `certifi`, Python standard library, the app's own code, fonts, and anything else), with sizes.
7. The `light` retention level (Update 10) measured on the current build.

---

## 9. Verification

Rebuild with version `1.0.12`, build id `Update 12`, and verify from a sanitized environment (shared caches): `--selftest`, `--layout-check`, `--screenshots` (real platform), `--gui-smoke`, plus:

1. **Replays** of tasks 1 and 3 from the evidence files, before and after the fix, with timelines pasted.
2. **Fault injection on the exe:** `--fault llm:503-first=8`, `--fault llm:429-day`, `--fault llm:504-first=2`, `--fault tts:refuse-ids=3,5,8`, `--fault tts:hang=1`: report the stage times, the requests used, the model that translated each unit, and that the result has one voice.
3. **A real run** of the demo video in Fast mode and, if today's quota allows, in Accurate mode, in sentence mode and in segment mode: the metrics of 3.7, the request counts and latencies, and the TTS refusal statistics with the limiter.
4. **The experiment** of task 4 from source and from the exe.
5. Screenshots of: Settings → General with the overload dropdown and the backup voice labels, the TTS budget dialog with its three choices, the details panel during a fallback notice, the finished state, and the README images (real platform).
6. No key anywhere in the build root, logs, screenshots, diagnostics, or readable files (quiet search); the full-history scan of task 6; the aggregate offline suite in one run with the real counts.

---

## 10. Report back

Follow `AGENT.md` section 12, `AGENT_UPDATE.md` 7.2, and the Definition of done. Include:

1. Files added and changed; commands run; aggregate test counts.
2. **Task 1:** the replay timelines (before and after), the request counts, and what the fallback did on the real run.
3. **Task 2:** the comparison of 3.7 with the 12 sample units, and the decision about the default priority with its numbers.
4. **Task 3 and 4:** the voice summary of the real runs, the backup voices available on this machine, and the experiment tables from source and from the exe with what they show and do not show.
5. **Task 5:** the screenshot folder, `INDEX.txt` (platform), the check results, and what you saw in the images (or that you cannot view them).
6. **Task 6:** the history scan result, the license options, and the GitHub line with the three `git` command outputs and the tag list.
7. **Task 7:** each item.
8. The build: absolute path of the exe and the zip, build id, version, SHA-256.
9. What you could not verify (the services' behavior on other days, the dub's sound, translation quality, how the windows look on the user's display).
10. Anything you noticed but did not fix.
11. The review zip path and size.

Then stop and wait for the user's feedback.
