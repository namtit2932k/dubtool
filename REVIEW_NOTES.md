# REVIEW_NOTES.md

## Session: AGENT_UPDATE_10.md work — verified facts only

Working rule (same as previous updates): every section below contains
either a **measured result** from this session (the command or probe
that produced it is stated in it) or an explicit "not measured /
reason". Full rows live in `REPORT_UPDATE_10.md`; this file is the
reviewer's cheat sheet.

## 1. What exists in this repo for AGU10 (verified present)

- Build root: `src/dubtool/workspace.py` —
  `DUBTOOL_BUILD_ROOT` env or default `<project parent>_build`; a
  build root inside the project folder raises. Getters:
  `build_root()`, `venv_dir()`, `dist_dir()`, `vendor_dir()`,
  `jobs_dir()`, `models_dir()`, `hf_cache()`, `sample_output()`,
  `screenshots_dir()`, `logs_dir()`, `tmp_dir()`.
- Migration: `scripts/migrate_workspace.py` (dry run by default;
  `--apply` moves dist/jobs/vendor/models/.venv-build/logs and
  recreates the two venvs at the build root).
- Shared caches from source runs: `app/paths.py` `models_dir()` /
  `jobs_dir()` prefer `<build root>/models` and `<build root>/jobs`
  when they exist (fallback to the legacy in-project folders until the
  user migrates).
- Retention levels: `keep_level: none|light|full` in
  `app/settings.py` DEFAULTS + legacy `keep_intermediate` migration
  (false → none, true → light); applied in `api.py` `run_dub()`
  prune path. Old boolean setting is never written back.
- Git guards: `scripts/git_guard.py` (staged file > 5 MB blocked at
  commit; `ALLOW_BIG_FILE=1` override; pre-push 50 MB gate),
  `scripts/install_git_hooks.py`, tested in `tests/test_git_guard.py`.
- Disk report: `scripts/disk_report.py` (+ reusable
  `src/dubtool/disk_report.py`) — per-folder counts, duplicate-DB
  check, undocumented-entry check against `docs/WORKSPACE.md`, and
  the "Disk budget" line; never reads `.env*` contents.
- `docs/WORKSPACE.md`: every generated folder, what creates it, what
  needs it, how to regenerate, safe-to-delete.
- `mix_original_with_dub()` now references the ORIGINAL VIDEO by the
  absolute path the stage passes it (no-copy default), fixing the
  `jobs.copy_input: false` regression found during verification.

## 2. Aggregate offline suite (final state, one run)

`python -m pytest tests -q --ignore=tests/test_screenshots.py` →
**492 passed, 2 skipped in 66.22 s**. Skips are the environmental
symlink-privilege skips, not coverage skips.

## 3. Build in dist (identity verified this session)

`<build root>/dist/DubTool/DubTool.exe` — version **1.0.10**, build id
`Update 10 (ad373d8)`; zip `DubTool-1.0.10-win64.zip` = 187 MB
(sha256 `fe98c8cf…d74721`, full in the report). Unpacked dist 470 MB.
Fresh-exe gates on the sanitized environment: `--selftest overall:
PASS`; sanitized headless runs code=ok. Screenshot set
`<build root>/screenshots/update_10_ad373d8/` + `INDEX.txt`.

## 4. Retention levels — real test2 runs (the exe, sanitized APPDATA)

| level | job folder after run | kept |
| --- | --- | --- |
| none | **16 KB** | pipeline.log + state.json only |
| light | **133 KB** | all JSONs + logs, clips/tts/fitted deleted |
| full | **229.7 MB** | everything incl. fitted (10.8 MB), tts (1.2 MB), output.nosubs.mp4 (78.8 MB) |

`jobs.copy_input: false` (new default): the job folder has no video
copy — the table above. With `copy_input: true` the folder additionally
holds the input video copy (test2.mp4 = 79.0 MB), i.e. full+copy ≈
308.7 MB. `mix_original_with_dub` reads the input by its absolute path
(regression test `tests/test_mix_input_ref.py`).

## 5. Verification evidence (all run this session)

- `scripts/migrate_workspace.py` dry run: **3.5 GB** would move out of
  the project (dist 657 MB, jobs 918.4 MB, .venv-build 963.7 MB,
  sample\output 608.5 MB, vendor 244.6 MB, models 86.8 MB, logs…).
- `scripts/disk_report.py`: project 4930.2 MB, build root 2126.2 MB;
  budget line + top-10 in the report.
- Clone check: `git clone --no-hardlinks` → 2.7 MB, 194 files, **no
  file > 5 MB**.
- Pre-commit hook test: staged a 6 MB text file → `pre-commit: BLOCK -
  larger than 5 MB: tmp_hooktest.txt (5.72 MB)`, commit refused;
  unstaged and deleted.
- Quiet key scan over `<build root>` (dist, logs, tmp): only
  sha256/base64 strings, crypto binaries, and the `fixture-fake-key`
  test constants. No real key found.
- Shared caches proof: the three sanitized runs used
  `HF_HOME=<build root>/hf-cache` + `DUBTOOL_MODELS_DIR=<build root>/
  models-cache`; no model re-download in any run (run 1 downloaded
  nothing; the Whisper small model came from the shared cache).

## 6. Bugs found during verification (fixed this session)

1. `run_headless` ignored the saved `keep_level` (always the default
   `light`), so the first `none`/`full` verification runs measured the
   wrong level. Fixed in `app/main.py`: the settings file decides the
   level, as the GUI does. The `none`/`full` rows above are from the
   fixed exe; the `light` row is from the (default-equal) earlier run.
2. With `jobs.copy_input: false`, stage 6's ffmpeg opened the input by
   bare filename (worked only when the video was copied into the job
   folder). Fixed in `audio_utils.py` + regression test.

## 7. Sizes before → after (build environment)

| item | before | after |
| --- | --- | --- |
| project folder | 4930.2 MB | unchanged (user must run migration) |
| build venv | 963.7 MB (`.venv-build` in project) | **634 MB** (`<build root>/venv-build`) |
| dist/DubTool | 657 MB (project) | **470 MB** (`<build root>/dist`) |
| zip | 187 MB | 187 MB |
| build-root hf-cache | — | 464 MB (shared, once) |
| build-root tmp after runs | — | 544 MB (sanitized run homes; safe to delete) |

## 8. What is NOT in this file

Full provenance and the GitHub line live in `REPORT_UPDATE_10.md`.
Not verified (cannot be automated): how the app feels on the user's
display, dub sound, translation quality, antivirus behavior.
