# AGENT_UPDATE_6.md — Fix the flashing ffmpeg console windows and the 100% CPU in the packaged app

Sixth update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_5.md` still apply (ground rules, data contracts, resumability, secret safety, the review zip rule in `AGENT_UPDATE.md` 7.2, the reporting format). This file overrides them only where it says so.

The user ran the packaged `DubTool.exe` from Update 5 and reports two problems during a run:

1. **Console windows of `ffmpeg.exe` flash on and off continuously**, which is very annoying.
2. **CPU usage reaches 100%.**

Both are fixed here. Fix 1 is a known cause with a known fix. Fix 2 has several plausible causes, so it starts with a **measurement**; do not guess.

The user then added three requests, all covered below:

3. **Every other error found in the review of the Update 5 package** is listed in section 0 (findings 8–15) and fixed in tasks 4–6.
4. **Clean up the project folder, which has grown to about 2 GB** (task 5).
5. **From now on, push the code to GitHub after every update** (section 8, a standing rule starting with this update).

---

## 0. Findings from reading the delivered code (verified in the source)

1. **No subprocess call hides its console window.** The source has no `creationflags`, `CREATE_NO_WINDOW`, or `startupinfo` anywhere. The packaged app is a windowed (no console) program; on Windows, every console program it starts (`ffmpeg.exe`, `ffprobe.exe`) then opens its own console window. During development the app ran from a terminal, where child processes reuse the parent's console, so this never showed. Call sites: `audio_utils.py` (six calls), `stages/s7_subtitles.py`, `app/ui/main_window.py` (`explorer`, `cmd /c start`, and an `ffprobe` call), `app/selftest.py`, `app/layout_check.py`, and the scripts.
2. **A launch storm.** Reading the code, one run of the 82-segment sample starts on the order of hundreds of short processes (my estimate; measure it): stage 4 runs `ffprobe` to validate every cached clip, an `ffmpeg` silence trim per new clip, and two more `ffprobe` calls per clip; stage 5 runs an `ffmpeg atempo` per stretched clip; stage 6 probes every fitted clip; and stage 7 (`subtitles.py`) probes every fitted clip again. Each launch is a flashing window, a process start, and (on most machines) an antivirus scan of a large `ffmpeg.exe`.
3. **The dependency `av` (PyAV) is already bundled** (faster-whisper needs it), so media durations and formats can be read in-process without starting any process.
4. **Cancel cannot stop a long encode.** The stage 6 and stage 7 `ffmpeg` calls are blocking `subprocess.run` calls, so Cancel only takes effect after the encode ends (verify whether any other mechanism exists, and report).
5. **Windowed apps have no valid standard input.** None of the `subprocess` calls passes `stdin=DEVNULL`, which is the standard safeguard against "handle is invalid" errors when there is no console.
6. **CPU: not measured yet.** Plausible contributors, all unverified: libx264 with `-preset medium -crf 18` using every core on 1080p60 video (stage 7); Whisper on the CPU with default threading (stage 2); the hundreds of process launches plus antivirus scanning (finding 2); and UI work per progress event (the log widget grows without a limit, and progress events are not throttled). No busy-wait loop or timer was found in the UI or the service by searching the code, so a true spin loop is not suspected, but it must be ruled out by measurement.
7. **Opening the output uses `cmd /c start`**, which itself flashes a console window.

**Other errors found in the Update 5 package** (findings 8–15):

8. **Accurate mode exhausts the free-tier daily quota (observed by the agent).** In the Update 5 accurate run, stage 5 stopped on `429 RESOURCE_EXHAUSTED` with quota id `GenerateRequestsPerDayPerProjectPerModel-FreeTier` (limit 20 requests per day for `gemini-3.8-flash` on the user's key, retry delay about 7 hours). Cause in the code: `s5_fit_duration.py` calls `llm.shorten(...)` once **per segment per attempt** (up to 3 attempts × many segments), and every call is one request. Stage 3 already has a one-request batch call (`shorten_batch`); stage 5 does not use it. Quotas can change; the numbers are what was observed.
9. **Quota state is tracked per key and per run only.** `KeyPool.mark_daily_limit` marks a key unavailable only for the process lifetime. The real quota is **per project and per model**, so: (a) a key exhausted for the Accurate model is still usable for the Fast model, (b) the next run retries the exhausted key and wastes a request and time, and (c) several keys from the same Google project share one quota, so rotating between them cannot help (only keys from different projects add capacity). Google's terms may restrict creating extra projects or accounts to get around quotas; the user should read them (this is not legal advice).
10. **Accurate mode is very slow.** Stage 3 took 965 s on the sample against 10.2 s for Fast. The cause is still unexplained.
11. **The enforcement pass does nothing in Accurate mode:** over-budget counts were 24 before and 24 after. Update 5 task 5.3 asked for the cause to be diagnosed, and the notes do not say what it was.
12. **Debug leftovers are in the delivered source:** `tests/_tmp_dbg.py`, `tests/_tmp_dbg2.py`, and `tests/build_log.txt` … `tests/build_log6.txt`.
13. **`.gitignore` is far too small for publishing, and the folder has grown to about 2 GB.** It does not ignore `.venv-build/` (the build environment created by `build_exe.ps1`, probably more than 1 GB), `shots*/`, `screenshots/`, `excerpts/`, `models/`, media files, `old_before_*/`, logs, archives, or probe folders. A push now would try to upload gigabytes. Also, `build_exe.ps1` reuses an existing `.venv-build` instead of creating a clean one (its comment says "clean venv"), so stale packages can leak into a build, and the dev `.venv` plus `.venv-build` double the disk use.
14. **The bundled FFmpeg is a development snapshot** (`N-118281-gb76053d8bf`, dated 2025-01-11 in the notes), not a release build, and almost two years old.
15. **The full test suite was not run as one aggregate after the last fixes** (the notes say so; individual files were green).

---

## 1. Rules for this update

1. Change only what is listed here. Do not alter translation, TTS, timing, subtitle, or layout behavior.
2. Never print, log, or write an API key. Never read `.env` into output.
3. Report only what you measured. State what you cannot verify (how the app feels on the user's machine, antivirus behavior).
4. Do not commit binaries. Keep build output and media out of the review zip.
5. Produce the review zip at the end (`AGENT_UPDATE.md` 7.2).
6. The packaged app stays windowed (`console=False`). Do **not** "fix" the flashing by turning the console on.
7. **Deletion is irreversible.** Any cleanup runs as a dry run first, deletes only what task 5 lists as regenerable, never touches anything outside the project root, and asks the user before deleting anything else (section 6.2).
8. From this update on, finish every update run with the cleanup report and the GitHub publishing procedure (section 8) before the final report.

---

## 2. Task 1 — Never show a console window: one subprocess helper for everything

Create `src/dubtool/proc.py` and route **every** subprocess call in `src/` and `scripts/` through it.

```python
def run(cmd, *, cwd=None, capture=True, timeout=None, check=False,
        low_priority=False, cancel=None) -> subprocess.CompletedProcess: ...
