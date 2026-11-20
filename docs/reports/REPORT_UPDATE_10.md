# REPORT_UPDATE_10.md

Update 10 of the agent series (`agent/AGENT_UPDATE_10.md`): keep
generated data out of the project folder (build root outside the
project), lean build environment, shared model caches, job retention
levels, git guards against large files, disk tooling, and a rebuilt
1.0.10 exe. Ground rules from `AGENT.md` + `AGENT_UPDATE_7.md`
Definition of done apply. Evidence in `REVIEW_NOTES.md`; everything
below was actually run this session.

## 1. Files added and changed; commands; test counts

**Added:** `src/dubtool/workspace.py`, `src/dubtool/disk_report.py`,
`scripts/migrate_workspace.py`, `scripts/disk_report.py`,
`scripts/git_guard.py`, `scripts/install_git_hooks.py`,
`scripts/clean_workspace.py`, `docs/WORKSPACE.md`,
`tests/test_workspace.py`, `tests/test_build_env.py`,
`tests/test_disk_report.py`, `tests/test_git_guard.py`,
`tests/test_jobs_retention.py`,`tests/test_mix_input_ref.py`, `scripts/run_retention.sh`, `agent/AGENT_UPDATE_10.md`.

**Changed:** `src/dubtool/app/paths.py`, `app/settings.py`,
`app/main.py`, `app/cleanup.py`, `app/housekeeping.py`,
`app/selftest.py`, `app/ui/main_window.py`,
`app/ui/settings_dialog.py`, `app/i18n.py`, `api.py`, `cli.py`,
`config.py`, `job.py`, `audio_utils.py`,
`stages/s1_extract_audio.py`, `stages/s6_mix.py`,
`packaging/build_exe.ps1`, `packaging/dubtool.spec`,
`packaging/update_number.txt`, `src/dubtool/__init__.py`,
`scripts/preflight_gates.py`, `scripts/latency_probe.py`,
`scripts/check_subtitle_frames.py`, `scripts/update_readme_images.py`,
`.gitignore`, `tests/test_app_ui.py`, `tests/test_update9_ui.py`,
plus doc files.

**Aggregate offline suite (one run, final code):**
`python -m pytest tests -q --ignore=tests/test_screenshots.py` →
**492 passed, 2 skipped in 66.22 s** (skips are environmental
symlink-privilege skips, not coverage skips).

## 2. Task-by-task, with evidence

### Task 1 — build root outside the project
`src/dubtool/workspace.py` resolves `DUBTOOL_BUILD_ROOT` or default
`<project parent>/Project_K_build`; refuses a root inside the project.
`app/paths.py` prefers `<build root>/jobs` and `<build root>/models`
when present. **Dry-run migration output** (nothing moved; user
decides `--apply`):

```
project   : D:\Projects\Vibe\Project_K
build root: D:\Projects\Vibe\Project_K_build (env DUBTOOL_BUILD_ROOT overrides)
     size  action     source -> destination
  657.0 MB  move       dist -> <build root>/dist
  918.4 MB  move       jobs -> <build root>/jobs
   86.8 MB  move       models -> <build root>/models
  244.6 MB  move       vendor -> <build root>/vendor
  608.5 MB  move       sample\output -> <build root>/sample_output
  963.7 MB  recreate   .venv-build -> <build root>/venv-build
   57.9 MB  recreate   .venv -> <build root>/.venv (dev only)
   (+ 175 KB screenshots, build/, and 32 log files)
these items account for 3.5 GB inside the project folder.
```

### Task 2 — lean build environment
`packaging/build_exe.ps1` builds at the build root: dedicated
`venv-build` (634 MB vs 963.7 MB before — PySide6 Essentials wheel
instead of the full wheel, minus unneeded extras), pip cache and
workpath redirected there. **Before → after:** project `.venv-build`
963.7 MB → `<build root>/venv-build` 634 MB; dist 657 → 470 MB;
zip 187 MB.

### Task 3 — shared model caches
`app/paths.py` models/jobs resolution + `HF_HOME` /
`DUBTOOL_MODELS_DIR` in the sanitized runs. **Proof:** three
headless runs from the exe ran with
`HF_HOME=<build root>/hf-cache` (464 MB, downloaded once) and
`DUBTOOL_MODELS_DIR=<build root>/models-cache`; no per-run
re-download in any run.

### Task 4 — job retention levels (`keep_level`)
`none | light | full` in settings (`keep_intermediate` migrated on
load: false→none, true→light; old key never written back). Real
`test2` runs from the packaged exe, sanitized APPDATA each:

| level | job folder kept | contents |
| --- | --- | --- |
| none | **16 KB** | pipeline.log + state.json |
| light | **133 KB** | JSONs + logs; clips/tts/fitted deleted |
| full | **229.7 MB** | all intermediates (fitted 10.8 MB, tts 1.2 MB, output.nosubs.mp4 78.8 MB) |

`jobs.copy_input` **false** (new default) = table above (no video in
the job); **true** adds the 79.0 MB input copy (full+copy ≈ 308.7 MB;
light/none keep the input in place either way).
**Bugs found and fixed during this verification:** (a)
`run_headless` ignored the saved `keep_level` — every headless run
measured `light`; fixed in `app/main.py` so the settings file decides,
as in the GUI; (b) with `copy_input: false` the s6 mix ffmpeg opened
the input by bare filename and failed — fixed in `audio_utils.py`
(absolute input path) + `tests/test_mix_input_ref.py`.
The runs exposed the designed resilience behavior: edge-TTS was
degraded and every unit exhausted its 5 attempts with recorded
failures for a later `retts` pass; runs still completed code=ok.

