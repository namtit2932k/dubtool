# REVIEW_NOTES.md

Session: 2026-10-25 (second session). Task: AGENT_UPDATE_2.md — keep the
original audio under the dub, burn Vietnamese subtitles.

## What changed this session

- `src/dubtool/audio_utils.py`: `probe_audio_format`, `probe_video_info`,
  `has_audio_stream`, `make_silence(..., channels=)`, and the new mixer —
  pure `build_mix_graph()` (static + duck graphs, `amix normalize=0`,
  closing `alimiter`) and `mix_original_with_dub()` which writes
  `filter_complex_script` and runs FFmpeg with the job dir as cwd using
  relative names.
- `src/dubtool/stages/s6_mix.py`: mixes the Vietnamese voice on top of the
  lowered original audio taken straight from `input.mp4` (not the mono ASR
  wav); writes `output.nosubs.mp4`; falls back to dub-only if the input
  has no audio stream; the background track is a parameter (Demucs swap
  later needs a one-line change).
- `src/dubtool/stages/s7_subtitles.py` (new): writes `output.vi.srt`
  (cue timing per spec) and burns it with libass: white text, black
  outline, bottom center, `fontsdir=fonts` with a copied Arial from the
  Windows fonts dir, relative paths only, temp output `output.tmp.mp4`
  then atomic replace. `--no-burn` copies the video stream unchanged.
- `src/dubtool/subtitles.py` (new): `compute_cue_end` (cue end =
  max(end, start + fitted duration), capped 0.05 s before next cue,
  1.0 s minimum where allowed), `wrap_text` (2 lines / ~42 chars),
  SRT rendering, font discovery.
- `config.yaml`: new `mix:` and `subtitles:` blocks exactly as specced;
  `config.py` mini-parser generalised to any nesting depth (mix.duck.*).
- CLI: `--mix-mode static|duck`, `--original-gain-db`, `--dub-gain-db`,
  `--no-burn` on `run` and `stage`.
- `job.py`: s7 in STAGE_ORDER; `output.nosubs.mp4`, srt, fonts, check
  paths.
- Tests: 84 offline total — mix-graph/duck command construction, burn
  filter style, synthetic-tone FFT mix test (440 Hz lowered ~14 dB
  ±1.5 dB, 880 Hz dub unattenuated, no clipping), SRT timing/wrapping.

## Verified on sample/input/test1.mp4 (216.9 s)

Reran ONLY stages 6 and 7 with `--force` on job test1-e5df32ffda (no
Gemini/TTS calls). Output: `sample/output/test1_v2.mp4`.

| Check | Result |
| --- | --- |
| ffprobe streams | 1 video (h264 1920x1080@60) + 1 audio (aac); duration matches input within 0.05 s |
| volumedetect (full) | mean -18.3 dB, max -2.0 dB (no clipping, limit headroom) |
| Original present in gap 188.9–190.0 s | output mean -67.0 dB vs input mean -53.3 dB → -13.7 dB (configured -14 dB; this stretch is near-silence in the input) |
| Dub on top (10–20 s) | output mean -18.5 dB vs gap -67.0 dB → voice clearly present; dub passes at 0 dB (unit-test verified) |
| Frames vs un-burned base | strong-diff pixels: cue t=1.0 → 46.7% bottom third; t=32.9 → 37.1%; t=117.5 → 97.1%; no-cue t=189.9 → 0 changed pixels |
| Old output untouched | test1.mp4 mtime unchanged |
| Rerun without --force | both stages skip, test1_v2.mp4 byte-identical |

Check frames: `jobs/test1-e5df32ffda/check/` (cue_*.png / ref_*.png /
nocue_189.9.png). First cue contains ơ, ạ, ệ, ỉ diacritics.

## Deliberate deviation from spec

The burn command uses `-c:a copy` instead of `-c:a aac -b:a 192k`:
s6 already encoded the audio to AAC 192k, and re-encoding a lossy stream
would only degrade it. Video flags follow the spec (libx264, crf 18,
preset medium, yuv420p, +faststart).

## Not verified

Listening quality (mix balance, duck behaviour) and subtitle readability
/ diacritic rendering: cannot be judged by an automated check. The user
must listen to test1_v2.mp4 and inspect the PNGs. Duck mode is
implemented and unit-tested but the sidechaincompress starting parameters
have not been tuned by ear.

## Files

- Output: `sample/output/test1_v2.mp4` (105 MB) vs `sample/output/test1.mp4` (70 MB),
  both present, neither overwritten.
- Log: `jobs/test1-e5df32ffda/pipeline.log`.

## Session: AGENT_UPDATE_3.md — stop point 1 (implement + test + compare, no rerun)