def popen(cmd, *, cwd=None, low_priority=False, **kw) -> subprocess.Popen: ...
```

Behavior on Windows:
- `creationflags = CREATE_NO_WINDOW (0x08000000)`, plus `BELOW_NORMAL_PRIORITY_CLASS (0x00004000)` when `low_priority=True`.
- A `STARTUPINFO` with `STARTF_USESHOWWINDOW` and `wShowWindow = SW_HIDE`.
- `stdin=subprocess.DEVNULL` always. Decode output with `encoding="utf-8", errors="replace"`.
- On other platforms, no Windows flags.

Cancellation: when `cancel` (a `threading.Event`) is given, run the child with `Popen` and poll (for example `communicate(timeout=0.2)` in a loop); on cancel, terminate the child **and its process tree** (use `taskkill /T /F /PID <pid>` through the same helper, hidden, or `psutil`), then raise the existing cancel exception. Use this for the long calls: the stage 6 mix, the stage 7 encode, stage 1 audio extraction, and the per-clip trim and stretch calls. Pass the job's cancel event down to them.

Also:
- Replace `cmd /c start …` and `explorer …` in `main_window.py` with `os.startfile(...)` on Windows (and keep `xdg-open` elsewhere), so opening the output folder or playing the video never creates a console. If "reveal the file in the folder" is needed, call `explorer /select,<path>` through the helper.
- Count every call in the helper (`proc.stats`: calls per executable name, and per stage via a context variable set by the pipeline). Log a per-stage summary line, for example `s4: 311 ffprobe, 82 ffmpeg`. This is how the improvement is measured.

### Checks for task 1
- [ ] A static test (AST or text scan) fails if any file under `src/` or `scripts/` other than `proc.py` calls `subprocess.` directly or `os.system`.
- [ ] Unit tests with a mocked `subprocess.Popen` (and `sys.platform` patched to Windows): the helper passes `CREATE_NO_WINDOW`, `SW_HIDE`, `stdin=DEVNULL`, and the priority class only when requested; no Windows flags on other platforms.
- [ ] A cancel test with a fake long-running child: the child (and its child) is terminated within about 1 second of the cancel event.
- [ ] **A real watcher test on Windows (section 5).**

---

## 3. Task 2 — Stop launching hundreds of processes

### 3.1 Read media information in-process
Reimplement `probe_duration`, `probe_audio_streams`, `probe_audio_format`, and `probe_video_info` with PyAV (`av.open`: container duration, stream sample rate and channels, video width, height, codec, and average frame rate). Keep `ffprobe` (through the helper) only as a **fallback** when PyAV fails.

Test: on a small set of generated files (an MP3 and a WAV made by ffmpeg, and a short MP4), the in-process results equal the `ffprobe` results (duration within 0.05 s, same sample rate, channels, width, height, frame rate). Skip with a message if `ffprobe` is not installed.

### 3.2 Probe each file once
Add a small media cache (memory plus `durations.json` in the job directory) keyed by relative path, file size, and modification time in nanoseconds, so each clip is read at most once per content version across stages 4, 5, 6, and 7. In stage 4 the cache-validation probe, the post-trim probe, and the final duration probe collapse into at most one read. Never trust a cached value if size or mtime changed. Test: probing the same unchanged file twice reads it once; changing the file invalidates the entry.

### 3.3 Cache capability probes
If any code checks FFmpeg capabilities at run time (for example whether `amix` supports `normalize`, or the filter list), cache the answer for the lifetime of the process (`functools.lru_cache`). Report whether any such probe was found.

### 3.4 Measure, then decide about the per-clip FFmpeg calls
After 3.1 and 3.2, run the sample and report the process launches per stage (from `proc.stats`), before (Update 5 behavior, measured with the helper counting only) and after. **Only if** the remaining count is above about 200 per run, move the per-clip silence trim and tempo stretch into in-process PyAV filter graphs (`av.filter.Graph` with `silenceremove` and `atempo`) and verify that the resulting clips match the FFmpeg-process results (duration within 20 ms; peak levels within 1 dB). If PyAV's bundled filters do not include what is needed, say so and stop at the measured count. Do not do this refactor if the count is already low.

---

## 4. Task 3 — CPU: measure first, then limit the heavy work

### 4.1 Measure (mandatory, before changing CPU behavior)
Add `--profile` to `--headless-run`. It samples once per second and writes `profile.csv` in the job directory with: elapsed seconds, current stage, the app process's CPU%, the CPU% of all its child processes, total system CPU%, and the number of live child processes (use `psutil`; add it as a dependency if needed). Run the sample in both modes from the packaged exe and report, per stage: average and peak CPU of the app, of the children, and of the whole system; plus the number of launches per stage.

Interpretation rules to apply and report:
- Sustained high CPU in **stage 2** (Whisper) or **stage 7** (encode) is legitimate heavy computation; it is handled in 4.2.
- High CPU in any other stage (translation, TTS, fitting, mix) with the app process as the main consumer is **unexpected**. Find the cause: sample the Python stacks of the worker threads once per second for that stage (for example with a sampler thread that calls `sys._current_frames()` and logs the top frames), identify the hot function, fix it, and report what it was. If instead the load is in many short child processes, the fix is Tasks 1 and 2.
- If you can read it, note whether `MsMpEng.exe` (Windows Defender) shows high CPU during the process-launch storm; this is a best-effort observation, not a requirement.

### 4.2 Limit CPU use and keep the machine usable
- Add a setting **"Mức dùng CPU" / "CPU usage"** (Settings → General): `Thấp / Low`, `Cân bằng / Balanced` (default), `Tối đa / Maximum`. Map it to a thread count from `os.cpu_count()`: Low = a quarter of the cores (at least 1), Balanced = half of the cores (at least 2), Maximum = all cores. Apply it to Whisper (`cpu_threads`), to FFmpeg (`-threads N` on the encode and on filter-heavy calls), and make it part of the resume hash only if it changes outputs (it should not; do not include it).
- Run FFmpeg children with `low_priority=True` (below-normal priority class) so the rest of the computer stays responsive. While stage 2 runs, lower the app process's own priority class to below normal and restore it afterwards.
- Encode speed: add Settings → Advanced "Tốc độ mã hóa video / Video encode speed" with the x264 presets `veryfast`, `faster`, `medium`. **Measure** on the sample, with the thread limit in place, the encode time and the output file size for each preset (same CRF), and report them as a small table. Set the default to `veryfast` unless its file is more than 40% larger than `medium`'s, in which case use `faster`. State the default you chose and why. Do not change CRF.
- Show a short note in the progress panel during stages 2 and 7 saying that heavy CPU use is normal in this stage (a localized string).

### 4.3 UI work per progress event
- Coalesce progress events so the UI handles at most about 10 updates per second (keep the latest per stage; never drop `done`, `error`, or `skipped` events).
- Give the log view a block limit (for example `setMaximumBlockCount(2000)`), keeping the newest lines.
- Measure the UI thread's event-loop delay during a real run with a 50 ms `QTimer` that records the largest gap between ticks; report the maximum and the 95th percentile. The goal is a maximum below about 250 ms at the Balanced setting.

### Checks for task 3
- [ ] The measurement tables (4.1) for both modes, before and after the changes where applicable.
- [ ] Tests for the CPU-usage mapping (thread counts for several core counts), that the settings value reaches Whisper and the FFmpeg command line (mocked), and that the resume hash does not change when only the CPU setting changes.
- [ ] Tests for event coalescing (rapid events produce at most the allowed UI updates; terminal events always pass) and for the log block limit.
- [ ] The preset table (time and size) and the chosen default.

---

## 5. Task 4 — Request budget and quota handling (findings 8–11)

### 5.1 Batch the shortening in stage 5
Replace the per-segment `llm.shorten(...)` calls with **batched** requests. In each attempt round: collect every segment that is still too long and not reviewed, send them in **one** `shorten_batch` request (split into chunks of at most 40 items), regenerate the TTS only for segments whose text changed, re-measure, and repeat for at most `max_fit_attempts` rounds. Keep the existing semantics: reviewed segments are never rewritten; if TTS fails after a shorten, restore the previous text and clip; the final `fit_status` rules are unchanged. The number of LLM requests in stage 5 for the sample must be at most a handful (rounds × chunks), not about 200.

### 5.2 Request budget
Add `llm.max_requests_per_run` (default 40). Count every LLM request in the client (stage 3 chunks, the enforcement pass, stage 5 rounds). When the budget is used up, stop calling the LLM, finish the run with what exists (overflow segments are stretched or lagged as before), and say so in the log and the result. Report "requests used" per stage in the result and show the total in the run summary.

### 5.3 Quota-aware key pool and fallback
- Track exhaustion per **(key, model)**, not per key.
- **Persist** per-day exhaustion across runs in `%LOCALAPPDATA%\DubTool\quota_state.json` as `{ "<first 12 hex of sha256(key)>|<model>": {"until": <epoch seconds>, "reason": "per-day"} }`. Take `until` from the retry delay in the API's error when present; otherwise use the next reset time as documented by Google (check the official documentation for the daily reset rule, and report what you found). Remove expired entries on load. The file must contain no key values and no text from which a key can be reconstructed.
- When **all** keys are daily-exhausted for the selected model, do not wait: fail fast with a clear localized message. In the UI the error dialog offers a button to redo the run in **Fast** mode (a new job, because the mode is part of the job id).
- If stage 3 finished with the Accurate model and the quota runs out during stage 5, **continue the shortening with the Fast model automatically** (config `llm.shorten_fallback_mode: fast`), and record that in the log and the result.
- In Settings → API keys, show a badge such as "Hết hạn mức đến HH:MM" for a key whose Accurate-model quota is exhausted, with a tooltip naming the model. Do **not** add explanatory hint text to the Settings dialog (the user removed it).

### 5.4 Timeouts, ETA, and diagnosing the slowness
- Add `llm.request_timeout_s` (default 180) per request with the existing retry rules, and show "đang chờ phản hồi từ model" with the elapsed seconds in the progress panel while a request is outstanding.
- **Diagnose Accurate mode's 965 s in stage 3.** Report the split between time spent waiting on 429s and retries, and time spent generating; run one 20-unit request three times and log total time, whether the API reports thinking or reasoning tokens, and any retries. If the official documentation describes a way to limit thinking for this model, test it once on the same units and report time and whether the Vietnamese differs (show both; do not judge). Do not change defaults.
- **Diagnose the no-op enforcement pass (24 → 24).** Log the raw reply of the enforcement call (truncated to 500 characters, never containing a key) and report whether the model returned unchanged text, invalid JSON, or something else. Fix it only if the cause is clear and the fix is trivial; otherwise report it.

### 5.5 Checks for task 4
- [ ] Test with a fake LLM that counts requests: 82 over-long segments cause at most `ceil(82/40)` requests per round; statuses equal those of the old per-segment logic on a deterministic fake.
- [ ] Tests for the request budget cap, for per-(key, model) tracking (a key exhausted for model A is selected for model B), for persistence and expiry with a fake clock, and for the fallback to the Fast model during stage 5.
- [ ] The persisted file contains only fingerprints and times: a test scans `quota_state.json` for every stored key value (quiet search) and finds none. The no-leak test from Update 5 is extended to this file.
- [ ] Fail-fast message when all keys are exhausted (fake provider).
- [ ] On the real sample in Accurate mode, report the requests used per stage. If the daily quota of today is already exhausted, say so; do not wait for it.

---

## 6. Task 5 — Clean up the project folder (it has grown to about 2 GB)

### 6.1 Remove debug leftovers
Delete `tests/_tmp_dbg.py`, `tests/_tmp_dbg2.py`, and `tests/build_log*.txt`. Look for other scratch files (`*.tmp`, `*.bak`, `*.orig`, `scratch*`, `~*`). List every file in `scripts/` with one line saying who uses it (build, tests, documentation, or nobody); remove the ones nobody uses that are clearly one-off, and keep documented tools. Report every deletion.

### 6.2 `scripts/clean_workspace.py` (also available as `dubtool clean`)
A **dry run by default** that prints (a) the 30 largest files and folders under the project root with their sizes, and (b) a per-category table: path, size, category, proposed action.

Categories:
- **Regenerable (deleted with `--apply`):** `build/`; `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `*.pyc`, `*.egg-info/`; temp files named `~raw-*` or `~trim-*`; `shots*/` and `screenshots/` except the newest set; `check/` frame folders; `old_before_*/` job backups; `review/` zips beyond the 3 newest; `dist/` builds and zips other than the newest; job folders under `jobs/` older than `--jobs-days` (default 14) **except** the most recent job for each sample video.
- **Report only (never deleted automatically):** `.venv/`, `.venv-build/`, `vendor/`, `models/` and any Whisper model cache, `sample/`, `.git/`, `.env`, `src/`, `tests/`, `packaging/`, `scripts/`, and the documentation files (`AGENT*.md`, `README*`).

