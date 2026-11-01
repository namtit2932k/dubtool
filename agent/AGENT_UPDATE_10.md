# AGENT_UPDATE_10.md — Slim the workspace: keep generated data out of the project folder, shrink the build environment and the package, stop duplicate downloads and copies, guard Git against large files, and report disk usage every update

Tenth update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_9.md` still apply: ground rules, data contracts, resumability, secret safety, the review zip rule, the reporting format, the version rule `1.0.<update number>` (this update is **1.0.10**), and the **Definition of done** in `AGENT_UPDATE_7.md` section 1 rule 5 (real-widget tests, real-SDK contract tests, rebuilt exe, fresh screenshots, rewritten `REVIEW_NOTES.md`, one aggregate test run, cleanup report, and the GitHub line from `AGENT_UPDATE_6.md` section 8). This file overrides them only where it says so. If a task here repeats an item of `AGENT_UPDATE_9.md` task 8 (package size), **check what was already done, verify it, and report it**; do not do it twice.

**Why this update exists.** The project folder is almost 4.4 GiB and the user can no longer keep it (the agent harness and the user's storage both struggle). A read-only inventory of the folder (`inventory.zip`, taken on 2026-10-31) shows that the source code is 3.5 MB; everything else is generated, third-party, or duplicated. The user is doing a one-time manual cleanup of the existing folder; this update makes sure the folder **does not grow back**.

---

## 0. Facts from the inventory (do not re-derive; verify only where a task says so)

Total: 4,614,988,140 bytes, 22,771 files. Largest top-level entries:

| Entry | Size | What it is |
|---|---|---|
| `.venv-build` | 997 MB (18,381 files) | build virtual environment; contains the **full** `PySide6` wheel |
| `dist` | 962 MB | `DubTool/` 728 MB + `DubTool-1.0.1-win64.zip` 281 MB |
| `.freebuff/san8` | 983 MB | two sanitized-run HOME folders, each holding the same 483.5 MB Whisper model download |
| `.git` | 534 MiB | 532 loose objects; 3 **unreachable** blobs of 483.5 MB, 110.1 MB, 2.2 MB (from an amended commit); reachable history is clean (largest blob 50 KB, no binaries) |
| `sample` | 341 MB | test inputs (73 + 79 MB) and e2e outputs (95 + 108 MB) |
| `vendor` | 245 MB | `ffmpeg.exe` 127.3 MB, `ffprobe.exe` 127.1 MB, a font |
| `jobs` | 234 MB | 6 old jobs; three hold identical copies of the 73 and 79 MB test videos |
| `models` | 87 MB | two Piper voice models |
| `.venv` | 58 MB | contains only the Piper package |
| root clutter | ~1 MB | about 25 `build_log*.txt`, `cleanup_dryrun.txt`, `cleanup.log`, `exe_build_8.log` |

Outside the project: `%LOCALAPPDATA%\DubTool\jobs` 1.88 GB (one completed job of a 33-minute video is 1.2 GB with 1,858 files because "Giữ các tệp trung gian" is on), the Hugging Face cache 2.65 GB, the pip cache 570 MB, `%TEMP%\DubToolProbe` 817 MB, and about 15 `%TEMP%\dubtool-gui-smoke-*` and `dubtool-smoke-media-*` folders left by smoke tests.

Inside `dist/DubTool/_internal`: `vendor/` 256.5 MB (`ffmpeg.exe` + `ffprobe.exe`), `PySide6` 98 MB (including `opengl32sw.dll` 20.6 MB, `Qt6Qml.dll` 5.4 MB, `Qt6Quick.dll` 6.8 MB), `av.libs` 65.6 MB (`avcodec` 19.3 MB, `libx265` 12.7 MB, `libSvtAv1Enc` 7.7 MB, `avfilter` 5.9 MB), `ctranslate2` 63.7 MB, `onnxruntime` 39.3 MB, `numpy.libs` 21.2 MB. **Duplicated inside dist:** `ctranslate2.dll` (61.3 MB) at `_internal/` and at `_internal/ctranslate2/`; `onnxruntime.dll` (19.3 MB) at `_internal/` and at `_internal/onnxruntime/capi/`.

Inside `.venv-build/Lib/site-packages/PySide6`: `Qt6WebEngineCore.dll` 203.5 MB, `resources/` 106.3 MB (including `qtwebengine_devtools_resources.debug.pak` 75.8 MB), `translations/` 62.2 MB (including `qtwebengine_locales` 45.8 MB), `qml/` 21.2 MB. None of the WebEngine parts is used by the app.

**How the growth happened (so the same mistakes are not repeated):**
- Verification runs used a fresh temporary HOME each time, which re-downloaded the 483.5 MB model each time, inside the project folder (`.freebuff/san8/home`, `home2`).
- The 483.5 MB and 110.1 MB blobs were staged by a broad `git add` while `.freebuff/` and `vendor/` were not ignored; the pre-push gate (50 MB) runs only at push time, so it could not stop staging or committing.
- Job folders copy the input video, and "keep intermediate files" keeps every clip.
- Tools wrote their logs into the project root.

---

## 1. Rules for this update

1. **Do not delete or move any existing data of the user** (videos, outputs, jobs in the app data folder, models) in this update. The user performs the one-time cleanup. The tools you write must be **dry-run by default** and must never delete without an explicit flag and the user's approval.
2. Change only what this file lists. New behavior sits behind config keys where it changes outputs.
3. Never print, log, or write an API key. Never read `.env` into output.
4. Every number and file claim in your reports is backed by the command and its output (paste the lines).
5. **Never write logs, redirected command output, or temporary files into the project root.** From now on they go to `<build root>/logs/` and `<build root>/tmp/` (task 1).
6. **Never stage with `git add -A` or `git add .`.** Stage explicit paths, or use `git add -u` plus reviewed new files.
7. Do not run `git gc`, `git prune`, or `git reflog expire` yourself unless the user says so; report and quote the commands (task 6.4).

---

## 2. Task 1 — A build root outside the project folder

### 2.1 Layout
Introduce one concept, the **build root**: a folder **outside** the project, default `<project parent>\<project folder name>_build` (for the user: `D:\Projects\Vibe\Project_K_build`), overridable by the environment variable `DUBTOOL_BUILD_ROOT`. Everything generated lives there:

```
<build root>/
  venv-build/        # build virtual environment (was .venv-build)
  build/             # PyInstaller work directory
  dist/              # DubTool/ folder + zip (only the newest pair)
  vendor/            # ffmpeg.exe, fonts (third-party binaries)
  jobs/              # job folders when running from source
  models/            # Piper or other voice models, if any
  hf-cache/          # shared Hugging Face / model cache for all verification runs
  sample_output/     # e2e outputs of the sample videos
  screenshots/       # newest set only
  inventory/         # disk reports
  logs/              # every log and redirected command output
  tmp/               # sanitized homes and temporary files (cleaned after each run)
