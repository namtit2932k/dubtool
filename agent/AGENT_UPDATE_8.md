# AGENT_UPDATE_8.md — Make the dub robust (a TTS or Gemini hiccup must not kill a job), make translations more accurate (punctuation, filler words, idioms, truncated lines), and close the open items from the Update 7 review

Eighth update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_7.md` still apply: ground rules, data contracts, resumability, secret safety, the review zip rule, the reporting format, the **Definition of done** in `AGENT_UPDATE_7.md` section 1 rule 5 (real-widget tests, real-SDK contract tests, rebuilt exe, fresh screenshots, rewritten `REVIEW_NOTES.md`, one aggregate test run, cleanup report, and the GitHub line from `AGENT_UPDATE_6.md` section 8). This file overrides them only where it says so.

This update responds to three things the user brought:

1. **The diagnostics file `dubtool-diagnostics.zip`** from a failed run: the dub stopped at the voice-generation stage.
2. **The review of the Update 7 package** (findings in section 0).
3. **The exported `test2_vi_translation.docx`**: the translation is not accurate enough, and the speaker puts the word "girl" in front of sentences, which the app cannot tell apart from a real noun.

---

## 0. Findings (each verified in the delivered code, the diagnostics, or the exported document)

### 0.1 From the user's diagnostics (`dubtool-diagnostics.zip`, 2026-10-27, app 1.0.0, frozen exe, edge-tts 7.3.1, google-genai 2.33.0, 3 keys in the keyring)
1. **A single failed TTS segment ended the whole run.** The log shows `edge-tts attempt 1/5 … 5/5 failed: No audio was received` for **segment id=0** (a normal 41-character sentence), 91.9 s in total, then `tts failure … edge-tts failed after 5 attempts`. In `stages/s4_tts.py` a `TTSFailure` is turned into a `RuntimeError` that aborts the stage (lines about 176–179). The provider's cooldown is capped at 30 s and there is no deferred retry, no circuit breaker, and no fallback voice. The cause of the failures (service, IP throttling, network) is **not** established by the logs; the packaged exe produced TTS fine on other runs the same day.
2. **Gemini failures were `504 DEADLINE_EXCEEDED` after the full 180 s timeout, then `503 UNAVAILABLE` ("high demand")**, in two consecutive rounds of 3 attempts. The log lines do not name the model.

### 0.2 From the code
3. **Retries are nested and multiply requests.** `llm/client.py`: `complete_json` runs a validation loop of `MAX_RETRIES` attempts (about line 262) that catches `LLMError`, but `complete()` already retried 3 times and raised `LLMError("giving up after 3 attempts")`. So a persistent 503 produces up to 3 × 3 = 9 requests, each with a timeout of up to 180 s (about 27 minutes for one chunk), which matches the two rounds in the log. A 503 is model-wide, so rotating API keys cannot help; a 504 resends the same large request.
4. **`av<19` is pinned only in `packaging/build_exe.ps1`**, not in `pyproject.toml` (`faster-whisper>=1.0`, no `av` constraint). The user runs from source (`py -m dubtool.app.main`), so a PyAV 19 environment fails in stage 2 with `open() got an unexpected keyword argument 'metadata_errors'`.
5. **Diagnostics are partly wrong or empty:** `language` is read from the environment variable `DUBTOOL_LANG` (empty) while `settings.json` says `vi`; `config_defaults_chars_per_sec` shows the stale constant 14 while the effective config uses 12.5; there is no TTS failure summary, no model names, and no connectivity check.
6. **Two settings keys for one setting:** the UI reads and writes `default_output`, `settings.py` defines `default_output_dir`.
7. **The Update 7 report omitted required items:** no GitHub line; none of the Update 6 close-out results (console-window watcher, launches per stage, CPU tables, preset table, event-loop delay, package size) are in the zip (the notes point to `REPORT_UPDATE_7.md`, which is not in the zip); the cleanup reclaimed only 77.1 MB (about 2,602 MB to 2,525 MB) with no breakdown of where the 2.5 GB is and no question to the user; the end-to-end runs used a 33-second synthesized-speech clip, not the user's videos; the `.docx` appearance check was not done (no LibreOffice).

### 0.3 From the user's exported translation (`test2_vi_translation.docx`, 24 rows) — evidence for tasks 3–6
8. **Row 1 is three or four sentences in one row, with no punctuation:** `I support trans woman because I'm a girl's girl I don't support the rights of trans people just to protect my own rights But I support the rights of trans women because I'm a girl's girl Understand that when they attack trans girls in sports`. The recognizer's first segment was `I support trans woman because I'm a girl's` and the next began `girl I don't support…`, so the idiom "girl's girl" was cut in half. Rows 1–5, 7, 18, and 20 are unpunctuated or run-on; rows 8–17, 19, 22, and 23, which have punctuation, translate acceptably.
9. **The Vietnamese for row 1 is a broken sentence:** `Tôi ủng hộ phụ nữ chuyển giới vì tôi luôn Tôi không bảo vệ quyền…` ("because I always", then a new sentence). The idiom "a girl's girl" (a woman who stands with other women) was lost the first time and rendered by meaning the second time (`vì tôi đứng về phía phụ nữ`).
10. **Compression artifacts:** `Ta vừa thấy…` (literary "ta"), `mọi nữ sinh giáo dục` (the word "trong" is gone), `Không quan tâm.` (subject and "at all" gone), and row 8 loses "What the fuck do you mean the earth is flat" entirely.
11. **Sentences are split across rows** (`… one was crazy. I` | `am a transgender student …`) because unit building depends on punctuation and on caps (`max_segments`, `max_duration_s`).
12. **The word "girl" occurs in this document only as a noun** (`girl's girl`, `trans girls`, `girls`). The user says the speaker also puts "girl" in front of sentences as a verbal tic. If the recognizer did transcribe it, it is not visible here, so it may have been dropped by Whisper, or it may come from another clip. The agent must handle both cases and report what it finds in the user's audio.
13. The document does not record which mode (Fast or Accurate) produced it.