Safety rules for the script:
- Resolve every path and refuse anything outside the project root.
- Do not follow symlinks or junctions (remove the link itself, never the target).
- Never touch `.git/`.
- `--apply` requires either `--categories <list>` or `--all-regenerable`.
- Write `cleanup.log` (path, size, time) and keep it out of Git; print the total reclaimed space.

### 6.3 Run it now
1. Run the dry run and report the breakdown of the current size (top 20 items and the category table).
2. Apply the regenerable categories.
3. Report the new size and the remaining large items with a recommendation for each.
4. **Ask the user before deleting anything in the report-only list**, and anything under `sample/` (the delivered `test1*.mp4` outputs belong to the user; list their sizes and ask which to keep). Do not delete `.venv` without asking; propose whether the dev `.venv` and `.venv-build` can be merged into one environment.

### 6.4 Build hygiene (`packaging/build_exe.ps1`)
- Create `.venv-build` fresh whenever the hash of the pinned requirements changed (store the hash in `.venv-build/.reqhash`); reuse it otherwise. This makes the "clean build environment" claim true.
- Build into a temporary dist folder and replace `dist/DubTool` only when the build and the checks succeed, so a failed build never leaves a half-built `dist/`.
- After a successful build delete `build/` (add a `-KeepBuild` switch), and keep only the newest `dist/DubTool` folder and its zip.
- Report the sizes of `.venv`, `.venv-build`, `build/`, and `dist/` before and after.

