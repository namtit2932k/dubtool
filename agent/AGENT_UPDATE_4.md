# AGENT_UPDATE_4.md — Finish the pipeline fixes, then build the DubTool Windows desktop app (.exe)

Fourth update. `AGENT.md`, `AGENT_UPDATE.md`, `AGENT_UPDATE_2.md`, and `AGENT_UPDATE_3.md` still apply (ground rules, data contracts, resumability, secret safety, the review zip rule in `AGENT_UPDATE.md` 7.2, the reporting format). This file overrides them only where it says so.

The user reviewed the Update 3 stop-point-1 package. Decisions made by the user:

- Subtitle position and size are now acceptable.
- **Both translation models stay**, as two modes of the final app: **Fast** = `gemini-3.5-flash-lite`, **Accurate** = `gemini-3.8-flash`. (The user found the first faster and the second more accurate.)
- The next goal is a **complete Windows desktop application**, packaged as an `.exe`, with: a place to import a video, a place to choose the output folder, two buttons to generate (Fast / Accurate), and a Settings screen where the user types the Gemini API key.

The work has two parts. **Part A** (sections 1–4) finishes the pipeline: some findings from the review must be fixed first because the app wraps this pipeline. **Part B** (sections 5–13) builds the app.

---

## 0. Findings from the review that this update responds to

1. **`chars_per_sec: 8.0` was calibrated on untrimmed clips and is now stale.** The speech rate was measured before silence trimming (median 9.98, p25 8.03 characters per second on clips that carry about 1.15 s of leading and trailing silence each). Trimming was then added, but the value was not re-measured. True speech rate is higher, so the budgets are too tight and the models are pushed to compress more than needed.
2. **Both models ignore the budget.** In the 20-unit comparison, 0 of 20 units were within 1.15× of `max_chars` for either model (median 2.48× for Fast, 2.18× for Accurate). Part of that is finding 1. The rest is real: a faithful Vietnamese translation of fast English speech is about as long (in characters) as the English, while Vietnamese TTS speaks slower than this speaker. Expect many segments to need shortening, stretching, or lagging in Phase B. This must be **measured and reported**, not assumed away.
3. **Schedule cap behavior.** When the cumulative shift passes `max_shift_s`, `schedule.py` sets `play_start = orig_start + max_shift_s` and marks `overlap: true`, which means Vietnamese voices **do** overlap there. With heavy overshoot this can cascade. It must be visible in the timing report.
4. **Pronoun and audience inconsistency across units**, in both models (for example "chúng tôi" and "chúng ta" for the same speaker; Accurate adds "cô" for "you" in several units although the source gives no gender). This violates "do not add information not in the English".
5. **Terminology**: Fast used "đoạn cắt" for "clip", "giảm thiểu" for "reduce", and "đáng tiếc" for "Shame" (should convey shame); Accurate used "chế độ phụ hệ" for "patriarchy" and produced an ungrammatical sentence in unit 3 ("liệu mình có không nên bỏ... vì liệu mình có..."). Neither model is uniformly better; the glossary and brief exist to fix terminology, and the user decides.
6. **Accurate-mode latency**: one 20-unit call took 163.75 s against 3.01 s for Fast, with almost the same token counts. The cause is unknown (thinking, queueing, rate limiting?). The app's Accurate mode cannot ship without understanding this: a 20-minute video is roughly 19 such calls.

---

## 1. Rules for this update

