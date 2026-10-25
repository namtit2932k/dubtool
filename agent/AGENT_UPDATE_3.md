# AGENT_UPDATE_3.md — Fix oversized subtitles, reduce overflow and overlapping voices, improve translation quality, and close correctness gaps

Third update. `AGENT.md`, `AGENT_UPDATE.md`, and `AGENT_UPDATE_2.md` still apply (ground rules, data contracts, resumability, secret safety, review zip rule `AGENT_UPDATE.md` 7.2, reporting format). This file overrides them **only** where it says so:

- `AGENT_UPDATE_2.md` 3.2 (subtitle cue timing) and 3.3 (subtitle font sizing).
- `AGENT.md` stage 5 (how `max_chars` is derived) and stage 6 (how clips are placed on the timeline).
- `AGENT_UPDATE.md` 3.3 and the translation prompts in `llm/prompts.py` (how stage 3 groups segments, words the prompt, and treats the character budget).
- `AGENT.md` stage 2 (a small post-processing step is added).

This update comes from a code review plus tests of the delivered code. Everything below was either reproduced or read directly in the code. Work through tasks 1–8 **in the order given**. After each task, run its checks before starting the next one. There are **two stop points** (see section 10): the full rerun on the sample happens only once, after the user has chosen a translation model.

---

## 0. Findings this update responds to

1. **Subtitles are far too big.** Reproduced with the exact burn filter at 1080p: an 80-character cue became 4 lines covering about 60% of the frame height, with the bottom edge about 233 px above the frame bottom. Cause: for SRT input, libass computes `FontSize`, `MarginV`, and `Outline` on a virtual frame that is **288 px high**, then scales them by `video_height / 288`. At 1080p, `FontSize=45` therefore renders at about 170 px. The `original_size` option suggested in `AGENT_UPDATE_2.md` was tested and has **no effect** on this; do not use it as the fix. Corrected values were tested: `FontSize=12`, `MarginV=16`, `Outline=0.6` at 1080p gives 2 lines at about 8% of frame height with the bottom edge about 61 px above the bottom.
2. **38% of segments end as `overflow`** (31 of 82; 36 `stretched`, 13 `ok`, 2 `shortened`). In the 10-segment sample the real TTS speed has a median of about **10 characters per second**, but `chars_per_sec` is 14. The shorten loop rescues almost nothing.
3. **Overflowing clips can overlap the next clip.** Stage 6 places each clip at its original `start` and never prevents two Vietnamese clips playing at once.
4. **Subtitle text can differ from the spoken audio.** In stage 5, if TTS fails after a shorten, `entry["vi"]` already holds the shortened text while the clip is still the old, longer one.
5. **`--force` does not really force stage 5's fitted clips**: `materialize_clip` returns early if `fitted/<id>.wav` already exists.
6. edge-tts still fails often (65 warnings in the last 200 log lines, 5 segments failed all 5 attempts) and failures are logged without segment id or text, so we cannot see whether they correlate with content.
7. Smaller issues: font lookup uses the configured name for the wrong file; stage 5 requires an LLM provider even when nothing needs shortening; very long cues produce overlong lines.
8. **Translation quality problems seen in a 10-segment sample** (prompt in `llm/prompts.py`, model `gemini-3.5-flash-lite`):
   - A reaction was rendered as slang and a subject was dropped ("Oh my god. Is she doing it again? Oh wow." became "Trời ơi, lại thế nữa à? Đỉnh thật."). The output (34 characters) was far under its 49-character budget, so the budget was **not** the cause. The prompt asks for "NOT literal", a "native Vietnamese YouTuber" voice, and says "shorten the meaning rather than overflow".
   - A sentence the ASR split across two segments was translated in halves ("...maybe I shouldn't leave" / "this marriage because can I even get better?" became "nhỡ mình không nên bỏ" / "cưới để tốt hơn ư?"), which changed the meaning.
   - Whisper repeated the words "The answer is always yes" across two consecutive segments, so the dub would say it twice.
   - The model receives no description of the video and the glossary is only the example file; a "lite" model is in use.
   Note: Task 2 lowers `chars_per_sec`, which tightens budgets. Task 7 therefore turns the budget into a soft limit so that quality does not drop further.

---

## 1. Rules for this update

