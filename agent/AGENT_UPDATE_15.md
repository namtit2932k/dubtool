# AGENT_UPDATE_15.md — Make the release process mechanical (no tag, push, or release without gates), close the Update 13 and 14 leftovers, validate the new voice settings on the live service, and generate every evidence table by script

Fifteenth update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_14.md` (Update 14 has no specification file) still apply: ground rules, data contracts, resumability, secret safety, the review zip rule (`AGENT_UPDATE.md` §7.2), the GitHub publishing procedure (`AGENT_UPDATE_6.md` §8), the **Definition of done** (`AGENT_UPDATE_7.md` §1 rule 5), and `AGENT_UPDATE_13.md` §1 and §7. The version rule is `1.0.<update number>`: this update is **1.0.15** (`packaging/update_number.txt` = 15).

**Why this update exists.** The Update 14 review found that the release steps were done in the wrong order and by hand, and that several things promised in Update 13 are still open. This update turns the process into code, so that it cannot be skipped again, and then closes the open items.

---

## 0. Evidence (from the Update 14 package and report)

### 0.1 Process
1. **Update 14 was not specified.** The report says it came from follow-up requests (memory and SSD pressure and pytest hangs, "Option 1" voice-service stability, workspace deletion guards, multi-key rotation, a Windows fallback voice, shipping). Work outside a specification is allowed only if the same release process still applies; it was not applied.
2. **Order of events (the report's own words):** the code commit `045660d`, the tag `v1.0.14`, and the **GitHub Release with the `DubTool-1.0.14-win64.zip` asset** were made **before** `scripts/preflight_gates.py` was fixed (it had exit code 1 on UTF-8 paths) and before the report and the review package existed. The repository is **public**, so that release is already downloadable.
3. **The exe does not match the tag.** `BUILD_INFO.txt` says commit `63c85a1`; the release commit is `045660d`, which changed 39 files (+2363/−470) after that build, because `build_exe.ps1` built the working tree. The report acknowledges the stamp mismatch and says the Python and UI files match; this cannot be proven from the tag.
4. **Only a `pre-commit` hook exists.** `scripts/install_git_hooks.py` installs `pre-commit` only. Nothing blocks a push, a tag, or `gh release create`, which is how the gates were skipped.
5. **The report lacks the required GitHub evidence:** `AGENT_UPDATE_12.md` §7.3 requires the outputs of `git log -1 --format=%H`, `git status -sb`, and `git ls-remote origin refs/heads/main`; the Update 14 report gives a URL, a commit hash, and a tag list only.
6. **The release publishes binaries without a license decision.** The repository has no `LICENSE` ("all rights reserved"), and the zip contains a GPL FFmpeg build (`ffmpeg.exe`; `ffmpeg -version` shows `N-118281-gb76053d8bf-20250111`) next to LGPL components. `THIRD_PARTY.md` is copied into the zip, but no release notes, source offer, or license texts accompany the asset. This is not legal advice; it is a gap the user must decide on and the release must at least document.

### 0.2 Update 14 code (read in the package; not run)
7. `tts/edge.py` now runs one persistent asyncio loop in a daemon thread with `atexit` cleanup and a `close()`; stage 4 uses a `ThreadPoolExecutor` (`concurrency: 2`). `workspace.safe_rmtree_sanitized()` refuses symlinks/junctions and anything not strictly under `…/tmp/sanitized/<run_id>`. Both look sound.
8. **The new voice settings were never run against the live service.** `config.yaml` now has `concurrency: 2`, `max_rps: 1.5`, `limiter_floor_interval_s: 2.5` (was 5), `breaker_consecutive: 6` (was 3), `breaker_pause_s: 15` with a `[10, 20, 30]` schedule (was a flat 60 s), and `direct_retries: 1`. The report says so ("Live `--tts-probe` … not run"; the five `--fault` runs were done on 1.0.13, not 1.0.14). All earlier evidence says that refusals depend on the service state and on request rate (more requests meant more refusals; handshake `503` from the Microsoft endpoint). More workers, a faster start rate, a shorter floor, a longer tolerance, and shorter pauses could make refusals worse.
9. `tests` ran as `566 passed, 9 skipped`. The **9 skipped tests are still not explained** (Update 13 had 9, Update 12 had 6).

### 0.3 Update 13 items that are still open (re-checked in the Update 14 package)
10. **Fixed since the Update 13 review (do not redo):** the model dropdown default is `Gemini 3.5 Flash-Lite` in `docs/images/ready.png`; `docs/images/INDEX.txt` now says 1.0.14 (`Update 14 (63c85a1)`); the `--mode` alias exists (in `app/main.py`).
11. **Still open:**
    - `docs/images/dialog_settings_tab1_Mô_hình.png` (the removed Models tab) is still in the public repository, and `docs/images/` holds other images that nothing references.
    - Catalog labels still read "(tra phi)" without diacritics (`packaging/models.yaml`) while the UI adds "(trả phí)", which produced the doubled suffix seen in Update 13's screenshot.
    - `docs/images/ready.png` shows the file card named **`DubTool.exe`** above the line "test.mp4 · 1.0 min" (the screenshot states in `app/layout_check.py` set the info text to `test.mp4` but call `file_card.show_file(win.video_path.name, …)` with the exe's own path), and the output folder is still the real path `D:\Projects\Vibe\Project_K`.
    - **`AGENT_UPDATE_13.md` §5.1:** the sentence-mode versus segment-mode comparison on the demo video was never backed by raw data. The Update 13 table had a unit count (37) and a share of non-sentence-ending fragments (over 60%) that disagree with the user's `translated.json` (79 segments, 20 not ending a sentence, about 59 sentence ends), text that does not appear in that transcript, fit-status counts that did not add up to the units, and no input path or hash. Update 14 did not revisit it.
    - **`AGENT_UPDATE_13.md` §5.5:** the Update 13 voice experiment showed the exe at 97.5/97.5/92.5% against 100/100/100% from source, with an unexplained "Other" category, and the report drew a conclusion ("robust", "the limiter protects") that the data did not support.

---

## 1. Rules for this update

1. **No release by hand.** From now on, `git tag v*`, `git push` of a tag, and `gh release create|upload|edit` happen **only** through `scripts/release.py` (task 1). If you run any of them directly, the update is not done.
2. **Gates before anything public**, in this order, each blocking the next: clean working tree → aggregate tests → pre-push gates and history scan → build from the clean commit → sanitized verification → documentation and images → review package → push → tag → release → post-release verification.
3. **No hand-written evidence.** Every table in the report comes from a script that reads job files and prints its own totals (task 5). If a total does not add up, the script fails.
4. Never print, log, or write an API key of any provider. Never read `.env` into output. Never commit a key.
5. Do not delete, move, or re-point existing tags or releases, and never use `--force`. Do not rewrite history. Do not run `git gc`, `git prune`, or `git reflog expire`.
6. Never write logs or temporary files into the project root. Never `git add -A`.
7. The report is written in complete sentences, re-read, and every number is followed by the command that produced it. The report is generated from `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md` (tasks 1 and 2) wherever possible.

---

## 2. Task 1 — A release script and a `pre-push` hook

### 2.1 `scripts/release.py N [--dry-run]`
One entry point, each step fail-fast, each step logged to `<build root>/logs/release-N.log`:

1. **Preconditions:** `git status --porcelain` is empty (no uncommitted or untracked-but-unignored files); the branch is `main`; `origin` is set and reachable; `gh auth status` works (if `gh` is missing, stop and say so); `packaging/update_number.txt` equals `N`; `__version__` equals `1.0.N`.
2. **Tests:** the full suite as one run, `pytest tests -q --timeout=120 -rs`, no `--ignore`. Save the output and **extract the skip reasons**; any skip without a written, environmental reason fails the step (task 3.5).
3. **Gates:** `scripts/preflight_gates.py` and `scripts/scan_history_secrets.py` (both must exit 0).
4. **Build from the clean commit, not the working tree:** `git archive HEAD` (or a temporary `git worktree`) into `<build root>/tmp/release-src/`, and run the build script there. `build_exe.ps1` must **refuse a dirty tree** and write `git rev-parse HEAD` (full 40 characters) to `BUILD_INFO.txt`. Verify afterwards that `BUILD_INFO.txt` commit equals `HEAD`.
5. **Sanitized verification of that build:** `--version`, `--selftest`, `--layout-check`, `--gui-smoke`, `--screenshots` (real platform), the way `AGENT_UPDATE_7.md` §8 describes.
6. **Docs and images:** run `scripts/update_readme_images.py` (which must sync, task 3.1) and `scripts/check_readme_links.py`. If this changes tracked files, commit them **and return to step 1** (the build must be made from the final commit; allow at most one loop).
7. **Review package:** `scripts/make_review_zip.py`, containing `docs/`, `REPORT_UPDATE_N.md`, `REVIEW_NOTES.md`, `DOD_REPORT.md`, `RELEASE_EVIDENCE.txt`, and the skip-reason list. Record its name and SHA-256.
8. **Definition-of-done check:** `scripts/definition_of_done.py` (task 2) must report no failure.
9. **Gate stamp:** write `.git/dubtool-release-ok-N` (inside `.git`, never committed) containing the `HEAD` hash, the zip SHA-256, the review zip SHA-256, and the time.
10. **Publish:** `git push origin main`, then create the tag `v1.0.N` on `HEAD` (annotated), then `git push origin v1.0.N`, then `gh release create` with the zip, `SHA256SUMS.txt`, and the release notes of 2.3.
11. **Post-release verification** (`scripts/verify_release.py`): the remote `main` equals `HEAD`; `git ls-remote --tags origin` shows `v1.0.N` pointing to `HEAD`; the release asset's SHA-256 (downloaded again) equals the local zip's; `BUILD_INFO.txt` inside the downloaded zip carries the same commit. Write all of it to `RELEASE_EVIDENCE.txt`, including the three raw outputs required by `AGENT_UPDATE_12.md` §7.3 (`git log -1 --format=%H`, `git status -sb`, `git ls-remote origin refs/heads/main`), the full tag list from `git tag -l` and `git ls-remote --tags origin`, and the release URL.

`--dry-run` performs steps 1–8 and stops before the stamp. **This update's own release (v1.0.15) is the first one made with this script.** If authentication for `gh` or `git` fails, stop with a clear message and **do not tell the user the release happened**.

### 2.2 `pre-push` hook (installed by `scripts/install_git_hooks.py` next to the existing `pre-commit`)
- Runs `scripts/preflight_gates.py --quick` (secret scan of the pushed commits, forbidden paths, file sizes) for **every** push and blocks on failure.
- **Blocks any push of a ref that matches `refs/tags/v*`** unless `.git/dubtool-release-ok-<N>` exists, its `HEAD` hash equals the pushed commit, and the recorded review-zip hash still matches the file on disk. There is no bypass variable for tags.
- Blocks a push of `main` that contains a commit changing `packaging/update_number.txt` or `__version__` unless the same stamp exists (so that a version bump cannot reach `main` without the release gates).
- The hook never contacts the network, prints only file paths and reasons (never secret values), and refuses to overwrite a `pre-push` hook it did not write (as the `pre-commit` installer does).
- Tests in a temporary Git repository with a bare remote: a tag push without the stamp is blocked; with a stale stamp is blocked; with a valid stamp passes; a push containing a fake key is blocked; an ordinary docs push passes.

### 2.3 Release notes and third-party notices
`packaging/RELEASE_NOTES_TEMPLATE.md` generates the body of every release (Vietnamese first, English below) with: the version and commit; the SHA-256 of the zip; "chưa có chữ ký số (Windows SmartScreen có thể cảnh báo)"; the license status of the application ("chưa chọn giấy phép — bảo lưu mọi quyền" until the user chooses); a **third-party section** listing the bundled components with their licenses, **including the exact `ffmpeg -version` string and `--enable-gpl` configuration line, the URL of the FFmpeg build that was downloaded and of its source**, and where the license texts are in the zip (add a `licenses/` folder to the package with the FFmpeg, LGPL, and other license texts that the components require; report the list); and the notes that `edge-tts` is an unofficial interface and that a paid provider receives the English text of the video. State in the report that this is a documentation gap closed, not a legal opinion, and ask the user to choose a license.

### 2.4 The already published `v1.0.14`
Do **not** delete or move the tag or the release. Run `scripts/verify_release.py --tag v1.0.14` and report: the tag's commit, the `BUILD_INFO` commit inside the published zip, the SHA-256 of the asset, and the list of source files that differ between `63c85a1` and `045660d` (`git diff --stat 63c85a1 045660d`). Then **ask the user** which of these they want, with the exact commands, and do nothing until they answer: (a) mark the release as a pre-release and prepend a note that it is superseded by v1.0.15; (b) leave it as it is. (Both keep the history.)

---

## 3. Task 2 — A mechanical Definition-of-done checker

`scripts/definition_of_done.py` writes `DOD_REPORT.md` (a pass or fail table, with the evidence line for each row) and exits non-zero on any failure. It checks at least:

1. **Version consistency:** `update_number.txt`, `__version__`, `pyproject.toml`, `packaging/version.txt`, the exe's version resource, the zip name, and `BUILD_INFO.txt` all agree on `1.0.N`; the build id says `Update N (<40-char HEAD>)`; **`BUILD_INFO` commit equals `HEAD`**.
2. **Build artifacts:** the exe and the zip exist at the reported paths with the reported sizes and SHA-256.
3. **Screenshots:** `docs/images/INDEX.txt` says platform `windows`, the same version and build id, and a time not earlier than the build; every required state exists; **every file in `docs/images/` is referenced by the README or listed in `INDEX.txt`** (no stale image survives); every PNG is at least 600 px wide and not mostly white; the README links resolve to tracked files.
4. **Tests:** the saved `pytest -rs` output has no failures and **every skipped test has a reason that is documented in `tests/SKIPS.md`** (test id, reason, condition under which it runs).
5. **Report completeness:** `REPORT_UPDATE_N.md` contains the required sections (the sections that `AGENT.md` §12 and the specification of the update list), the GitHub evidence block is present with the three raw `git` outputs, and the tag list shows one `v1.0.N` tag per update with no duplicates.
6. **Review package:** the zip contains `docs/`, the report, the notes, `DOD_REPORT.md`, `RELEASE_EVIDENCE.txt`, and `tests/SKIPS.md`, and contains no `.env`, no key-like string (except the marked fake fixtures), and no file over 5 MB.
7. **Messages:** no user-visible string, log line, or diagnostic contains a specification identifier (`AGU`, `AGENT_UPDATE_`, a task number); config comments are exempt.
8. **Reports versus code:** every file path named in the report exists in the review zip or in the repository.

Each row prints the command it ran. Add tests for the checker with a fake project tree (each rule fails when its condition is broken).

---

## 4. Task 3 — Close the Update 13 and 14 leftovers

1. **README images sync.** `scripts/update_readme_images.py` copies the current set into `docs/images/`, writes `INDEX.txt`, and **deletes every file in `docs/images/` that is not part of the current set** (a normal commit; history remains). That removes `dialog_settings_tab1_Mô_hình.png` and every other stale image. The README references only images that exist in the set.
2. **Labels.** Remove the cost suffix from the catalog labels (`packaging/models.yaml` carries only the model name); the UI adds the single suffix "(trả phí)" and "(cần khóa API)". Fix the diacritics. Test: no catalog label contains "phi" without diacritics, and no dropdown item shows a duplicate suffix.
3. **Screenshot states.** In `app/layout_check.py` and the screenshot harness use a fake video path whose name is `test.mp4` (the file card must show `test.mp4`, the info line must come from the same source) and the neutral output folder `C:\Videos\Output`. Add assertions in the harness (`win.video_path.name == "test.mp4"`, the output folder is the neutral path) and a line `masked paths: yes` in `INDEX.txt`. Check for the real project path (`D:\Projects\Vibe`) in every state's widget texts and fail if it appears.
4. **Realistic running state.** The "running" image must show one active stage at a time with consistent counters.
5. **The 9 skipped tests.** Run `pytest -rs`, write `tests/SKIPS.md` with each skip (test id, reason, the condition under which it runs). Fix any skip that is not environmental. Keep the result in the review zip.
6. **Spec identifiers.** Remove `AGU…`/`AGENT_UPDATE…` from every user-visible string and log message (config comments and developer docstrings may keep them).
7. **`AGENTS.md` (untracked).** Keep it untracked, and state in the report that its rules do not weaken any rule of the specifications (list any conflict you find).

---

## 5. Task 4 — Validate the Update 14 voice settings on the live service

The goal is to decide the defaults from data, not from intention. Run on the **1.0.15 exe and from source, in the same time window**, interleaved and randomized per text.

1. **Conditions:** (A) the Update 14 settings (`concurrency: 2`, `max_rps: 1.5`, `limiter_floor_interval_s: 2.5`, `breaker_consecutive: 6`, `breaker_pause_schedule_s: [10, 20, 30]`, `direct_retries: 1`) and (B) the conservative baseline of Update 12 (`concurrency: 1`, `max_rps: 1.0`, floor 5 s, `breaker_consecutive: 3`, flat 60 s). At least **60 requests per condition and per environment**, a short fixed **control request every 10 requests**, and the same voice.
2. **Report** acceptance per condition and environment; the number of handshake `503`, `NoAudioReceived`, timeouts, and **every other failure kind by name** (no "Other" bucket); the control results; the time per request; and a two-sided Fisher exact test for each exe-versus-source pair and for A versus B (state the p-values, and what they do not show; a healthy service window cannot speak for the unhealthy ones).
3. **Decide the defaults by a written rule:** keep the Update 14 settings only if A's acceptance is not lower than B's by 5 points or more in either environment and A has no more handshake `503` than B; otherwise revert the defaults to B in `config.yaml` (the code and the tests stay) and say so. If the service state during the probe is unhealthy (control below 90%), repeat later and report both.
4. **One real run:** the user's demo video, Fast model (`gemini-3.5-flash-lite`), sentence mode, with the chosen defaults: the TTS stage time, the number of refusals, the repair-ladder steps used, the voice count (must be one), and the total time.
5. **The five fault runs** (`--fault llm:503-first=8`, `--fault llm:429-day`, `--fault llm:504-first=2`, `--fault tts:refuse-ids=3,5,8`, `--fault tts:hang=1`) **on the 1.0.15 exe**, each as its own command with its own timeout: exit code, stage times, requests used, the model per unit, the voice count. Report failures as failures.

---

## 6. Task 5 — Evidence tables generated by script

`scripts/evidence_table.py <job folder> [--second-job <job folder>]` reads `transcript.json`, `units.json`, `dub_units.json`, `translated.json`, `schedule.json`, `state.json`, and prints:

- the input file path, its duration, its SHA-256, and the model used;
- the number of segments, how many do not end a sentence, the number of units, the number of TTS requests, how many TTS texts do not end a sentence, the `fit_status` counts (**asserting that they add up to the number of units**), the shifted and overlapping clips, the maximum shift and the lag at the end, the cue count and the longest cue;
- the **first 12 units as raw JSON** (English, Vietnamese, ids, times, model, voice) and, for segment mode, the corresponding old fragments from `translated.json`.

Run it for the demo video in **sentence mode and in segment mode**, and for `balanced` and `faithful` (`AGENT_UPDATE_12.md` §3.2 default rule), and paste the script's output unedited. **If the demo video is not in `sample/input/`, stop and ask the user for its path**; do not use another video and do not write the sentences by hand. Do not say which mode is better. The user reads the units and judges.

---

## 7. Verification and report

Run `scripts/release.py 15`. If a step fails, the report says which one and stops there (the release does not happen). The report includes, generated or pasted from the files:

1. Files added and changed; the aggregate test counts and the skip list.
2. `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt` verbatim (including the three raw `git` outputs, the tag list, the release URL, and the asset hash check).
3. The `v1.0.14` analysis of 2.4 and the question for the user (no action taken).
4. The live voice results of task 4 with the decision taken, the real run, and the five fault runs.
5. The evidence tables of task 5.
6. The updated third-party section and the `licenses/` folder list, and the license question for the user.
7. The build: absolute path of the exe and the zip, version, build id with the full commit, sizes, SHA-256.
8. What you could not verify (the service on other days, the dub's sound and the translation quality, how the windows look on the user's display, Anthropic with a real key, and OpenAI without credit).
9. Anything you noticed but did not fix.
10. The review zip path, size, and SHA-256 (the same file the user will upload).

Then stop and wait for the user's feedback.