Third update done except the single Phase B rerun (stages 3-7 with the
model the user chooses). All 150 offline tests pass.

Implemented (tasks 1-8, code only — see below for Phase A runs):
- Task 1 subtitles: `px_to_ass_units` conversion (libass 288-line virtual
  frame), font-family→file map with fallbacks, long-cue splitting (2x
  max_line_chars), `subtitles.max_line_chars` (42 / 28 portrait); render
  test measures real pixels at 1920x1080, 1280x720 and 1080x1920.
- Task 2 speech rate: measured from job test1-e5df32ffda (82 segments):
  median 9.98, p25 8.03, p75 11.27 chars/s -> `chars_per_sec: 8.0`.
  Silence: avg 1.148 s lead+tail per clip (10-clip sample) -> trim on
  stage 4 with marg (threshold -35 dB), cache key includes trim; `tts_rate` config (+0% default).
- Task 3 schedule: `schedule.py` (pure, deterministic), stage 6 places
  clips at play_start, stage 7 builds cues from play_start/play_end.
- Tasks 4-6: s5 restores vi/tts_duration when TTS fails after a shorten;
  lazy LLM; --force always regenerates a processed fitted clip; TTS
  failures logged to tts_failures.json with id/chars/attempt/error/elapsed
  and stats-only summaries.
- Task 7: prompts rewritten (accuracy-first, neutral register, soft
  budget, reaction/subject rules, brief block, 3 generic examples);
  sentence-unit translation (units.py -> units.json, split back per
  segment, unit skipped if any member reviewed); ASR dedupe of boundary
  repeats + `s2 --postprocess-only`; `asr.condition_on_previous_text:
  false`; `--brief` / `--brief-file` (never invented; none provided).
- Task 8: `dubtool compare-translate` writes one .md per run with one
  column per model, per-unit char counts, tokens and time; no scoring.

Phase A on the real job (test1-e5df32ffda):
- `s2 --postprocess-only`: 1 removal — segment 6 start: 'The answer is
  always yes.' (5 words). 82 segments remain. transcript.json backed up
  to transcript.before_update3.json first.
- build_units: 68 units, 12 with 2+ segments, longest 10.08 s (segs 11+12).
- compare-translate units 0-19: gemini-3.5-flash-lite (1711 in / 884 out
  tokens, 3.0 s) vs gemini-3.8-flash (1711 in / 840 out, 163.8 s). Both
  free-tier per ai.google.dev docs checked 2026-10-25. Report:
  jobs/test1-e5df32ffda/compare/translation_compare_20261025-200118.md.