### 6.5 Shrink the package (measure first)
Print the 25 largest files and folders inside `dist/DubTool`. Then, **one at a time and only after verifying nothing needs it**, consider:
- dropping `ffprobe.exe` (about 100 MB) once probes are in-process (task 2) and no fallback still needs it (if any does, keep it and say which);
- excluding unused Qt modules and plugins (for example WebEngine, QML/Quick, 3D, multimedia, SQL, test modules) through the spec's `excludes`;
- keeping only the Qt translations for English and Vietnamese;
- removing duplicated DLLs.
Keep UPX off. After the pruning rerun `--selftest`, `--layout-check`, `--screenshots`, and `--headless-run` in Fast mode from the packaged exe, and report the new size. Do not remove anything the self-test or the headless run needs.

### 6.6 Replace the FFmpeg snapshot
Replace the bundled development snapshot with a current **release** build that includes `libass` and `libx264` and the filters required in `AGENT_UPDATE_4.md` section 10. Verify the publisher's checksum, record the version and source URL in `packaging/THIRD_PARTY.md`, and rerun the subtitle, mix, and encode checks. If no suitable release build is available, say so and keep the current one.

### 6.7 App data housekeeping (small addition)
The app removes old finished or failed job folders under `%LOCALAPPDATA%\DubTool\jobs` that are older than N days (setting "Giữ job tối đa N ngày", default 14), at startup and through a **"Dọn dẹp ngay"** button in Settings → Advanced. The button first shows the current size of the jobs and models folders. Never delete the job that is running, and never delete a resumable job from the last 3 days. The Whisper model cache is never deleted automatically.