1. Part A changes only what section 2–4 list. Part B adds the app and a thin refactor of the pipeline entry points. Nothing else changes.
2. The CLI keeps working exactly as before (all existing commands, flags, and tests). The app and the CLI call the same code.
3. Never print, log, or write the API key anywhere except the secret store described in section 8. No key in `settings.json`, logs, exceptions, screenshots, review zips, or reports. Never read `.env` into output.
4. Do not invent content for the user's video (no brief, no glossary terms).
5. Report only what you measured. State what you cannot judge (how it sounds, translation quality, how the UI feels on the user's screen).
6. Do not commit binaries (`ffmpeg.exe`, fonts, models, build output). Keep them out of Git and out of the review zip.
7. Produce a review zip at each stop point and at the end (`AGENT_UPDATE.md` 7.2); extend its allowlist to include the new app source folders and `packaging/`, still excluding binaries, build output, and media.

---

## 2. Part A1 — Re-calibrate `chars_per_sec` on trimmed clips (cheap)

1. Using the existing job's current `translated.json` (do not change any text), regenerate the TTS clips with trimming on (this calls edge-tts only; no LLM calls). Record `tts_duration` after trimming.
2. Compute characters of `vi` per second of **trimmed** clip for all segments. Report median, p25, p75, count, and the average silence per clip before and after trimming.
3. Set `chars_per_sec` to the **25th percentile** of the trimmed rates, rounded down to the nearest 0.5, with the measured numbers in a comment. Confirm trimming never cuts a word (check a sample of 10 clips by comparing the first and last 50 ms of each trimmed clip against silence-detect output, and report; you cannot listen).
4. Update `tests` if any assumed the old value.

Check: the report shows the old and new numbers and the new `chars_per_sec`.

---

## 3. Part A2 — Prompt additions: pronoun consistency and no assumed gender

In `llm/prompts.py` (translate and shorten prompts):

- Choose **one** first-person pronoun and **one** second-person form per video and keep them throughout. Defaults: first person "tôi", second person "các bạn" (plural "you") or "bạn" (singular "you"). The brief can override (the user may state the speaker's role, who the audience is, and which pronouns to use). Pass the choice into every chunk so chunks stay consistent.
- Do **not** assume the gender or relationship of "you" (no "anh", "cô", "chị") unless the brief says so.
- Prefer established Vietnamese terms over calques, and use the glossary exactly (the user will add entries such as `patriarchy`).
- Add config `translate.pronouns` (`first: "tôi"`, `second_plural: "các bạn"`, `second_singular: "bạn"`) and let the brief override it.

Checks: prompt tests (pronoun block present with defaults; overridden by brief; no gendered second-person default).

---

## 4. Part A3 — Translation concurrency, latency check, then Phase B for both modes

### 4.1 Concurrency (translation only)

Translation chunks use English-only context, so they are independent. Add `translate.concurrency` (default 1). The app will set 3 for Accurate mode. Rules: preserve output order and ids; on HTTP 429 back off (honoring retry-after) and reduce concurrency by one for the rest of the run (never below 1); a failed chunk is retried under the existing retry rules; resume and `--force` semantics unchanged. Tests with a fake provider: order preserved, a 429 reduces concurrency, one failed chunk does not lose the others.

### 4.2 Latency investigation for `gemini-3.8-flash` (measure, do not change defaults)

On the same fixed 20 units used in the comparison, call the model 3 times and report per call: total seconds, input/output tokens, any "thinking"/reasoning token count the API reports, retries or waits (429), and whether time scales with output tokens. Then check the official Gemini documentation for a documented way to limit or disable thinking for this model; if one exists, run the same 20 units once with it and report time and whether the Vietnamese differs (show both, do not judge). Do **not** change defaults; the user decides.

### 4.3 Phase B for both modes

Add `--model <id>` to `dubtool run` and `dubtool stage` (overrides the provider's configured model). Then, on the existing job, in this order (back up per `AGENT_UPDATE_3.md` rule 2 and move derived folders to `old_before_<mode>/` between runs; keep the TTS hash cache):

1. **Fast**: rerun stages 3–7 with `--force --model gemini-3.5-flash-lite` → `sample/output/test1_v3_fast.mp4`.
2. **Accurate**: rerun stages 3–7 with `--force --model gemini-3.8-flash --translate-concurrency 3` → `sample/output/test1_v3_accurate.mp4`.

Keep `test1.mp4` and `test1_v2.mp4` untouched. If a free-tier limit is hit, wait and rerun the same command.

### 4.4 Timing report (per mode, mandatory)

For each run report, as small tables:
- Characters over budget after stage 3, before and after the enforcement pass (count and median ratio of `len(vi)/max_chars`).
- `fit_status` counts after stage 5, and the number of LLM shorten calls and TTS regenerations stage 5 needed.
- Schedule statistics: segments shifted, maximum shift, total shift, ids marked `overlap: true`, and the **seconds of Vietnamese speech that overlap** other Vietnamese speech (computed from `schedule.json`).
- Maximum lag of any clip behind its original start, and the lag at the end of the video.
- Stage times and total time; token counts per stage.
- The first 10 segments: English, old Vietnamese (from `translated.before_update3.json`), Fast Vietnamese, Accurate Vietnamese. Do not say which is better.

Then continue to Part B without stopping. The user will listen to both outputs and may later change the timing strategy; keep timing behavior behind config so that is possible without touching the app.

---

## 5. Part B — The desktop app: goals and non-goals

**Goals (what the user asked for):**
1. Import a video (file dialog and drag-and-drop).
2. Choose the output folder.
3. Two buttons that generate the dubbed video: **Generate Fast** and **Generate Accurate**, mapped to the two models.
4. A Settings screen with a field for the Gemini API key.
5. A Windows `.exe` the user can run without installing Python.

**Non-goals (do not build):** macOS/Linux builds, installers (zip of a folder is enough), auto-update, telemetry, accounts, a built-in subtitle/translation editor, voice cloning, batch queues, cloud features, Demucs. Mention any of these only as future ideas in the report.

---

## 6. Architecture

### 6.1 Technology

- **GUI: PySide6 (Qt).** Native file dialogs, drag and drop, signals/slots for worker threads, offscreen rendering for screenshots and tests. If you believe another toolkit is clearly better for this job, stop and ask the user with a short reason before switching.
- **Secrets: `keyring`** (Windows Credential Manager).
- **Packaging: PyInstaller, one-folder (onedir), windowed (no console).** Not one-file (slow start, more antivirus false positives). No UPX.
- Python version: the one already used by the project; state it in the README.

### 6.2 Layout

```
src/dubtool/
  api.py                 # run_dub(...): the single library entry point used by CLI and app
  app/
    __init__.py
    main.py              # app entry (also handles --selftest, --version, --screenshots, --headless-run)
    paths.py             # app data dirs, bundled resource lookup (works frozen and from source)
    settings.py          # settings.json load/save, defaults, validation (no secrets)
    secrets.py           # keyring wrapper, env var fallback
    modes.py             # Fast / Accurate definitions loaded from modes.yaml
    i18n.py              # vi (default) and en strings
    service.py           # PipelineService: runs api.run_dub in a QThread, emits progress, cancel, resume
    errors.py            # maps exceptions to friendly, localized messages
    diagnostics.py       # export redacted diagnostics zip
    selftest.py
    ui/
      main_window.py
      settings_dialog.py
      first_run.py       # privacy notice + model download progress
      widgets.py         # drop zone, stage list, log view
packaging/
  modes.yaml             # default mode → model mapping (shipped in the exe, user-overridable)
  build_exe.ps1          # one command build
  dubtool.spec           # PyInstaller spec
  THIRD_PARTY.md         # licenses and sources (FFmpeg, Qt, fonts, models)
  README_BUILD.md
vendor/                  # gitignored: ffmpeg.exe, ffprobe.exe, fonts
```

### 6.3 `api.py` — one entry point

Refactor the CLI's `run` path into a library function without changing behavior:

```python
def run_dub(
    video: Path, output_dir: Path, *, mode: str,              # "fast" | "accurate"
    options: DubOptions, secrets: SecretsProvider,
    progress: Callable[[ProgressEvent], None],
    cancel: threading.Event,
    jobs_dir: Path | None = None,
) -> DubResult: ...
```

- `ProgressEvent`: `stage` (s1…s7 or `download_model`), `state` (`start|progress|done|error|skipped`), `done`/`total` counters (segments, units, chunks), `message`, `elapsed_s`, `eta_s` (best effort).
- `cancel` is checked between items (segments, units, chunks, TTS calls), between stages, and while waiting on retries. On cancel: stop FFmpeg subprocesses, leave the job resumable, return a `cancelled` result.
- Job directory defaults to `%LOCALAPPDATA%\DubTool\jobs\<job_id>`, where `job_id` derives from the video path, size, modified time, mode, and the options that affect output. Re-running the same inputs resumes; a "start over" option uses `--force` semantics.
- The final video is copied to the chosen output folder as `<video name>_vi.mp4`, written to a temp name and renamed atomically. If the target exists, return a result code that lets the UI ask (overwrite / use `_vi_2` / cancel); never overwrite silently.
- `secrets.get_gemini_key()` is passed in; the pipeline must not read `.env` or any file in frozen mode.
- The CLI becomes a thin wrapper over `run_dub`; existing tests keep passing.

### 6.4 Modes (`modes.yaml`)

```yaml
fast:
  label_key: mode.fast
  provider: gemini
  model: gemini-3.5-flash-lite
  translate_concurrency: 1
accurate:
  label_key: mode.accurate
  provider: gemini
  model: gemini-3.8-flash
  translate_concurrency: 3
```

Settings → Advanced lets the user edit the two model IDs and reset them to the shipped defaults. If the API reports an unknown or unavailable model, show a clear message that points to Settings → Advanced. Do not hardcode model IDs anywhere else.

---

## 7. The main window (Vietnamese by default, English available)

Single window, clear and uncluttered:

1. **Video**: a drop zone and a "Chọn video…" button (filters: mp4, mkv, mov, webm, avi, m4v). After selection show file name, duration, resolution, frame rate, size (via ffprobe). Reject files with no video stream with a friendly message. Warn (not block) above 30 minutes.
2. **Output folder**: a read-only path field and a "Chọn thư mục…" button. Remember the last folder. Validate that it exists and is writable, and that free disk space is at least 3× the input size; warn otherwise.
3. **Video description (optional)**: a multi-line box, max 1,000 characters with a counter, placeholder explaining that topic, speaker, tone and audience improve the translation. Saved per video path; passed as the brief.
4. **Options** (collapsed panel): burn Vietnamese subtitles (default on), also save a `.srt` file next to the output (default off), original-audio level slider (−6 to −24 dB, default −14), voice (the two Vietnamese edge-tts voices), keep intermediate files (default off; when off, delete the job folder after success but keep the log).
5. **Two buttons**: **"Tạo nhanh"** and **"Tạo chính xác"** (labels in `i18n`). Tooltips say what each mode uses and give honest expectations: Fast is quicker; Accurate is slower and in the 20-unit test took about 160 s for one request versus about 3 s for Fast (state this as a measured example, not a promise).
   - Both are disabled until a video and an output folder are chosen. If no API key is set, clicking opens Settings with an explanatory message instead of failing later.
   - On the first Generate, show the one-time privacy notice (section 9.2).
6. **Progress area** (visible while running and after): the seven stages as a list with state icons, one overall progress bar plus the current stage's counter (for example "Dịch: 3/5 đoạn"), elapsed time and a best-effort ETA, a collapsible log view, a **Cancel** button, and after success: **Open output folder** and **Play video** buttons. A cancelled or failed run offers **Resume** and **Start over**.
7. **Menu / gear**: Settings, Export diagnostics, About (version, FFmpeg version, licenses link).
8. The window stays responsive at all times. Closing during a run asks for confirmation and cancels cleanly.

Errors appear as a dialog with a short human message, a "Copy details" button, and a "Export diagnostics" button. Never show a stack trace by default.

---

## 8. Settings and the API key

**Settings dialog** (modal):
- **Gemini API key**: password field with a show/hide toggle, a **Save** button, a **Test key** button, a **Delete key** button, and a plain line of text telling the user where to get one (Google AI Studio). Do not display the key anywhere after saving; show only "Key saved" and, optionally, the last 4 characters.
- **Test key**: a minimal request with a short timeout. Map results to messages: valid; invalid key; network unreachable; quota or rate limit; unexpected error (with details copyable, key never included).
- **Language**: Tiếng Việt / English.
- **Whisper model size** (default `small`) and device (auto/CPU/GPU, default auto).
- **Advanced**: the two model IDs (with Reset), jobs folder location, default output folder.

**Storage rules:**
- The key lives only in Windows Credential Manager via `keyring` (service name `DubTool`). If no keyring backend is available, show a clear message and fall back to the environment variable `GEMINI_API_KEY` (read-only). **Never** store the key in a plaintext file, in `settings.json`, or in the job directory.
- `settings.json` lives in `%APPDATA%\DubTool\` and contains no secrets.
- Logs, diagnostics, screenshots, and exceptions must never contain the key. Add a test that saves a fake key, runs a mocked pipeline with an error, and then searches the entire app data folder, logs, and diagnostics zip for the fake key (a quiet exit-code-only search). It must find nothing.

---

## 9. First run, models, and notices

1. **Whisper model download.** The first run needs the Whisper model (not bundled; too large). Download it to `%LOCALAPPDATA%\DubTool\models` with a progress dialog, retry on failure, offline detection with a clear message, and an option to cancel. Show the model size before downloading. If the download tool in use cannot report progress, show an indeterminate bar and say so.
2. **Privacy notice (one time, with "I understand" checkbox, re-readable from About).** State accurately what leaves the computer: the audio and video stay on the computer (speech recognition runs locally); the **English transcript text** is sent to Google (Gemini) for translation, and on the free tier Google may use submitted content to improve its products; the **Vietnamese text** is sent to Microsoft's speech service (through edge-tts, an unofficial interface that can be rate-limited or change) to create the voice. Do not claim more or less than this.
3. **Dependencies check at startup**: bundled FFmpeg/ffprobe present; subtitle font present; a clear message if anything is missing (should never happen in a good build).

---

## 10. Fonts and FFmpeg inside the app

- **FFmpeg/ffprobe**: bundle `ffmpeg.exe` and `ffprobe.exe` from an official Windows build that includes `libass` and `libx264`. Verify with `ffmpeg -filters` (must list `subtitles`, `amix`, `alimiter`, `sidechaincompress`) and `ffmpeg -encoders` (must list `libx264`). Verify the download's checksum against the publisher's, record the exact version and source URL in `packaging/THIRD_PARTY.md`, and keep the binaries in `vendor/` (gitignored). Note in `THIRD_PARTY.md` that a GPL FFmpeg build carries GPL obligations if the app is ever redistributed.
- **Subtitle font**: bundle an open-license font that supports Vietnamese (for example Noto Sans, SIL OFL) with its license text, and make it the default for burned subtitles. Keep the existing Windows-font lookup as an optional alternative (`subtitles.font`). The font file is copied into the job's `fonts/` as before so libass finds it; re-run the pixel-measurement render test with the bundled font and report the text-block height as a percentage of frame height.
- Resolve all resource paths with `paths.py` so the same code works from source and from the frozen build (`sys._MEIPASS` / the install folder).

---

## 11. Packaging and the `.exe`

- `packaging/build_exe.ps1`: one command that creates a clean virtual environment, installs pinned dependencies, runs the tests, builds with PyInstaller (`dubtool.spec`), copies FFmpeg and the font, writes `THIRD_PARTY.md` into the output, and produces `dist/DubTool/DubTool.exe` plus `dist/DubTool-<version>-win64.zip`. Fail clearly at each step.
- Collect what PyInstaller misses: data and binaries for `faster_whisper` (including its bundled VAD assets), `ctranslate2`, `onnxruntime`, `av`, `tokenizers`, `huggingface_hub`, `certifi` (TLS certificates, needed for Gemini, Hugging Face, and edge-tts), `edge_tts`, `keyring` backends, and the PySide6 plugins (platforms, imageformats, styles). Find the rest by running the built executable's self-test and end-to-end run, and fix them in the spec file; list every non-obvious fix in `README_BUILD.md`.
- Windowed build: when it has no console, write errors and logs to `%LOCALAPPDATA%\DubTool\logs\`. Add a version string, an application icon (create a simple one programmatically if none exists), and a version resource.
- Expected size is several hundred MB; report the real size of the folder and the zip.
- The executable is **unsigned**. Say in `README_BUILD.md` that Windows SmartScreen or antivirus may warn, and that the user can choose "More info → Run anyway" for their own build; do not suggest disabling security software.

### 11.1 Built-in checks usable from the packaged exe

Implement these flags in `app/main.py`, usable from source and from `DubTool.exe`:

- `--version`: prints the app version and the bundled FFmpeg version.
- `--selftest [--json out.json]`: no network required. Verifies: FFmpeg and ffprobe run; `libass` and `libx264` available; the bundled font renders Vietnamese (burn the sample string "Chào mừng bạn, ắ ữ ề ộ ỵ" onto a generated frame and report the measured text-block height as a percentage of frame height); `keyring` backend available (report only the backend name); model and jobs folders writable; `faster_whisper`, `ctranslate2`, `av` import; Qt creates an offscreen window with the main window class. Exit code 0 only if everything passes; print a table.
- `--screenshots <dir>`: renders the main window and the Settings dialog **offscreen** and saves PNGs for these states: empty; video selected; ready (video, folder, key set); running (stage list with progress, using a fake service); finished; error dialog; Settings. For the user's and the reviewer's visual check. The screenshots must not contain a key.
- `--headless-run <video> <outdir> --mode fast|accurate`: runs `api.run_dub` without a window, for automated end-to-end verification of the packaged exe. Uses the key from the keyring or `GEMINI_API_KEY`.

---

## 12. Tests

Offline, no key, no network; GUI tests with `pytest-qt` and `QT_QPA_PLATFORM=offscreen`.

- **Settings**: defaults, save/load round trip, validation of bad values, no secret fields.
- **Secrets**: save/get/delete with an in-memory fake keyring backend; env-var fallback is read-only; the "no key anywhere on disk or in logs" test from section 8.
- **Modes**: both modes load from `modes.yaml`, user edits override, Reset restores defaults.
- **Service** (with a fake pipeline): progress events arrive in order; Cancel stops within one item and leaves a resumable job; Resume continues; errors map to the right friendly messages (invalid key, no network, quota/rate limit, model not found, disk full, FFmpeg missing); output-exists returns the "ask" result and never overwrites.
- **UI state machine**: Generate buttons disabled without video or folder; clicking with no key opens Settings; the two buttons start different modes; while running both are disabled and Cancel is shown; after success the open/play buttons appear; closing during a run asks for confirmation.
- **i18n**: `vi` and `en` have the same keys; no hardcoded user-facing strings in `ui/` (a test that scans for string literals passed to widget text setters is acceptable).
- **API refactor**: `run_dub` and the CLI produce the same job files on a tiny fake pipeline; all existing tests still pass.
- **Concurrency** (section 4.1) and **prompt** (section 3) tests as listed there.

---

## 13. Staged delivery, verification, and stop points

### Stage 1 — Part A (sections 2–4)
Implement and test; run the re-calibration, latency investigation, and both Phase B runs; produce the timing report. No stop; continue.

### Stage 2 — Pipeline API and the app UI (sections 6–9, 12 except packaging)
Implement `api.py`, the service, settings, secrets, modes, i18n, and the windows. Run all tests. Produce the screenshots with `--screenshots` from source.

**STOP POINT 1.** Report, give the screenshot folder path, produce the review zip, and **wait for the user's feedback on the UI** before building the package.

### Stage 3 — Packaging (sections 10–11)
Build the exe and verify, reporting real output:

- [ ] `dist/DubTool/DubTool.exe --version` and `--selftest --json` pass (paste the table; no secrets).
- [ ] Run these from a different folder with a **sanitized environment**: `PATH` reduced to Windows system folders only (no Python, no FFmpeg) and a temporary profile directory (temporary `APPDATA` and `LOCALAPPDATA`), proving nothing depends on the development machine.
- [ ] `--screenshots` from the **packaged** exe works.
- [ ] `--headless-run sample/input/test1.mp4 <outdir> --mode fast` completes and produces `<outdir>\test1_vi.mp4`; same for `--mode accurate`. For each: `ffprobe` shows one H.264 video stream and one AAC audio stream, same resolution and frame rate as the input, duration within 1 s; the timing report from section 4.4 is produced; total time and token counts are reported. (If the Whisper model must be downloaded in the temporary profile, report the download time separately.)
- [ ] The folder and zip sizes are reported.
- [ ] No key found anywhere in the dist folder, logs, or temp profile (quiet search).
- [ ] State clearly what you could **not** verify: how the window looks and feels on the user's display and DPI settings, behavior on a clean Windows machine other than this one, SmartScreen behavior, listening quality of the dubbing, and whether the translations are good.

**STOP POINT 2.** Final report, review zip, and wait.

---

## 14. Report back

Follow `AGENT.md` section 12 and `AGENT_UPDATE.md` 7.2. Include:

1. Files added and changed; commands run; test counts (passed, failed, skipped, and why).
2. Part A: the re-calibration numbers, the pronoun/terminology prompt changes, the latency investigation table, the concurrency result, and the timing report for both modes (section 4.4), plus the paths of the two output videos.
3. Part B: the screenshot paths (Stop 1) or the packaging verification checklist with real outputs (Stop 2).
4. Sizes (exe folder, zip), the Python version, pinned dependency versions, and the FFmpeg version and source.
5. What you could not verify, stated plainly.
6. Anything you noticed but did not fix.
7. The review zip path and size.

Then stop and wait for the user's feedback. Likely next topics: the timing strategy (accuracy versus fitting the original timing), a translation review screen, an installer, or a different voice service. Do not start any of them unless asked.