```
Stay inside the project: `src/`, `tests/`, `scripts/`, `packaging/`, `docs/`, `agent/`, `sample/input/` (the user's test videos), `review/` (small review zips), the README files, and the config files.

### 2.2 One place for paths
Create `src/dubtool/workspace.py` with `build_root()`, `venv_dir()`, `dist_dir()`, `vendor_dir()`, `jobs_dir()`, `models_dir()`, `hf_cache_dir()`, `logs_dir()`, `tmp_dir()`, and so on. It resolves the environment variable, then the default; it creates folders on demand; it refuses a build root that is inside the project folder (with a clear message). `app/paths.py` (source-checkout paths), `packaging/build_exe.ps1`, and every script in `scripts/` use it; none hardcodes `dist`, `jobs`, `models`, `vendor`, `.venv-build`, or `screenshots` relative to the current directory. The installed exe keeps using `%LOCALAPPDATA%\DubTool` and `%APPDATA%\DubTool` as today.

For compatibility: if `vendor/` exists inside the project and the build root's `vendor/` does not, the build script may read from it and print a one-line notice that it should be moved.

### 2.3 Migration helper
`scripts/migrate_workspace.py` is a **dry run by default**. It prints, for every existing generated folder in the project (`dist`, `build`, `jobs`, `models`, `vendor`, `screenshots`, `sample/output`, root `*.log` and `build_log*.txt`), its size and the proposed destination in the build root, and it states "move" (same drive: an instant rename) or "recreate" (`.venv-build` and `.venv` are **not relocatable**; the build script creates `venv-build` in the new place and the old folder is left for the user to delete). `--apply` performs only the moves, one folder at a time, refuses to overwrite an existing destination, refuses any path outside the project and the build root, does not follow links, and writes `logs/migrate.log`. It never deletes. Report the dry-run output; do **not** run `--apply` until the user says so.

### 2.4 Tests
Path resolution with and without the environment variable; the refusal of a build root inside the project; the migration dry run on a temporary fake project (nothing changes, the report lists every item), `--apply` on the fake tree (moves only, refuses existing destinations and outside paths); a static test that no script or module builds a generated-folder path relative to the current directory instead of using `workspace.py`.

---

## 3. Task 2 — A lean build environment

1. **Replace the full `PySide6` wheel by `PySide6-Essentials`** (and keep `shiboken6` compatible) in the pinned requirements, **if** the app only imports Essentials modules. Scan `src/` for every `PySide6.Qt*` import and list them. If an Addons-only module is needed, name it and keep only that. Verify with `--selftest`, `--layout-check`, `--screenshots`, `--gui-smoke`, and a real headless run from the rebuilt exe. Report the size of the build environment **before (997 MB baseline) and after**.
2. The build script creates the environment in `<build root>/venv-build`, recreates it only when the hash of the pinned requirements changes, and passes a pip cache directory under the build root.
3. Report how many Python environments exist and what each is for (`.venv` contains only Piper; the user runs the app with the system Python launcher `py`). Propose a single development environment strategy in `README_BUILD.md`; do not delete anything.
4. Tests: `pip check` passes in the new environment; a test that the requirements file does not list the full `PySide6` when Essentials is sufficient; the self-test prints the list of Qt modules actually imported.

---

## 4. Task 3 — No duplicate downloads, no leftover temporary folders

1. **Shared model cache for every verification run.** Every sanitized-environment run (temporary HOME, APPDATA, LOCALAPPDATA, PATH, as in `AGENT_UPDATE_7.md` section 8) sets `HF_HOME` and `HF_HUB_CACHE` to `<build root>/hf-cache`, and the app's model folder (`%LOCALAPPDATA%\DubTool\models` when installed) to a shared `<build root>/models-cache` through an environment override. Check `app/paths.py` for how the model folder is chosen, and add `DUBTOOL_MODELS_DIR` if it does not exist. The first run may download the model once; later runs reuse it. The first-run download dialog must still work in a normal installation.
2. **Sanitized homes go to `<build root>/tmp/sanitized/<run id>/`** and are removed when the run ends (keep the log). Never create them under the project folder or under `.freebuff`.
3. **Smoke tests clean up.** `--gui-smoke`, `--selftest`, and the tests delete their temporary directories (`dubtool-gui-smoke-appdata-*`, `dubtool-smoke-media-*`, `DubToolProbe`, and so on) in a `finally` block. Add a test that after `--gui-smoke` the temp folder contains no new `dubtool-*` entries.
4. Report the size of `<build root>/hf-cache` and `<build root>/tmp` after the verification runs.

---

## 5. Task 4 — Jobs: do not copy the input, and slim the folder after success

### 5.1 No copy of the input video
`Job.create` must not copy the input video into the job folder by default. Store in `state.json`: the original path, size, modification time, and a fingerprint (SHA-256 of the first 1 MB, the last 1 MB, and the size). Stage 1 reads the original path. If the file is missing or its size, time, or fingerprint changed, the run stops with a clear localized message ("Video gốc đã thay đổi hoặc bị di chuyển — hãy chọn lại video") and does not resume silently with the wrong file. Config `jobs.copy_input: false` (default); `true` restores copying. Legacy jobs that contain `input.mp4` keep working.

### 5.2 Retention level after a successful run
Replace the checkbox "Giữ các tệp trung gian" by a dropdown **"Tệp tạm sau khi xong"** with three choices:
- **Xóa hết:** delete the whole job folder after success (today's unchecked behavior).
- **Giữ bản dịch và nhật ký** (new default): keep the small files (`state.json`, `transcript.json`, `translated.json`, `units.json`, `schedule.json`, `words.json`, `quality_report.json`, `tts_failures.json`, `durations.json`, the `.srt`, the readable file, and the logs) and delete the bulky files (`tts/`, `fitted/`, `vi.wav`, `audio.wav`, mix and encode temporaries, any copied input).
- **Giữ tất cả:** keep everything (today's checked behavior).
The saved boolean is migrated: `false` → "Xóa hết", `true` → "Giữ bản dịch và nhật ký". Add a tooltip that states what each choice keeps and that failed or cancelled runs always keep everything so that they can be resumed. Put the choice in the settings and in `--keep-level none|light|full` for the CLI. The choice is disabled while a run is in progress (Update 9 task 1).

### 5.3 Size cap
Add `jobs.max_total_mb` (default 3000). When the total size of the jobs folder exceeds it, housekeeping deletes the **oldest finished** jobs first until it is under the cap; it never deletes the running job or a resumable job from the last 3 days. Show the total size and the cap in the working-files group of Settings → Advanced (Update 9 task 6).

### 5.4 Tests
Job creation without copying (the job folder has no video; `state.json` has the fingerprint); a changed or missing source is detected and reported; a legacy job with `input.mp4` still runs; each retention level deletes exactly the listed files and keeps the others (temporary job tree); the migration of the old boolean; the size-cap ordering and exceptions; the dropdown with real widgets, including that it is disabled while busy. Report, from a real headless run of `test2` in Fast mode, the job folder size at the end for the three levels.

---

## 6. Task 5 — The package and the build output

Do the items of `AGENT_UPDATE_9.md` task 8 that are not yet done (verify and report the rest), and add the following. **Do not promise a size; report what was achieved.**

1. **`ffprobe.exe`:** remove it from `vendor/` and from the package once PyAV probing is proven for every container type in the samples (mp4, mkv, mov, webm, avi, m4v). The failure path of a probe must give a clear message and never try to start a missing program.
2. **Duplicate DLLs inside `dist`:** investigate why `ctranslate2.dll` and `onnxruntime.dll` appear twice (`_internal/` and the package folder), remove one copy of each through the spec file (filter the binaries list), and verify with `--selftest`, `--gui-smoke`, and a real headless run from the packaged exe in a sanitized environment. If one copy is required (DLL search order), say which and why, and keep it. Then scan the whole `dist/DubTool` for other duplicate files (same size and SHA-256) and list them.
3. **Qt:** exclude Qml and Quick (DLLs and QML data) if nothing imports them; keep only the English and Vietnamese translations; limit the plugins to the ones the app uses. **Keep `opengl32sw.dll`** (software OpenGL fallback for machines without graphics drivers) unless the user decides otherwise; report its size and the trade-off.
4. **`av.libs`:** `faster-whisper` needs PyAV, and the encoders (`libx265`, `libSvtAv1Enc`) are linked by it. Report whether anything in `av.libs` can be removed without breaking the import; **do not remove** a library that the import needs.
5. **FFmpeg:** evaluate whether a smaller **release** build still provides every filter and encoder the app uses (see `AGENT_UPDATE_9.md` task 8, item 3). Switch only when every check passes.
6. **Build output location:** the build script writes to `<build root>/dist`, keeps only the newest pair (folder plus zip, matched by build id), builds in a temporary directory and promotes only on success, and has a switch `-ZipOnly` that deletes the unpacked folder after zipping. It prints the absolute path of the exe and the zip at the end.
7. Report a before and after table: `dist/DubTool` folder size, zip size, number of files, per group (Qt, FFmpeg and media libraries, speech-recognition runtime, Python runtime, other).

---

## 7. Task 6 — Git hygiene (finding: a 483 MB file could be staged)

1. **`.gitignore`:** add at least `.freebuff/`, `inventory/`, `hf-cache/`, `tmp/`, `*.log`, `build_log*`, `exe_build*`, `cleanup_dryrun.txt`, `*.onnx`, `*.bin` (outside `tests/fixtures/`), and keep every existing entry. Show the final file in the report.
2. **Pre-commit guard.** `scripts/install_git_hooks.py` installs a local `pre-commit` hook (hooks are local; the script lives in the repository) that **blocks** a commit when any staged added or modified file (a) is larger than `GIT_MAX_FILE_MB` (default 5), or (b) is under a forbidden prefix (`.freebuff/`, `.venv`, `venv-build/`, `dist/`, `build/`, `jobs/`, `sample/`, `vendor/`, `models/`, `hf-cache/`, `tmp/`, `inventory/`) or has a forbidden extension (`.exe`, `.dll`, `.zip`, `.mp4`, `.mov`, `.mkv`, `.wav`, `.mp3`, `.onnx`, `.bin`, `.pt`, `.safetensors`, `.pfx`, `.p12`, `.pem`, `.key`), and it also runs the secret scan of `scripts/preflight_gates.py` on the staged diff. Use `git diff --cached --name-only --diff-filter=AM` and `git cat-file -s` for sizes. The message must say which file, why, and how to fix it. Bypass only with the environment variable `ALLOW_BIG_FILE=1`, which is logged. Install the hook in the user's repository (this changes only `.git/hooks`) and report it.
3. **`scripts/preflight_gates.py` uses `proc.py`** (it still calls `subprocess` directly; Update 7 finding) and checks the same limits before a push.
4. **Report the current state and quote the cleanup commands; do not run them.** Report `git count-objects -vH`, the unreachable objects of 1 MB or more (`git fsck --unreachable --no-reflogs`, sizes only) and whether anything large is currently **staged** (`git diff --cached --stat`). If large unreachable objects remain, print these commands for the user to run after checking that nothing large is staged: `git reflog expire --expire=now --all`, `git gc --prune=now`, `git count-objects -vH`.
5. **`agent/` and old screenshots in history:** state which paths in the reachable history are not source code (for example `agent/AGENT_UPDATE_*.md`, `screenshots/*.png`) and ask the user, before the first push, whether the repository will be public.
6. Tests in a temporary Git repository: staging a 6 MB file is blocked; a normal source file passes; a file under `.freebuff/` is blocked; the bypass variable works and is logged; the secret scan blocks a fake key in a staged file.

---

## 8. Task 7 — Disk usage tooling and a disk budget

1. **`scripts/disk_report.py`** (read-only; also `dubtool doctor --disk`): promote the inventory script of the user's review. It prints and writes to `<build root>/logs/`: the top-level entries of the project and of the build root with sizes and file counts; the 30 largest files; duplicates of 1 MB or more (size plus SHA-256, skipping `.env*` and virtual environments); the location sizes outside the project (`%LOCALAPPDATA%\DubTool`, `%APPDATA%\DubTool`, the Hugging Face cache **with one line per model folder and its size**, the pip cache, `%TEMP%` entries whose names contain `dubtool` or `DubToolProbe`); and the git facts of task 6.4. It never reads file contents of `.env*` or settings files.
2. **Disk budget.** Config `workspace.project_max_mb: 1500`. The report ends with a **"Disk budget"** line: project folder size, build root size, the app-data jobs size, the Hugging Face cache size, and "within budget" or "OVER budget" with the 10 largest items. An update that leaves the project folder over budget is **not done** unless the report explains why and names what the user must decide. Until the user runs the manual cleanup and `migrate_workspace.py --apply`, expect "OVER budget" and say so.
3. **`scripts/clean_workspace.py`** is updated for the new layout: it understands the build root, never writes its own log into the project root, and keeps its dry-run default and safety rules (`AGENT_UPDATE_6.md` 6.2).
4. **`docs/WORKSPACE.md`:** a table of **every** folder and file that can appear in the project folder and in the build root: what creates it (script and command), what needs it (build, tests, runtime, nothing), how it is regenerated, how long that takes, and whether it is safe to delete. Write it from the code, citing files; do not answer "unsure" (the previous inventory had to). The disk report compares the real top-level entries with this document and flags any entry that is not documented.

Tests: the disk report on a temporary fake tree (counts, duplicates, budget line, no content of `.env*` in the output); the undocumented-entry check; the clean script on the new layout.

---

## 9. Verification

1. Rebuild with the new layout and verify from a sanitized environment (using the shared caches of task 3): `--selftest`, `--layout-check`, `--screenshots`, `--gui-smoke`, and a real `--headless-run` in Fast mode on `test2` from `<build root>/dist/DubTool/DubTool.exe`. Version `1.0.10`, build id `Update 10`.
2. Run `scripts/migrate_workspace.py` as a **dry run** and paste the output.
3. Run `scripts/disk_report.py` and paste the "Disk budget" line and the top 10 items.
4. Clone the repository into a temporary folder (`git clone --no-hardlinks`) and report its size and file count; the clone must contain no binary larger than 5 MB.
5. Install and test the pre-commit hook in the real repository with a harmless test (stage a temporary 6 MB file, confirm the block, unstage it, delete it) and report.
6. Report the job folder sizes of the real `test2` run for the three retention levels, and for `jobs.copy_input` false and true.
7. A table of before and after: build environment size, `dist/DubTool` size, zip size, project folder size (before the user's migration), and the sizes of `hf-cache` and `tmp` after the runs.
8. No key found anywhere in the build root, logs, screenshots, diagnostics, or readable files (quiet search); the aggregate offline suite passes in one run with the real counts.

---

## 10. Report back

Follow `AGENT.md` section 12, `AGENT_UPDATE.md` 7.2, and the Definition of done. Include:

1. Files added and changed; commands run; aggregate test counts.
2. Task by task, what changed and the evidence (the dry-run migration output, the build environment sizes, the shared-cache proof, the retention-level sizes, the dist table, the hook test, the disk report).
3. What remains for the user: the manual cleanup commands, the decision on `migrate_workspace.py --apply`, `.venv`, `models/piper`, `sample/output`, and the repository visibility question.
4. The build: absolute path of the exe and the zip, build id, version, SHA-256, and the screenshot folder with `INDEX.txt`.
5. The GitHub line (pushed or not, commit, tag, gate results, reason if not pushed, any question you need answered).
6. What you could not verify (how the app feels on the user's display, the dub's sound, translation quality, antivirus behavior, any size that depends on the user's machine).
7. Anything you noticed but did not fix.
8. The review zip path and size.

Then stop and wait for the user's feedback.