### 6.8 Checks for task 5
- [ ] Tests for `clean_workspace.py` on a temporary fake project tree: dry run deletes nothing; `--apply` deletes only the listed categories; report-only items survive; paths outside the root are refused; a symlink or junction is removed without touching its target; `.git/` is never touched.
- [ ] Tests for the job pruning rule (age, running job, recent resumable job).
- [ ] The before and after size reports (6.3, 6.4, 6.5) and the list of files you deleted.
- [ ] The packaged exe still passes `--selftest`, `--layout-check`, `--screenshots`, and a Fast `--headless-run` after pruning and after the FFmpeg replacement.

---

## 7. Task 6 — Small corrections and answers

1. Run the **whole offline test suite as one aggregate** and report the real counts (passed, failed, skipped, and why). Do not report per-file results as a substitute.
2. `README.md` and `README_BUILD.md`: add a short note that Gemini free-tier quotas are **per project and per model**, so several keys from the same Google project share one quota, and that the user should check Google's terms before creating additional projects or accounts. Put it in the README, not in the Settings dialog.
3. Answers to the three questions in your `REVIEW_NOTES.md`:
   - Q1 (stale wrap-label hints): keep the constant minimum height on the wrap labels, and add a code comment explaining why. Do not spend time on a different invariant.
   - Q2 (should the per-day exhausted key stay unavailable): it should be persisted per key and model until its `until` time (task 4, 5.3), not reset on restart.
   - Q3 (overflow ids): the nuanced status counts are enough; also write the overflow ids to the job log.