1. Do not touch anything not listed here. If you see another problem, report it at the end; do not fix it.
2. **Back up before rerunning.** Before rerunning any stage on the existing job, copy `translated.json` to `translated.before_update3.json` and `transcript.json` to `transcript.before_update3.json` in the job directory. Derived folders and files are **moved**, not deleted (see section 10).
3. Keep the old deliverables: `sample/output/test1.mp4` and `sample/output/test1_v2.mp4` must stay unchanged. The new result goes to **`sample/output/test1_v3.mp4`**.
4. Rerunning stages 3–5 calls Gemini and edge-tts again (free-tier limits, throttling). The pipeline is resumable; if a limit is hit, wait and rerun the same command. Never report success on a partial run. **Rerun stages 3–5 only once** (Phase B in section 10); do not rerun them to try out individual tasks.
5. Never print or log secrets. Never read `.env` into output.
6. Report only what you measured. State plainly what you cannot judge (how it sounds, whether subtitles look good, whether a translation is better; the user is a native Vietnamese speaker and judges translation quality).
7. Do not invent content for the user's video: no made-up video brief, no made-up glossary terms. If the user did not provide them, run without and say so.
8. Do not change the default model in `config.yaml` on your own. The user chooses it after the comparison in Task 8.

---

## 2. Task 1 — Fix subtitle sizing (do this first)

### 2.1 Convert pixels to libass units

Add a pure function, for example in `subtitles.py`:

```python
LIBASS_SRT_PLAYRES_Y = 288

def px_to_ass_units(px: float, video_height: int) -> float:
    return px * LIBASS_SRT_PLAYRES_Y / video_height
```

In stage 7, decide the **intended real pixel sizes** first, then convert:

- Font size (px) = `video_height / subtitles.size_ratio` (keep `size_ratio: 24` as the default; this means 45 px at 1080p, which is the intent).
- Bottom margin (px) = `video_height / subtitles.margin_ratio` (default 18).
- Outline (px) = `max(1.0, video_height / 540)`.
- Pass `FontSize`, `MarginV`, and `Outline` to `force_style` in **converted units**, rounded to 2 decimals where fractional values are allowed. Use `Shadow=0`.
- Remove any use of `original_size`.
- Log both the intended pixel values and the converted units.

### 2.2 Fix font lookup

- Honor `subtitles.font`. Use an explicit map of family to font files, for example: `Arial → arial.ttf`, `Segoe UI → segoeui.ttf`, `Tahoma → tahoma.ttf`, `Times New Roman → times.ttf`, `DejaVu Sans → DejaVuSans.ttf`. Pass libass the **family name that matches the copied file**.
- If the configured font is not found, try the fallbacks in that order but set `FontName` to the family of the file actually found, and log which one was used. Never pair one family name with another family's file.
- Search font directories recursively (the DejaVu path on Linux is nested).
- If nothing is found, stop with the existing clear error.

### 2.3 Long cues

- If a cue's text is longer than `2 × max_line_chars`, split it into several consecutive cues at punctuation or word boundaries, each at most 2 lines, and divide the cue's time span in proportion to character counts (each part at least 1.0 s if the span allows).
- Add `subtitles.max_line_chars` (default 42). For portrait video (height > width) default to 28.
- Keep UTF-8 and Vietnamese diacritics intact.

### 2.4 Checks for task 1

- [ ] Unit tests for `px_to_ass_units` (1080p: 45 px → 12.0 units; 720p: 30 px → 12.0 units).
- [ ] A **render test** (skipped only if ffmpeg/libass is missing; say so in the report if skipped): burn an 80-character, 2-line cue onto a black 1920x1080 frame and measure the text block from pixels. Assert: text block height ≤ 12% of frame height, bottom edge between 2% and 8% of the frame height above the bottom, text width ≤ 85% of the frame width. Repeat at 1280x720 and at a portrait 1080x1920 frame.
- [ ] Tests for font lookup (configured family is honored; fallback uses the matching family name) and for long-cue splitting.
- [ ] On the real job, extract frames at ≥ 5 cues (include the longest cue and one containing ắ, ữ, ề, ộ, ỵ), compare each against the same frame without subtitles, and report for each: text block height as % of frame height, and distance of its bottom edge from the frame bottom. Save the PNGs in `jobs/<job_id>/check/` and give the paths. Expected: about 8% for two lines at 1080p. If any is above 15%, treat it as a failure.

---

## 3. Task 2 — Calibrate speech rate to cut overflow

### 3.1 Measure first (no API calls)