- No brief, no glossary provided (nothing invented for the user's video).
- fit_status before rerun: ok 13, stretched 36, shortened 2, overflow 31
  (overflow ids listed in the report below).

Phase B (the single rerun stages 3-5 then 6-7 to sample/output/test1_v3.mp4)
is waiting on the user's model choice.

- Task 1 real-job check (new sizing burned onto output.nosubs.mp4 into a
  temp file, deliverables untouched; script scripts/check_subtitle_frames.py):
  font 45 px -> 12.00 units, margin 60 px -> 16.00 units, outline 2.0 px ->
  0.53 units (Arial). Measured over 5 unique burned cues (incl. the longest
  cue and one with diacritics): text-block height 3.4-8.7% of frame height,
  bottom edge 5.4-6.2% of frame height above the frame bottom. All cues are
  under the 15% failure threshold. PNGs:
  jobs/test1-e5df32ffda/check/task1_t*_burned.png and task1_t*_ref.png.
  Still automated-only: how the subtitles LOOK is for the user to judge.

## Session: AGENT_UPDATE_4.md — STOP POINT 1 (Part A finished + desktop app UI + screenshots)

Parts done: A1 recalibration, A2 pronoun prompts, A3 concurrency + latency check,
Phase B fast run complete, Phase B accurate run complete, then Part B sections
6-9 UI code + 12 tests. Packaging (sections 10-11) NOT started — waiting for
the user's UI feedback per the spec's stop point.

### Part A (measured)
- `chars_per_sec: 12.5` (was 8.0) — p25 of trimmed speech rates (see config.yaml
  line 5 comment; median 15.02, p75 17.31, min 7.81, max 20.36 chars/s on 82
  trimmed clips; ~0.94 s avg silence removed per clip).
- Pronoun defaults (tôi / các bạn / bạn) in translate + shorten prompts,
  overridable via `translate.pronouns` and the brief; no gendered second person.
- `translate.concurrency` added (default 1; accurate mode runs 3) with 429
  back-off that reduces concurrency and preserves output order (fake-provider
  tests in tests/).
- Phase B fast: stages 3-7 rerun → `sample/output/test1_v3_fast.mp4`
  (140 MB). Phase B accurate: → `sample/output/test1_v3_accurate.mp4`
  (~105 MB). test1.mp4 / test1_v2.mp4 untouched.
- Multi-key support added for the user's extra keys: `GEMINI_API_KEY_2/3`
  rotate automatically on 429 (`llm/gemini.py` + `llm/client.py`). Two
  rotations happened during the accurate s3 run; values never logged.

### Timing report (AGENT_UPDATE_4.md 4.4)
Written to `jobs/test1-e5df32ffda/timing_report.md` (script
`scripts/build_timing_report.py`). Highlights:
- Fast: enforcement flagged 69/82 raw-over-budget; final over budget 45/82
  (median 1.04); fit ok 34 / shortened 24 / stretched 24; s3 15.2 s, s4 175 s,
  s5 102.2 s; tokens 15,912 in / 5,673 out.
- Accurate: enforcement flagged 42/82 but the pass FAILED (invalid JSON from
  the model after 3 attempts) and was left as-is — s5 handled the rest.
  Final over budget 38/82 (median 1.00); fit ok 34 / shortened 17 / stretched
  30 / overflow 1; s3 551.7 s wall (incl. two 30 s rate-limit rotation waits;
  ~490 s model time), s4 129.7 s, s5 90.5 s; tokens 20,277 in / 3,915 out.
- Schedule: fast shifted 24 clips (max 0.08 s); accurate shifted 34 (max
  0.55 s, total 1.78 s); neither run overlaps Vietnamese clips (0 s), end
  lag 0.00 s in both.
- First-10 EN/old/Fast/Accurate table in the report file; no scoring given.

### Desktop app (Part B, from source)
- `src/dubtool/api.py`: `run_dub()` library entry (mode resolution from
  `packaging/modes.yaml`, stable job id for resume, cancel between segments
  and stages, atomic output delivery (+`_vi.mp4`), never overwrites silently,
  `output_exists` result for the dialog, `keep_intermediate=False` deletes
  everything but the log).
- `src/dubtool/app/`: paths, settings (no secrets in settings.json), secrets
  (keyring + env fallback), modes (fast/accurate from modes.yaml), i18n
  (vi default, en equal keys), service (worker thread, progress, cancel),
  errors (friendly messages + Copy details), diagnostics (redacted zip),
  selftest, `--version / --selftest / --screenshots / --headless-run` flags.
- `src/dubtool/app/ui/`: main window (drop zone, video info, output folder
  with disk check, brief with 1000-char counter, options panel, two generate
  buttons with honest tooltips, 7-stage progress list with icons, cancel,
  resume/start-over, open/play, menu), settings dialog (key field + save/
  test/delete, language, whisper model/device, advanced model IDs), first-run
  privacy notice, widgets.
- Screenhots (no-key, from source): `screenshots/01_empty.png` …
  `08_first_run.png`. Generated with `QT_QPA_PLATFORM=windows` because the
  offscreen platform has no Vietnamese font (tofu) — the packaged exe keeps
  real fonts, so this is a screenshot-tool only issue.

### Bugs found & fixed this session
1. `audio_utils.probe_video_info` returns no `has_video` key → the GUI's
   "no video stream" check treated every valid video as invalid. Fixed to
   check `codec_name` (`app/ui/main_window.py`).
2. i18n typo: Whisper device row read "Charged thiết bị" → "Thiết bị Whisper".
3. Stage list too short to show all 7 rows → minimum height added.
4. s4/s6/s7 skip-vs-done hazard: after a failed accurate run the stale
   state.json marked s4/s6/s7 done and stage reruns skipped them silently
   (the first v3_accurate copy was built from stale fast artifacts). Fixed by
   rerunning s4-s7 with --force; hash-verified the two outputs now differ
   (md5 3d24e851… vs 229227f8…), accurate duration 216.87 s matches input.
5. `--force` on stage 4 and later must always be used when re-running after a
   failed run — the "start over" flag exists in the UI for this.

### Tests
185 passed, 0 failed, 0 skipped (`py -m pytest tests/ -q`). New test files for
settings/secrets/modes/service/i18n/UI state machine/API refactor are in
tests/test_app_modules.py, tests/test_api_and_service.py, tests/test_app_ui.py.

### Not verified (cannot be judged automatically)
- How the UI looks/feels on the user's display and DPI.
- Listening quality of either dub; translation quality — user decides.
- Keyring behavior under the packaged exe (packaging not started).
- Windows SmartScreen / antivirus behavior (unsigned exe not yet built).