---

## 1. Rules for this update

1. Change only what this file lists. Everything new that changes outputs sits behind a config key, and the old behavior stays reachable.
2. Never print, log, or write an API key. Never read `.env` into output.
3. **Do not claim that translations improved.** Report structural metrics (counts, ratios) before and after, show the text side by side, and let the user judge Vietnamese quality.
4. Any new request to a language model counts toward `llm.max_requests_per_run`; the mechanical steps in this update (punctuation, re-translation of flagged units) always use the **Fast** model.
5. The Definition of done from `AGENT_UPDATE_7.md` applies, including the GitHub line and a rewritten `REVIEW_NOTES.md` that **includes the Update 6 and Update 7 items still missing** (task 7).
6. Do not commit binaries or the user's media. The user's test videos are never uploaded anywhere. Test fixtures contain short text excerpts only.

---

## 2. Task 1 — TTS must never kill a job (finding 1)

### 2.1 Stage 4 behavior
- A failed segment must **not** abort the stage. Record it (`tts_failures.json` already exists), continue with the next segments, and keep every finished clip.
- After the first pass, retry the failed segments in **deferred rounds** with a long cooldown between rounds (config `tts.retry_rounds: 3`, `tts.round_cooldown_s: [60, 180, 300]`). While waiting show a countdown message in the progress panel ("Dịch vụ giọng đọc đang bận, thử lại sau N giây"), and honor Cancel at once.
- **Circuit breaker:** after `tts.breaker_consecutive: 3` failures in a row (a service-wide problem), stop calling the service at once and go straight to the round cooldown instead of burning about 90 s per segment.
- **Adaptive pacing:** the delay between requests starts at the configured value (default 0.3 s), grows after failures (up to `tts.max_delay_s: 2`), decays after successes, and the provider cooldown cap rises from 30 s to `tts.max_cooldown_s` (default 120).
- If segments still fail after the last round, apply the fallback (2.3) if configured; otherwise fail with the dedicated error below, **keeping all finished clips**, so "Resume" continues from where it stopped.