From the existing `translated.json` and `tts_duration`, compute characters of `vi` per second of TTS for all segments. Report median, 25th percentile, 75th percentile, and the number of segments. Also check whether the edge-tts clips contain leading or trailing silence longer than 0.15 s (for example with ffmpeg `silencedetect` on ~10 clips) and report the average seconds of silence per clip.

### 3.2 Apply, only what the measurements justify

- Set `chars_per_sec` in `config.yaml` to the **25th percentile** of the measured rates, rounded down to the nearest 0.5, so that most sentences fit even when the LLM fills the limit. Write the measured numbers in a comment next to it. (The user may adjust it afterward.)
- If clips carry meaningful silence (average more than 0.2 s per clip), add safe trimming in stage 4 using ffmpeg `silenceremove`, keeping a ~0.05 s margin so that no word onset or ending is cut. Record `tts_duration` **after** trimming, and make the TTS cache key include the trim setting. If silence is below that threshold, do not add trimming.
- Add config `tts_rate` (edge-tts speaking-rate parameter such as `"+0%"`), default `"+0%"`. Do **not** change the default; mention in the report that a small positive rate is an option and let the user decide.

### 3.3 Rerun with the new setting

Do **not** rerun anything yet. The single rerun of stages 3–5 happens in Phase B of section 10, after Tasks 7 and 8, so that the new `chars_per_sec`, the new prompt, and the chosen model are all applied together. Reviewed segments (`reviewed: true`) must stay untouched. The TTS cache means unchanged texts are not regenerated.

### 3.4 Checks for task 2

- [ ] The report shows the measured distribution, the chosen `chars_per_sec`, and the silence measurement with the decision made.
- [ ] A table of `fit_status` counts **before** (ok 13, stretched 36, shortened 2, overflow 31) and **after**. The goal is a clearly lower overflow count; report the number whether or not it improves, and do not tune further beyond what is specified here.
- [ ] No segment is stretched beyond `max_stretch` (1.15).
- [ ] Reviewed segments unchanged (compare with the backup).

---

## 4. Task 3 — Schedule clips so Vietnamese voices never overlap, and make subtitles follow the schedule

### 4.1 One schedule used by both stage 6 and stage 7

Create a small module (for example `schedule.py`) that produces `schedule.json` in the job directory:

```json
[{"id": 0, "orig_start": 0.52, "play_start": 0.52, "play_end": 4.31, "shift": 0.0}]
```

Algorithm, processing segments in order of `start`:

- `play_start = max(orig_start, previous_play_end + gap)` with `gap = 0.08` s (config `schedule.gap_s`).
- `shift = play_start - orig_start`. Cap `shift` at `schedule.max_shift_s` (default 2.0). If the cap would be exceeded, use `orig_start + max_shift_s` and mark the segment as `overlap: true`.
- `play_end = play_start + actual duration of fitted/<id>.wav` (probe the file; do not assume).
- The schedule must be deterministic and unit-testable as a pure function over (start, duration) pairs.

### 4.2 Use it

- **Stage 6** places each clip at `play_start`, not `start`.
- **Stage 7** builds cues from the schedule: cue start = `play_start`, cue end = `play_end` plus up to 0.2 s of tail, capped at 0.05 s before the next cue's start, with the minimum cue duration of 1.0 s where the next cue allows it. This replaces the cue-timing rule in `AGENT_UPDATE_2.md` 3.2.
- Segments whose text is empty must not take part in `next_start` calculations.

### 4.3 Checks for task 3

- [ ] Unit tests: no overlap when clips fit; shifting when a clip overruns; the cap marks `overlap: true`; cumulative shifts behave as specified.
- [ ] On the real job, report: how many segments were shifted, the maximum and the total shift, and the ids marked `overlap: true`. Verify from `schedule.json` that no two clips overlap by more than 0.02 s except the ids marked `overlap: true`.
- [ ] SRT cues match `schedule.json` times (a test comparing the two).

---

## 5. Task 4 — Keep text and audio consistent when TTS fails (stage 5)

- Before overwriting `entry["vi"]` with a shortened sentence, keep the previous `vi` and `tts_duration`.
- If `llm.shorten(...)` raises, leave the entry unchanged.
- If TTS raises `TTSFailure` after a shorten, **restore** `entry["vi"]` and `entry["tts_duration"]` to the previous values so they match the clip that is actually used, then break out of the loop and fall through to the existing overflow logic. Never leave `vi` describing a clip that does not exist.
- Create the LLM provider **lazily**, only when the first shorten is needed, so a rerun that needs no shortening works without an API key.