---

## 8. GitHub publishing (a standing rule: the last step of this update and of every later one)

At the end of each update run, after the cleanup report and before the final report, commit and push the project to GitHub. Update 6 is the first run to do it. **Fix `.gitignore` before anything else (finding 13).**

### 8.1 Setup
- If the folder is not a Git repository, run `git init` with the branch `main`. **Do not change global Git configuration.** Use the repository's configured `user.name` and `user.email`; if they are not set, stop and ask the user (never invent them). Warn in the report that commit emails are visible in a public repository and that the user may prefer GitHub's noreply address.
- Use the remote `origin` if it exists. If there is none, commit locally only and ask the user for the repository URL in the final report. Never invent a URL and never create a repository yourself.
- `.gitignore` must exclude at least: `.env*` (keep `.env.example`), `.venv/`, `venv/`, `.venv-build/`, `build/`, `dist/`, `vendor/`, `models/`, `jobs/`, `sample/`, `review/`, `excerpts/`, `screenshots/`, `shots*/`, `check/`, `old_before_*/`, `*.log`, `cleanup.log`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `*.egg-info/`, media (`*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.wav *.mp3 *.m4a`), archives (`*.zip`), signing material (`*.pfx *.p12 *.pem *.key *.cer`), and any `transcript*.json` or `translated*.json` outside `tests/fixtures/` (they contain text from the user's videos). Show the final file in the report.
- **`AGENT*.md` files:** they contain phrases from the test video and instructions that only matter for this workflow. Do not include them in the repository until the user says so; ask once at the first push.
- **License:** do not create a `LICENSE` file; the user chooses it. Generate `THIRD_PARTY_LICENSES.md` from the installed packages' own metadata (name, version, license) and flag every package whose license is missing or copyleft (GPL, LGPL, AGPL). A package-index mirror lists `edge-tts` as LGPL-3.0, PySide6 is LGPL, and an FFmpeg build that includes `libx264` is GPL; **verify each from the installed package's own metadata, not from this file**. Without a `LICENSE` file a public repository is "all rights reserved" by default.
- The README states that the app needs the user's own Gemini API key and that the voice service (`edge-tts`) is an unofficial interface.

### 8.2 The first push needs the user's confirmation
Before the first push to a remote, print in the report: the remote URL, the branch, the **total size and the list of files** that would be published (paths and sizes), and the results of the gates below. Then **wait for the user's explicit OK in the chat**. After the user approves, write the approved remote URL to `.git/dubtool-publish-approved` (inside `.git`, so it is never committed). Later updates push automatically only to that same URL; if `origin` has changed, stop and ask again.

### 8.3 Pre-push gates (all must pass; otherwise commit locally, do not push, and report which gate failed)
1. The full offline test suite passes (the aggregate run from task 6).
2. A secret scan over everything that would be pushed (tracked and staged files, and the diff of unpushed commits): the exact values of all stored API keys and of any GitHub token in the environment; the patterns `AIza[0-9A-Za-z_-]{20,}`, `sk-ant-`, `-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----`, `ghp_[A-Za-z0-9]{20,}`, and `github_pat_`; and the forbidden file types listed above. Never print a matched value; print only the file path and line number.
3. No tracked file is larger than 50 MB (GitHub rejects files above 100 MB); warn above 5 MB. The total size of the repository's tracked files is reported and must be below 50 MB; if it is not, find out why before pushing.
4. Nothing under `jobs/`, `sample/`, `dist/`, `build/`, `vendor/`, `models/`, `.venv/`, or `.venv-build/` is tracked.
5. `git status` shows no unexpected untracked files (review them).
6. `python -m dubtool --version` runs from the working tree.

### 8.4 Commit and push
- Commit message: `Update N: <one-line summary>` with a short body listing the tasks (N is the number of the update being finished; for this run, 6). Multiple logical commits are fine.
- Tag `update-N`, then `git push origin main` and `git push origin update-N`.
- **Never** use `--force` or `--force-with-lease`, never rewrite history, never push to another branch or remote. If the push is rejected because the remote is ahead, stop and report; do not merge or rebase automatically.
- Authentication: use the credentials already configured on the machine (Git Credential Manager, an SSH key, or `gh auth`). Never ask for, print, store, or write a token. If authentication fails, stop and tell the user to sign in themselves.
- If you discover a secret in a commit that was **already pushed**, stop, tell the user to revoke the secret first, and do not rewrite history without the user's explicit decision.
- Do not upload binaries (no GitHub Release assets) in this update.

### 8.5 Report
Commit hash, tag, pushed or not (and why), the gate results, the tracked size, and the categories that were excluded.

---

## 9. Verification on the packaged executable

Rebuild with the existing build script and verify from the **packaged** exe, run from a different folder with a sanitized environment (PATH limited to Windows system folders, temporary `APPDATA` and `LOCALAPPDATA`).

### 5.1 Console-window watcher (`scripts/watch_console_windows.py`)
A script that starts `DubTool.exe --headless-run <sample> <outdir> --mode fast` (and then `--mode accurate`) **detached from any console**, and while it runs polls every 20 ms with `ctypes` (`EnumWindows`, `IsWindowVisible`, `GetClassNameW`, `GetWindowThreadProcessId`, `QueryFullProcessImageNameW`) for visible windows that are console windows (class `ConsoleWindowClass` or `CASCADIA_HOSTING_WINDOW_CLASS`) or that belong to `ffmpeg.exe`, `ffprobe.exe`, `cmd.exe`, or `conhost.exe` processes started by the app. It reports the number of distinct console windows seen and their owner images. **Expected: 0.** As a positive control, run the same watcher against a small test program that starts `ffmpeg.exe` **without** the helper's flags and show that it detects windows (this proves the watcher works; label it clearly as a control).

### 5.2 Other checks
- [ ] The watcher reports 0 console windows for both modes. Paste the table, including the control.
- [ ] Process launches per stage, before and after, as a table.
- [ ] CPU tables (section 4.1) and the encode preset table.
- [ ] Event-loop delay (max and 95th percentile) during a GUI-driven run of the sample.
- [ ] Cancel during the stage 7 encode returns control within about 2 seconds and leaves the job resumable (test it with the real packaged exe in headless mode by cancelling mid-encode, or with the service in a GUI-driven run).
- [ ] `--selftest`, `--layout-check`, and `--screenshots` still pass from the packaged exe.
- [ ] Both `--headless-run` outputs pass `ffprobe` (one H.264 video stream, one AAC audio stream, same resolution and frame rate as the input, duration within 1 s), and no key is found anywhere in the dist folder, logs, or temp profile (quiet search).
- [ ] All existing tests plus the new ones pass, as **one aggregate run**; report the counts (passed, failed, skipped, and why).
- [ ] Tasks 4, 5, and 6 checks (sections 5.5, 6.8, 7) are done and their results reported.
- [ ] The GitHub gates (section 8.3) pass, or the report says exactly which gate failed and that nothing was pushed.

---

## 10. Report back

Follow `AGENT.md` section 12 and `AGENT_UPDATE.md` 7.2. Include:

1. Files added and changed; commands run; test counts.
2. **Console windows:** the watcher results (with the control), and the list of every call site moved to the helper.
3. **Process launches:** the per-stage table before and after, and whether the in-process filter work of 3.4 was needed.
4. **CPU:** the per-stage measurement tables, the cause you found for any unexpected load (or a plain statement that none was found), the thread and priority settings, the encode-preset table and the default you set.
5. **UI responsiveness:** the event-loop delay numbers.
6. **Cancel:** what you verified about cancelling during the encode.
7. **Quota and requests (task 4):** requests used per stage, the batching result for stage 5, the quota-state behavior, the Accurate-mode latency diagnosis, and the cause of the no-op enforcement pass.
8. **Cleanup (task 5):** the before and after sizes, the list of deleted files and folders, what is left and your recommendation for each remaining large item, the package-size table before and after pruning, the FFmpeg version now bundled, and the questions you need the user to answer before deleting anything else.
9. The aggregate test counts, and the answers or changes for the three reviewer questions.
10. **GitHub (section 8):** the commit hash, the tag, whether anything was pushed (and if not, which gate or missing piece stopped it), the tracked size, and the questions you need answered (remote URL, license, whether to include the `AGENT*.md` files, Git identity).
11. What you could not verify: how the app feels on the user's computer, behavior with the user's antivirus, other Windows versions or DPI settings.
12. Anything you noticed but did not fix.
13. The review zip path and size.

Then stop and wait for the user's feedback.