### 2.2 Diagnose the failure better
- Log, for every failed attempt: the exception **type** (for example `NoAudioReceived`, a handshake error with its HTTP status, a client or timeout error), the voice, the `rate` string, the installed `edge-tts` version, and the text length plus its first 80 characters. Never log keys.
- Measure the **system clock offset** once per run (compare local UTC with the `Date` header of one HTTPS response) and log it as a diagnostic; do not change the clock or the library's behavior.
- Add `--selftest --network` and a "Kiểm tra kết nối" button in Settings → Advanced that check DNS and TLS reachability of: the Gemini API host, the Hugging Face host used for model downloads, and the host(s) that `edge-tts` connects to (read them from the installed `edge_tts` package's constants, do not hardcode them), and print a table. This is also included in the exported diagnostics (task 7.2).

### 2.3 Backup voice (evaluate first, then implement only what passes)
Add `tts.fallback: none | windows_sapi | piper` (default `none`) and a `FallbackTTS(primary, secondary)` wrapper over the existing `TTSProvider` interface. **Before implementing a provider, evaluate it and report**:
- **`windows_sapi`:** list the installed voices on this machine (for example PowerShell `System.Speech.Synthesis.SpeechSynthesizer.GetInstalledVoices()`), and say whether a Vietnamese (`vi-VN`) voice exists. If none is installed, report it and do not implement it.
- **`piper`** (local, offline neural TTS): check whether a Vietnamese voice is available, its **license** (some voice datasets carry non-commercial terms), its size, and whether it runs from the packaged exe. Produce three 15-second sample clips of the same Vietnamese text from the primary voice and from each candidate backup so that the user can **listen and decide**. Do not bundle any voice model without license verification and the user's choice.
- Do not implement paid or account-bound services; mention them in the report only.
Where the backup voice was used, record it in the result (how many segments) and tell the user in the summary. A different voice in a few segments is acceptable only because the alternative is a failed job; say so in the message.

### 2.4 Errors in the UI
Add the error code `tts_unavailable` with Vietnamese and English strings that say plainly: the voice service did not respond; progress is saved; the user can resume in a few minutes, change the voice, or enable the backup voice. Buttons: **Tiếp tục / Resume** and **Mở cài đặt / Open settings**. Never show the generic message for this case.

### 2.5 Fault injection for verification
Add debug-only switches for headless runs, ignored by the GUI and logged as a warning when active: `--fault tts:fail-first=N` and `--fault llm:503-first=N`. They exist so the packaged exe can prove the recovery paths without needing the real service to fail.

### 2.6 Tests
Fake provider scenarios: one failing segment does not abort; deferred rounds recover; the circuit breaker pauses immediately after N consecutive failures; Cancel works during a cooldown; if everything fails the finished clips are preserved and a resumed run reuses them; the fallback voice is used and counted; the log fields are present and contain no key; `tts_unavailable` is shown for the exhausted case. Fake clock; no real sleeping.

---

## 3. Task 2 — Gemini retries and overload handling (finding 2, 3)

1. **Remove the nested retries.** The validation loop in `complete_json` must catch **only** parse and validation errors (a reply that arrived but is invalid). Transport, rate-limit, and server errors are retried only by the inner loop. Total requests for one logical call are at most `MAX_RETRIES` transport attempts plus at most 2 validation attempts. Test: a fake provider that always answers 503 produces exactly `MAX_RETRIES` requests (not 9).
2. **Server errors get proper backoff.** For 503/UNAVAILABLE and 504/DEADLINE_EXCEEDED use exponential backoff with jitter (config `llm.server_error_backoff_s: [10, 30, 90]`), not an immediate or fixed retry. These errors are model-wide: do **not** rotate keys for them (rotate only for rate limits and invalid keys).
3. **Smaller requests after a timeout.** After a 504 or a client-side timeout, retry by splitting the chunk into two halves (config `llm.split_on_timeout: true`, minimum 4 units), instead of resending the same large request.
4. **Overload policy.** After `llm.overload_after: 2` consecutive 503/504 on the selected model, apply `llm.overload_policy`:
   - `ask` (default in the GUI): a dialog with three buttons: **Thử lại sau / Try later** (pause with a countdown, keep progress), **Dùng model Nhanh cho phần còn lại / Use the Fast model for the rest**, **Hủy / Cancel**.
   - `fallback_fast` (default for the CLI and `--headless-run`): continue the remaining chunks with the Fast model automatically.
   - `wait`: keep waiting with the backoff.
   Record in the log and the result which chunks used which model, and state it in the run summary.
5. **Per-mode request timeout:** `llm.request_timeout_s` per mode (defaults: Fast 90, Accurate 180). **Log the model name** and the request kind and size (`translate chunk 3/4, 20 units, 2,431 chars`), the elapsed time, and the status on every request; the progress message shows "đang chờ phản hồi (N giây)".
6. **Tests** with fakes: request counts, backoff schedule (fake clock), no key rotation on 503, splitting after a timeout, each overload policy, the dialog (real widgets) with its three buttons, and that the log lines carry the model name.

---

## 4. Task 3 — Punctuation and sentence boundaries (findings 8, 11)

### 4.1 Measure first (no behavior change)
On the user's `test2` video (look for it in `sample/input/`; if it is not there, ask the user for it and report that you could not run this step), run stage 2 for these variants and report a table of: number of segments; **share of segments ending with sentence-final punctuation**; the longest run of words without a sentence-final mark; the number of units and the longest unit duration; the time taken.
- the current settings;
- `condition_on_previous_text` true and false (since Update 3 it defaults to false; whether this affects punctuation is a **hypothesis** to test, not a fact);
- an `initial_prompt` that contains a short, well-punctuated English sample;
- Whisper `small` and `medium`.
Check the installed `faster-whisper` version's `transcribe` signature for the parameter names. Write a side-by-side text file of the first 60 seconds for each variant so that the user can read it. **Do not change any default** because of this table; the user decides.

### 4.2 Word timestamps and sentence re-segmentation
- Request word-level timestamps in stage 2 (`word_timestamps=True`; config `asr.word_timestamps: true`) and store the words with their times (in a separate `words.json` so the existing contract of `transcript.json` does not change).
- New function `resegment_by_sentences(segments, words)`: after punctuation is available (4.3), cut segments at sentence-final marks using the word times, so that **a segment is a sentence** (or a clause when a sentence is longer than about 12 s, cut at commas or pauses). Keep the existing merge and split rules (minimum 0.8 s, maximum 12 s), renumber ids sequentially, and keep the duplicate-removal step. Config `asr.sentence_resegment: true`.

### 4.3 Punctuation restoration (only when needed)
Trigger it when more than 30% of segments lack final punctuation or any run exceeds 25 words without a sentence-final mark. Send the transcript text in windows of about 40 segments to the **Fast** model with strict instructions: add punctuation, capitalization, and sentence breaks only; do not add, remove, reorder, or change any word. **Validate:** compare the word sequence (lowercased, punctuation removed) of the reply with the input; on any mismatch discard the reply for that window and keep the original text (log it, and count it in the quality report). Count these requests in the request budget. Config `punctuate.enabled: auto | on | off` (default `auto`).

### 4.4 Pause-aware units
In `build_units` add one more closing condition: a gap of at least `units.pause_split_s` (default 0.5 s) between consecutive segments closes the unit unless the previous segment ends with a comma. Keep the existing caps. A unit must never contain a sentence-final mark in its interior unless a cap forced the split (property test).

### 4.5 Tests
Fixture text: the English of row 1 quoted in finding 8, plus a synthetic word-timestamp list. Test with a fake LLM that punctuates correctly: the row splits into at least three sentences and no unit contains an interior sentence-final mark; a fake LLM that changes a word is rejected and the original is kept; re-segmentation assigns the right times from word timestamps; the pause rule; the `auto` trigger thresholds; ids stay sequential; the new settings are part of `inputs_hash` of the stages they affect.

---

## 5. Task 4 — Filler words, vocatives, and idioms (findings 9, 12)

### 5.1 Data and settings
Ship `fillers.yaml` (defaults) and merge a user override stored in the app data folder. Structure, with English as the first language:
- `vocative` (default action `drop`): `girl`, `girls`, `babe`, `bestie`, `sis`, `guys`, `dude`, `bro`, `honey`, `ladies`.
- `discourse` (default action `drop`): `um`, `uh`, `like`, `you know`, `I mean`, `kind of`, `sort of`, `literally`, `basically`, `actually`, `okay so`, `well`.
- Allowed actions per entry: `drop`, `keep`, or `translate_as: "<Vietnamese text>"` (for a vocative, for example `chị em ơi` or `này`).
- A list of **protected phrases** that are never treated as fillers: `girl's girl`, `girl power`, `girl dad`, `trans girl(s)`, `the girls`, and so on (editable).
- The settings and the dialog (5.3) let the user change actions and the protected list.

### 5.2 Detector (pure function, unit-tested)
`tag_fillers(sentence_text, words_with_times) -> list[span(kind, text, action)]`. A listed word is a **candidate filler or vocative** only when its position and context fit:
- Vocative: at the start of a sentence or clause and followed by a comma or by a pause of at least `fillers.pause_s` (default 0.25 s), or at the end after a comma; **not** preceded by a determiner, possessive, or adjective (`a`, `the`, `my`, `your`, `her`, `his`, `our`, `their`, `trans`, `good`, `little`, and `'s`), and not part of a protected phrase.
- Discourse markers: for ambiguous words such as `like`, only when set off by commas or a pause, or at the start of a sentence (so "I like pizza" is not a filler).
Words that fail the test are normal words and are translated normally.

### 5.3 Per-video suggestions (speaker tics)
After stage 2 (and the punctuation step), count tokens that begin a sentence or clause and are followed by a comma or pause, excluding a stop-list of ordinary sentence starters (`I`, `The`, `And`, `But`, `So`, `It`, `We`, `You`, `They`, `That`, `This`, `What`, and similar). Suggest tokens that occur at least `max(5, 3% of sentences)` times, with the count and three examples, and save them in `suggested_fillers.json`.
- GUI: when suggestions exist and `fillers.ask: true`, show **one** dialog before translation: "Phát hiện từ đệm: girl (14 lần), like (9 lần)…" with, for each token, **Bỏ / Giữ / Dịch thành…**; the defaults come from `fillers.yaml`. Buttons: "Áp dụng" and "Dùng mặc định lần này". The choice is saved per video (a hash of the file path and size) in the app data folder. The CLI and `--headless-run` never ask (`fillers.ask: false`) and use the defaults.
- The dialog must be driven by a test with real widgets (clicks), and must not appear when nothing is suggested.

### 5.4 Prompt policy
The translation and shortening prompts receive, per unit, the detected spans and a policy block built from the settings, in plain instructions:
- **Vocatives** (listed): action `drop` → do not translate them; `keep` → translate by meaning; `translate_as` → use exactly the given Vietnamese text.
- **Discourse fillers** (listed): omit them.
- If the same word is used as a noun, as part of an idiom, or as part of the meaning of the sentence (for example "a girl's girl", "girl power", "trans girls", "the girls"), translate it normally.
- **Idioms are translated by meaning, never word by word.** For "a girl's girl" use `người luôn đứng về phía phụ nữ` unless the glossary says otherwise. Add this entry to `glossary.example.json`.
- Shortening order: remove fillers first, then repeated words, then modifiers; **never** remove the subject, a negation, or the key noun phrase.
- Only the glossary entries and protected phrases that occur in the chunk are sent (to save tokens).

### 5.5 Tests
- Detector cases: `Girl, I support…` (vocative); `I'm a girl's girl` (not); `trans girls in sports` (not); `the girls` (not); `like, what?` (filler); `I like pizza` (not); `Girl` after a pause; a protected phrase.
- Auto-suggestion counts on a synthetic transcript with a repeated sentence-initial `girl`; the stop-list; the threshold.
- Prompt rendering for each action, including `translate_as`; the per-chunk glossary filtering.
- The dialog flow with real widgets; persistence per video; the new settings are part of `inputs_hash`.
- **In the report, say plainly what the detector found in the user's `test2` audio** (counts of sentence-initial `girl` and other tics, with the segment times), including the possibility that Whisper dropped the word.

---

## 6. Task 5 — Completeness guards and the fidelity choice (findings 9, 10)

### 6.1 Prompt rules (translation and shortening)
- Do not use the literary first-person pronoun `ta` unless the brief asks for it; use the pronouns from `translate.pronouns`.
- Do not drop function words in a way that makes a sentence ungrammatical (`trong`, `của`, `để`, `với`, …).
- **Every unit's Vietnamese must be a complete sentence ending with the sentence-final punctuation of its source.** If the source unit is cut by a cap, end the Vietnamese at a natural clause boundary instead of mid-phrase.

### 6.2 Truncation and shrink detector
A pure function that flags a translated unit when:
- the English unit ends with sentence-final punctuation but the Vietnamese does not; or
- the Vietnamese ends with a word from a configurable list of connectors and function words (`luôn`, `và`, `mà`, `nhưng`, `vì`, `nếu`, `rằng`, `là`, `của`, `để`, `một`, `những`, `các`, `thì`, `với`, `cho`, `khi`, `như`, `hay`, `hoặc`, `bởi`); or
- `len(vi) < 0.5 × len(en)` for units longer than 40 characters (probable loss of content).
Re-translate the flagged units **once**, in a single batch request (Fast model unless the run is in Accurate mode and the quota allows), telling the model that the previous output was incomplete and giving it a character allowance of 30% more. If a unit is still flagged, keep it and list it in `quality_report.json` (time, English, Vietnamese, reason). Show a plain-language line in the run summary ("3 câu có thể chưa đầy đủ — xem bản dịch để kiểm tra" with the times). Config `readable.mark_uncertain: false` (default): when `true`, the customer-readable file marks those rows with "(cần kiểm tra)".

### 6.3 Fidelity preset (the trade-off between meaning and timing)
Add `translate.priority: faithful | balanced | timing` (default `balanced`, which is the current behavior) and a dropdown in Settings → General ("Ưu tiên: Đủ ý / Cân bằng / Khớp thời lượng"):
- `balanced`: unchanged.
- `faithful`: the character budget is multiplied by 1.5 (the only shortening allowed in the translation prompt is fillers), the shortening rounds in stage 5 only stretch and shorten fillers, and the schedule's `max_shift_s` is raised to 4 s. The dub may lag the speaker more; say so in the tooltip.
- `timing`: the budget is multiplied by 0.9 and shortening is allowed to remove modifiers (never subjects or negations).
Put the preset and its numbers in `inputs_hash`. On `test2` report, for `balanced` and `faithful`, the `fit_status` counts, the number of shifted clips and the maximum shift, and the total lag at the end of the video. Do not change the default.

### 6.4 Tests
Detector cases built from the Vietnamese examples in findings 9 and 10 (`… vì tôi luôn` is flagged; `Không quan tâm.` against `She doesn't care at all.` is flagged by the shrink rule only if over the threshold; a complete sentence is not flagged); the re-translation batch with a fake LLM; the report file; the preset multipliers; the tooltip and dropdown exist; units cut by caps are judged after joining.

---

## 7. Task 6 — Choosing the speech-recognition model (finding 8, 11)

In Settings → General replace the Whisper model field with a dropdown: `base`, `small` (default), `medium`, `large-v3`, each with a one-line description (for example "nhanh nhất", "cân bằng", "chính xác hơn, chậm hơn, cần nhiều bộ nhớ hơn", "chính xác nhất, rất chậm nếu không có GPU"). Show the **download size before the first download** from the model metadata when it can be read, and otherwise say that the size is shown before downloading. After a run, show the real elapsed time of stage 2 and the video length ("lần chạy trước: 4 phút cho video 3 phút") in the tooltip. Measure, on this machine, the real-time factor of stage 2 for `small` and `medium` on a 60-second clip and report it (do not hard-code speeds). The choice is part of `inputs_hash`.

---

## 8. Task 7 — Close out the review items

1. **`av` pin (finding 4):** add `av>=11,<19` to `pyproject.toml`, keep the build pin, and add a startup check when running from source: if the installed `av` is 19 or newer, show (in the app and the CLI) a message with the exact command `pip install "av>=11,<19"` before stage 2 fails. Add a test for the check with a fake version.
2. **Diagnostics (findings 5):** `language` from the settings; `chars_per_sec` from the **loaded effective config**; add the TTS failure summary (counts by exception type and the first and last 5 failures with 80-character texts), the quota summary (key positions and models only), per-stage times, the model names used per stage, the build id, the Whisper model, the CPU setting, the free disk space of the output and jobs folders, the connectivity table (2.2), and the tails of the last 3 job logs. Redact keys (exact stored values and the usual patterns). Test that no key value is present.
3. **One setting key (finding 6):** keep `default_output_dir`; migrate any `default_output` value to it on load; update the main window and the settings dialog; test the migration and that the old key is no longer written.
4. **Reports in the zip (finding 7):** the review zip must also include the latest `REPORT_UPDATE_*.md` and the rewritten `REVIEW_NOTES.md`. `REVIEW_NOTES.md` must contain the Update 6 and Update 7 close-out results that are missing (console-window watcher, launches per stage before and after, CPU tables, preset table, event-loop delay, cancel check, package sizes, the FFmpeg build now bundled, the aggregate test count), or the words "not done" with the reason for each.
5. **Real end-to-end runs:** run `--headless-run` in Fast mode on the user's real videos in `sample/input/` (`test1`, `test2`) and report the results for each (stage times, requests used, TTS retries, flagged units, readable document rows). Use the synthesized sample only as an additional check. If a video is missing, say so.
6. **Cleanup that actually reduces size:** run the dry run and put the **top 20 largest items and the category table** in `REVIEW_NOTES.md` (the project is still about 2.5 GB). Fix the `dist_old` rule so that it keeps the newest **pair** (the `dist/DubTool` folder and its zip, matched by build id) and deletes older pairs. Apply the regenerable categories. Then **ask the user, with the real numbers,** what to do about `.venv`, `.venv-build`, `models/`, `vendor/`, and the old `sample/output` files; propose merging the two virtual environments but do not delete anything on the report-only list without the user's answer.
7. **GitHub:** the report has the GitHub line as required by `AGENT_UPDATE_6.md` section 8 (pushed or not, commit, tag, gate results, reason if not pushed, any question you need answered).
8. **Appearance of the Word document:** LibreOffice was not available, so the render check is still open. If you can obtain a way to render a `.docx` to an image on this machine, do it and put the PNGs next to the screenshots; otherwise repeat that it is not verified.

---

## 9. Task 8 — A translation-quality evaluation harness (so that quality is no longer guessed)

Add `dubtool eval-translation <job_id> [--variants current,punctuated,faithful]`, which writes `eval/<timestamp>.md` (outside Git, small) with, for each unit: the time, the English, the Vietnamese per variant, and the flags (unpunctuated source, run-on length, dangling end, shrink ratio, filler spans and their actions, glossary hits, idioms matched). It reuses the units of the job and counts its requests in the budget. It never scores or ranks; the user reads it.

Regression fixtures (short text excerpts only) in `tests/fixtures/`: the row 1 English of `test2` and its broken Vietnamese from findings 8–9. Tests assert structure only: the English splits into at least three sentences after punctuation restoration (fake LLM), the broken Vietnamese is flagged, a vocative `girl,` is detected, and `girl's girl` is not.

---

## 10. Verification

Rebuild the exe and verify from a sanitized environment as in `AGENT_UPDATE_7.md` section 8 (`--selftest`, `--layout-check`, `--screenshots`, `--gui-smoke`). Extend `--gui-smoke` with: the filler dialog (a fake suggestion list, the real buttons), the Gemini overload dialog (three buttons), the `tts_unavailable` error with the Resume button, and the Whisper dropdown.

Required screenshots (from the packaged exe, new build folder): the filler dialog, the overload dialog, the TTS-unavailable error, Settings → General with the Whisper dropdown and the priority dropdown, and Settings → Advanced with the connectivity check.

Checks:
- [ ] The packaged exe survives injected faults: `--fault tts:fail-first=6` completes the run through deferred rounds (report the number of rounds and the time), and `--fault llm:503-first=4` follows the overload policy (report requests used, and prove that the request count is not multiplied).
- [ ] `--headless-run` on `test2` in Fast mode: report the structural metrics **before** (from the Update 7 output if available, otherwise from the `current` variant) and **after** (unpunctuated runs, unit count, longest unit, flagged units, fit statuses), the filler detector's findings, the first 8 rows of the readable document as text, and the readable file's row count.
- [ ] The same for `test1` if it is present.
- [ ] No key found anywhere in the dist folder, logs, screenshots, diagnostics, or readable files (quiet search).
- [ ] The aggregate offline suite passes in one run; report the real counts (passed, failed, skipped, and why).

---

## 11. Report back

Follow `AGENT.md` section 12, `AGENT_UPDATE.md` 7.2, and the Definition of done. Include:

1. Files added and changed, commands run, the aggregate test counts.
2. **TTS resilience:** what the circuit breaker, rounds, and fallback did in the fault-injection run; the connectivity table; the evaluation of the backup voices with the paths of the sample clips for the user to listen to; what is still unknown about why edge-tts returned "No audio was received".
3. **Gemini:** the request counts before and after the retry fix, the overload policy behavior, and the model names in the log.
4. **Punctuation and sentences:** the variants table (4.1) with the side-by-side text files, what you changed, and the before/after metrics on `test2`.
5. **Fillers and idioms:** what the detector found in the user's audio (counts, times), how the dialog and the policy behave, and one example of each action on real lines (original, previous Vietnamese, new Vietnamese).
6. **Completeness and fidelity:** the flagged units on `test2`, the re-translation results, and the `balanced` and `faithful` numbers.
7. **Whisper model:** the real-time factors you measured.
8. **Close-out items (task 7):** each of the eight items with its result, including the missing Update 6 and Update 7 numbers, the cleanup breakdown and the questions for the user, and the GitHub line.
9. The build id, the exe path, size and SHA-256, and the screenshot folder with `INDEX.txt`.
10. What you could not verify: whether the translations are better (the user judges), how the dub sounds, how the windows and the Word document look on the user's computer, and why the TTS service failed.
11. Anything you noticed but did not fix.
12. The review zip path and size.

Then stop and wait for the user's feedback.