### Task 5 — git guards
`scripts/git_guard.py`: pre-commit blocks staged files > 5 MB
(`ALLOW_BIG_FILE=1` override), pre-push gate at 50 MB. Hook test
(verification 9.5, run in the real repository):
```
$ git add tmp_hooktest.txt  (6 MB)  && git commit -m "hook test"
pre-commit: BLOCK - larger than 5 MB: tmp_hooktest.txt (5.72 MB)
pre-commit: commit blocked. Fix by removing the staged file ...
```
Staged file unstaged and deleted. Note: a 6 MB `.bin` name is refused
earlier by .gitignore (binary extensions ignored) — the hook itself
was proven with a text file.

### Task 6 — disk tooling
`scripts/disk_report.py` (counts, duplicates, undocumented-entry check
against `docs/WORKSPACE.md`, budget line; never reads `.env*`
contents). **Budget line + top 10:**
```
Disk budget: project 4930.2 MB, build root 2126.2 MB, appjobs 2176.6 MB,
hf cache 2064.4 MB — OVER budget (1500 MB cap; 10 largest (MB):
sample 1056.2, .venv-build 963.7, .freebuff 937.5, jobs 918.4, dist 657.0,
vendor 244.6, models 86.8, .venv 57.9, tests 3.0, src 1.7)
```
`docs/WORKSPACE.md` documents every generated folder from the code
(no "unsure" rows).

### Task 7 — verification on the rebuilt exe
Sanitized environment (own APPDATA/LOCALAPPDATA under
`<build root>/tmp/`, shared caches): `--selftest overall: PASS`
(gemini client, docx roundtrip, Qt modules), three sanitized
headless runs (see task 4), fresh screenshots
`<build root>/screenshots/update_10_ad373d8/` + `INDEX.txt`
(version 1.0.10, git ad373d8, built 2026-11-01 05:45 UTC).
**Quiet key search** over the build root (dist, logs, tmp): only
sha256/base64 hashes, crypto runtime binaries, and the
`fixture-fake-key` test constants; **no real key found**.

### Repo clone check (9.4)
`git clone --no-hardlinks` into a temp folder → **2.7 MB, 194 files,
no file > 5 MB**. (The 483 MB unreachable blobs from the Update-9
inventory were already pruned by `git gc --prune=now` last session.)

## 3. What remains for the user (decisions + manual steps)

1. Run `scripts/migrate_workspace.py --apply` (dry run above shows the
   3.5 GB it moves) — frees the project folder to source + docs.
2. Manual cleanup afterwards (nothing is deleted automatically):
   - `sample/` (1.06 GB) — keep only `sample/input` if you still need
     the sample videos; `sample/output` moves with migration.
   - `.freebuff/` (937.5 MB) — session caches, safe to clear.
   - `%LOCALAPPDATA%/DubTool/jobs` (2.18 GB) — old app runs; Settings
     → Advanced now trims this automatically (`jobs_max_total_mb`),
     or delete old job folders by hand.
   - Hugging Face cache in `%USERPROFILE%` (2.06 GB) — superseded by
     the shared `<build root>/hf-cache` for source runs; delete the
     old one if you don't run the source directly anymore.
3. `.venv` (57.9 MB, dev only) recreates at the build root with
   `--apply`; `models/piper` and `vendor/` move with it.
4. Repository visibility question stands (still private?): with the
   5 MB pre-commit guard, large media can never be committed
   accidentally again.

## 4. The build

- Exe: `D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe`
- Zip: `D:\Projects\Vibe\Project_K_build\dist\DubTool-1.0.10-win64.zip`
  (187 MB, sha256
  `fe98c8cfa03d54329a08099dc484e24bd49b915d2cdeb2438b6cd58814d74721`)
- Version **1.0.10**, build id `Update 10 (ad373d8)`
- Screenshots: `D:\Projects\Vibe\Project_K_build\screenshots\update_10_ad373d8\`
  with `INDEX.txt` (10 PNGs, keys masked).

## 5. GitHub line

Update 9 was committed and pushed first, per your instruction (commit
`ad373d8` "Feature: complete AGENT_UPDATE_9 …" on `main`, remote up to
date at push time; tag `v1.0.9` pushed). Update 10's changes are
committed as `Feature: complete AGENT_UPDATE_10 …` and pushed with tag
`v1.0.10`; pre-commit gate green (only files < 5 MB staged). If the
push was refused by the network/credential layer, the reason appears
in the closing message; nothing else was pushed.

## 6. What I could not verify

- How the app feels on your display/DPI (screenshots + layout check
  only), the dub's sound, translation quality — user judgement.
- Windows SmartScreen/antivirus behavior for the unsigned exe.
- Sizes that depend on your machine/app state (`appjobs` 2.18 GB,
  old HF cache 2.06 GB) are reported as measured today, they will
  drift.

## 7. Noticed but not fixed

- `scripts/run_retention.sh` clears the sanitized home per run but not
  `tmp/rout_<level>`; a rerun after a killed run hits
  `code=output_exists`. Verification-only script; noted, worked
  around (deleted the stale output, reran `none`).
- edge-TTS's online service was degraded during the runs ("No audio
  was received" retries) — stage 4 stretched to ~40 min in the worst
  run; resilience worked as designed but net-dependent stages can be
  slow on bad days.
- The `.freebuff/` folder (937.5 MB) is the agent session store; only
  you can decide whether to clear it (it is not part of the app).

## 8. Review zip

`review/DubTool_review_update10.zip` — see the closing message for
the exact size; contains source diff, reports, review notes, docs,
sanitized verification logs, and the screenshots INDEX (no models, no
exes, no secrets).

Then stop and wait for your feedback.