Checks:
- [ ] Test with a fake TTS that raises `TTSFailure` on the shorten attempt: assert `vi` and `tts_duration` are restored and the source clip is untouched.
- [ ] Test with a fake LLM that raises: entry unchanged.
- [ ] Test that stage 5 runs with no LLM key when nothing needs shortening.

---

## 6. Task 5 — Make `--force` real for fitted clips

- Whenever a segment is processed in the current run (that is, not skipped by the resume condition), regenerate `fitted/<id>.wav`, overwriting any existing file, so stale audio can never survive a changed sentence or a changed status.
- Skipping stays correct only when the existing resume condition holds (clip exists, status already final, no `--force`).

Checks:
- [ ] Test: change a segment's `vi` and rerun with `--force`; the fitted clip is regenerated (compare modification time or content).
- [ ] Test: rerun without `--force` on an unchanged job; nothing is regenerated.

---

## 7. Task 6 — Log TTS failures with context

- When TTS fails for a segment, log: segment id, number of characters, attempt number, the error, elapsed seconds, and the text **truncated to 120 characters**.
- Collect all failures in `tts_failures.json` in the job directory: `[{"id", "attempt", "chars", "text", "error", "elapsed_s"}]`.
- At the end of stage 4 and stage 5, log a summary: total failures, how many segments failed all attempts, and basic stats (mean/median characters of failing texts vs. all texts, and whether failing texts more often contain digits or symbols). State only what the numbers show; do not claim a cause.
- Review zip: put only counts and character-length statistics from `tts_failures.json` in `excerpts/`, not full texts, except for the first 10 entries.

---

## 8. Task 7 — Translation quality (prompt, sentence-level translation, ASR duplicate cleanup)

Goal: more faithful Vietnamese, fewer broken sentences, no repeated sentences. All of this lives in stages 2 and 3 and `llm/prompts.py`. **The data contracts do not change**: `transcript.json` and `translated.json` stay per segment with the same fields.

### 8.1 Rewrite the prompts (`llm/prompts.py`, one source for all providers)

The translation system and user prompts must say, in clear English instructions:

- **Accuracy first.** Translate the meaning faithfully. Do not add information that is not in the English. Do not drop a whole clause or idea.
- **Register.** Neutral, natural, conversational Vietnamese that matches the speaker's tone. No slang, internet slang, or exaggerated intensifiers unless the English is clearly slang. Remove the current "native Vietnamese YouTuber" and "NOT literal" wording that pushes the model toward punchy rewriting.
- **Reactions and interjections** ("oh wow", "oh my god") are translated as reactions of the same emotion, not as praise or slang.
- **Subjects and pronouns** are kept when needed for clarity (for example "cô ấy", "anh ấy", "họ"). Names stay as they are.
- **The character budget is a soft target.** If the Vietnamese would exceed `max_chars`, first remove filler words and redundancy; never drop a clause or change the meaning to fit. Exceeding by a little is acceptable; later stages handle it.
- **Video brief** (see below) is included in a labeled block when present.
- **Glossary** terms are still applied exactly.
- **2–3 short examples** written by you (generic, not copied from the user's video) showing: a reaction translated faithfully, a subject kept, and filler removed instead of meaning.
- Keep the JSON-only output rules and the "exact same ids" rule.

The `SHORTEN_*` prompts get the same rules: remove filler first, never change the meaning, never add slang.

Config:

```yaml
translate:
  unit_mode: sentence       # sentence | segment (segment = previous behavior)
  budget_overshoot: 1.15    # enforcement pass triggers only above max_chars * this (was 1.1)
```

**Video brief.** Add `--brief "<text>"` and `--brief-file <path>` to `dubtool run` and `dubtool stage`. If neither is given, look for `<input video name>.brief.txt` next to the input video (for the sample: `sample/input/test1.brief.txt`). Limit the brief to 1,000 characters. If none exists, omit the block and say so in the report. Never write a brief yourself.

### 8.2 Sentence-level translation, then split back to segments

Segments are cut by the ASR at arbitrary points, sometimes in the middle of a sentence. Translate whole sentences, then divide the Vietnamese back across the original segments.

**(a) `build_units(transcript) -> units`** (pure function). Walk the segments in order and close a unit when any of these is true: the segment text ends with sentence-final punctuation (`.`, `?`, `!`, `…`, optionally followed by a closing quote or bracket) and the last word is not a common abbreviation (Mr., Mrs., Dr., vs., etc., e.g., i.e.); the gap to the next segment is more than `units.max_gap_s`; the unit's duration reaches `units.max_duration_s`; or the unit has `units.max_segments` segments. Save `units.json` in the job directory: `[{"unit_id", "seg_ids", "text", "start", "end"}]`.

```yaml
units:
  max_segments: 4
  max_duration_s: 14
  max_gap_s: 1.0
```

**(b) Translate units.** Stage 3 sends units (not segments) to the LLM in chunks (`chunk_size` now counts units), keeping the previous-context rule. A unit's `max_chars` is the sum of its segments' `max_chars`.

**(c) `split_translation(vi_text, segments) -> list[str]`** (pure function). Divide the Vietnamese text of a unit into exactly `len(segments)` non-empty parts:

- The target share of each part is its segment's share of the unit's total duration.
- Cut only at word boundaries, never inside a word. Prefer cutting after punctuation (`,` `;` `:` `.` `?` `!` `…`) when one lies close to the ideal cut position; otherwise cut at the nearest word boundary.
- Each part has at least one word. If the Vietnamese has fewer words than segments, use fewer words per part but never produce an empty part (the later parts may be a single word).
- Re-joining the parts with single spaces must reproduce the original text, ignoring whitespace differences. Vietnamese diacritics stay intact (UTF-8).

**(d) Write the parts into `translated.json`** exactly as before (one entry per segment, same fields). Then run the existing budget enforcement pass per segment, using `translate.budget_overshoot`.

**(e) Reviewed segments.** If any segment of a unit has `reviewed: true`, skip the whole unit and leave all its segments untouched.

**(f)** `translate.unit_mode: segment` restores the old per-segment behavior.

### 8.3 ASR duplicate cleanup (stage 2 post-processing)

Add a deterministic `dedupe_repeats(segments)` step after the existing merge/split cleanup:

- For each pair of consecutive segments, compare the last `n` words of segment *i* with the first `n` words of segment *i+1*, ignoring case and punctuation, for `n` from 8 down to `asr.dedupe_min_words` (default 3). On the longest exact match, remove those `n` words from the **start of segment *i+1*** and keep the rest of its text.
- Example: segment *i* ends with "And the answer is always yes." and segment *i+1* starts with "The answer is always yes. And this idea that..."; the repeated words are removed from segment *i+1*.
- If segment *i+1* becomes empty, drop it and extend segment *i*'s `end` to cover it. Renumber ids sequentially after dropping.
- Repetition inside one segment is left alone. Only a repeat that straddles a segment boundary is removed.
- Log every removal (segment id and removed words) and report the total.
- Add `dubtool stage <job_id> s2 --postprocess-only`, which applies this step (and the existing merge/split rules) to the existing `transcript.json` **without running Whisper**. Back up first (rule 2).
- Add config `asr.condition_on_previous_text` (default `false`, used only for new transcriptions) and pass it to faster-whisper. Do **not** rerun Whisper on the sample unless the user asks.

```yaml
asr:
  dedupe_min_words: 3
  condition_on_previous_text: false
```

### 8.4 Checks for Task 7

- [ ] Unit tests for `build_units`: closes at sentence-final punctuation; does not close at abbreviations; respects the gap, duration, and segment caps; every segment belongs to exactly one unit, in order.
- [ ] Unit tests for `split_translation`: exactly `k` non-empty parts; no cut inside a word; prefers punctuation when close; re-joined text equals the input; fewer words than segments does not produce empty parts; diacritics preserved.
- [ ] Unit test for `dedupe_repeats` using the example above, a test that a legitimate in-segment repeat is untouched, and a test that a segment that becomes empty is dropped with `end` extended and ids renumbered.
- [ ] Stage 3 test with a fake provider: the output `translated.json` keeps the per-segment contract; a unit containing a reviewed segment is skipped.
- [ ] Prompt tests: the brief block appears only when a brief exists; the soft-budget wording and the JSON-only rule are present; no provider-specific text.
- [ ] On the real job (Phase A, no API calls): run `--postprocess-only`, then report how many duplicate removals happened (ids and removed words) and show `build_units` statistics (number of units, how many have 2+ segments, longest unit duration).

---

## 9. Task 8 — Compare translation models on the same sentences

Goal: let the user choose the translation model with evidence, not guesses. The agent **collects**; the user **judges**.

Add `dubtool compare-translate <job_id> [--units 20] [--from 0] --models <provider:model>,<provider:model>[,...]`.

- Run the **same new prompt, same context, same units** (the first `--units` consecutive units starting at `--from`) through each listed model once.
- Include the current default model and, if the user's free key can use it, at least one non-lite Gemini Flash model. **Do not guess model names.** Check the official Gemini models and pricing pages for the currently available names and which are free, and report the source and the date you checked. If a model is not available on the user's key or plan, say so and skip it.
- Providers whose key is missing (or whose local server is not running) are skipped with a clear one-line message; do not fail the command.
- Output `jobs/<job_id>/compare/translation_compare_<timestamp>.md`: for each unit, the English text, then one column per model with the Vietnamese, its character count against the unit's budget, plus per-model token counts and time taken. Do **not** score the translations and do **not** recommend a model.
- Never change the default in `config.yaml` (rule 8).

Checks:
- [ ] Test with fake providers: the file is produced with one column per usable model, and an unusable provider is skipped without error.
- [ ] On the real job (Phase A): run it for up to 20 units with the models available, then give the user the file path.

---

## 10. Run and verify on the sample

### Phase A — implement, test, compare (no full rerun yet)

- Implement Tasks 1–8, run the offline unit tests, and report the counts.
- Apply the ASR duplicate cleanup to the existing job's transcript (Task 7.3, no API calls) and report the removals.
- Run `dubtool compare-translate` (Task 8) and produce the new review zip (`AGENT_UPDATE.md` 7.2).
- **STOP POINT 1.** Report what you did and the path of the comparison file, then **wait for the user** to say which model to use, and optionally to provide `sample/input/test1.brief.txt` and a `glossary.json`. Do not rerun stage 3 before then. Exception: if the user's message already names the model, continue directly to Phase B.

### Phase B — the single rerun (after the user's answer)

1. Back up (rule 2): copy `translated.json` to `translated.before_update3.json` and `transcript.json` to `transcript.before_update3.json` if not already done. **Move** (do not delete) `tts/`, `fitted/`, `vi.wav`, `schedule.json`, and `output.vi.srt` into `old_before_update3/` in the job directory, because segment ids may have changed and `tts/<id>.mp3` and `fitted/<id>.wav` are named by id. Keep the TTS hash cache.
2. Apply the model the user chose, exactly as they said, in `config.yaml`.
3. Rerun stages 3, 4, and 5 with `--force`, then stages 6 and 7 with `--force`, writing to `sample/output/test1_v3.mp4`. Use the same mix settings as before unless the user changed them in `config.yaml`.

Then verify:

1. Verify with `ffprobe`: one H.264 video stream and one AAC audio stream, resolution and frame rate equal to the input's, duration within 1 second of the input's.
2. Rerun the same command without `--force`: finished stages must skip and `test1_v3.mp4` must be unchanged.
3. Confirm `test1.mp4` and `test1_v2.mp4` still exist and are unchanged.
4. Run the whole offline test suite and report the counts (passed, failed, skipped, and why).
5. **STOP POINT 2.** Report and wait for the user's feedback.

If any check fails, say which one and what you tried. Do not report success.

---

## 11. Report back

Follow `AGENT.md` section 12 and `AGENT_UPDATE.md` 7.2. Include:

1. Files changed, commands run.
2. Task 1: the per-cue table of text-block height (% of frame), bottom-edge distance, and the PNG paths.
3. Task 2: measured speech-rate distribution, the silence measurement and decision, the chosen `chars_per_sec`, and the before/after `fit_status` counts with the overflow ids.
4. Task 3: shift statistics and the ids marked `overlap: true`.
5. Task 6: the TTS failure summary.
6. Task 7: how many duplicate removals (ids and removed words); `build_units` statistics; whether a brief and a glossary were used (and if not, say so); and a table for the first 10 segments with the English, the **old** Vietnamese (from `translated.before_update3.json`), and the **new** Vietnamese. Do not say which is better; the user decides.
7. Task 8: the path of the comparison file, which models ran and which were skipped (and why), with the source and date for the model names.
8. Total run time and API usage (token counts if available).
9. What you could not verify: how the dubbing sounds, how the subtitles look, whether the translations are better, and whether the voice is in sync with the speakers (shifted clips can lag the video by up to `max_shift_s`).
10. Any other problems you noticed but did not fix.
11. The review zip path and size (new zip at the end of this session, and one at each stop point).

Then stop and wait for the user's feedback. Likely next topics: tuning `chars_per_sec`/`tts_rate`, the ducking mode, Demucs for a cleaner background, or a different TTS service. Do not start any of them unless asked.
