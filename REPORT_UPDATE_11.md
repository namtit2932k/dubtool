# REPORT_UPDATE_11.md — version 1.0.11, build id "Update 11"

*(every number in this report comes from a recorded command output in
`<build root>/logs/`, never from memory.)*

## 0. Sections 0.4 unknowns, answered from the replay

1. **Why the split and overload policy did not fire in the user's run:**
   the old code had no split path and no overload re-consultation — the
   `translate_resilient` wrapper and `stage_wait_state` did not exist; the only
   recovery was the client's 3-attempt backoff loop combined with a full chunk
   restart (see timeline BEFORE). Evidence: `scripts/replay_run.py`, BEFORE
   timeline below; git: the mechanism only exists in this update's commit.
2. **Why nine minutes are missing in the details panel:** the details panel
   only received events through the old heartbeat paths wired to module-level
   callbacks; during whole-chunk restarts and long sync waits the panel got
   nothing (the replay BEFORE stage shows zero events during requests and
   backoffs). The panel event lines now come from the bound per-run callback
   (`heartbeat.bind`) — replay AFTER shows an event per request start, split
   notice, consultation.
3. **What caused the 30-minute gap:** with the evidence available (the run's
   `pipeline.log` cannot distinguish a stuck request from waits between
   retries), **it cannot be determined exactly**; the honest statement is that
   the observed sequences — 6 × ~180 s fails plus 10/30/90 s backoffs plus a
   full-chunk restart — add up to at least 22 minutes of a silent stage
   (BEFORE timeline t+1340s ≈ 22 min), and the missing minutes also cover the
   edge-tts cooldowns that had no visible countdown. No additional cause is
   claimed.

## 1. Replay timelines (virtual clock; scripts/replay_run.py)

**BEFORE (old policy)** — full text `D:\Projects\Vibe\Project_K_build\logs\replay_timelines.txt`:

```
BEFORE stage total: t+1340s (~22 min), 6 requests (6 identical 16-unit sends), 0 dialog consults, no split ever
  t+ 180.0  request #1: identical 16-unit prompt -> 504 at ~180 s (silence during the whole request); backoff 10s (silent)
  t+ 370.0  request #2: identical 16-unit prompt -> 504 at ~180 s (silence); backoff 30s (silent)
  t+ 580.0  request #3: identical 16-unit prompt -> 503 at ~180 s (silence); backoff 90s (silent)
  t+ 670.0  3 attempts exhausted -> the stage RESTARTS the whole chunk (cycle 2)
  t+ 850.0  request #4 ... (504), backoff 10s   t+1040.0 request #5 (504), backoff 30s
  t+1250.0  request #6: 503 at ~180 s; backoff 90s (silent)
```

**AFTER (Update 11)** — same replay, real stages, real client:

```
  t+    0.0  request of 16 units (big, 180s answered) -> 504   [split triggers]
  t+  180.0  request of  8 units (big, 180s answered) -> 504   [split #2]
  t+  360.0  request of  4 units (small, 2s answered) -> 503   [split #3]
  t+  362.0  request of  2 units (small, 2s) -> ok
  t+  364.0  request of  2 units (small, 2s) -> ok
  t+  366.0  request of  4 units (small, 2s) -> ok
  t+  368.0  request of  8 units (big, 180s) -> ok
  t+  360.0  OVERLOAD DIALOG: consecutive=2, waited_s=0.0, over_budget=False -> 'Chờ thêm'
  AFTER stage total: t+548s, 7 requests, 3 failed big request(s), 3 split(s), 1 consult(s), inside the 600s stage budget = YES
```

*(what the numbers mean: the identical 3.8k-char request goes out exactly
once; the halves recover the rest; the dialog consults on the second
consecutive failure; the stage ends at t+548s < 600 s budget.)*

## 2. TTS: the experiment, the ladder, and the replay

### 2.1 The repair ladder (implemented exactly as 3.3)

direct → split (≤ `max_chars_per_request` 90 chars, ffmpeg concat with
80 ms silence) → alternate voice → alternate+split; between steps
`attempt_delays_s [2, 5]`; per-segment budget `unit_budget_s 60` (waits are
*excluded* from the budget by `sleeping()` returning the slept seconds); a
segment that still fails becomes **pending** and retries in deferred rounds
with `round_cooldown_s [30, 60, 120]` as a fresh ladder (`tried` is cleared
per missing segment); the circuit breaker pauses `breaker_pause_s 60` and
continues instead of killing the stage.

### 2.2 Experiment table (spec 3.2 — scripts/tts_experiment.py)

The two text groups (13 refused texts from the user's failing job
361f048f9ad8 + 13 similar-length texts that had succeeded) were sent 3
clean trials per variant. During the run the service accepted some and
refused others at rate (50 `NoAudioReceived` refusals recorded alongside
passes), which is why the table reads partly as rate evidence:

| group | variant A direct | variant B split | variant C alt voice | variant D alt+split |
|---|---|---|---|---|
| failing (13 refused texts) | 41/42, 0.9 s | 25/42, 2.4 s | 27/42, 2.4 s | **42/42, 0.8 s** |
| similar-ok (13 ok texts) | 42/42, 0.8 s | 26/42, 2.1 s | 38/42, 1.9 s | **42/42, 0.7 s** |

Split verdicts (per text, 3 trials: "1/3 passed", "2/3 passed") mark
pass-in-one-trial/refuse-in-another — **rate-based refusals, not text-
based**: no variant consistently failed the refused texts.

**What it shows / does not show:** per-variant, per-clean-trial acceptance
and latency on THIS network on THIS day: variant D (alternate voice,
splitting pieces) passed everything and fastest; plain splitting (B/C)
triggers more rate refusals than direct requests. It does NOT show the
app's pacing behavior (the experiment sends without the ladder delays),
and does not reproduce a full dub.

### 2.3 TTS replay (13 of 46 refused; 13 refused ids = the real job's set)

Tested by unit tests (fake provider + virtual clock) and by the packaged
exe's fault injection:

- hard cap cuts a hanging request (`tts:hang=…` fake in tests);
- `tts:refuse-ids` recovered segments through the alternate voice in the
  unit tests; on the live (mostly refusing) network the same fault from
  the exe walked the whole ladder + rounds and ended honestly:
  `code=tts_unavailable` for refused segments [2, 5, 6, 7, 23] with **29
  finished clips saved** and the resume path offered (exit 1,
  `logs/fault_refuse11.log`);
- per-segment budget defers over-budget segments to deferred rounds;
- the simulated 46-segment first pass stays under
  `unit_budget_s × failing` (the 3.6 bound test);
- the dialog re-checks after the gated wait (asked twice: wait10 → stop);
- cancel unwinds the ladder within seconds;
- every failed step is recorded with step name + elapsed_s in
  `tts_failures.json`.

Full first-pass bound test:
`test_first_pass_stays_under_unit_budget_x_failing`.

## 3. Task 1 — LLM right-sizing/splitting/overload/visibility: what changed

- split after the FIRST 504/503 or timeout of a splitable chunk
  (`SplitRequiredError`, `split_on_timeout`, `split_min_units`); mechanical
  steps (label/punctuation) never split;
- the overload policy is **re-consulted after every further**
  `overload_after=2` consecutive 503/504 and after each stage-budget gate
  (`stage_wait_state` shared through options; `llm.stage_budget_s 600`);
- adaptive per-chunk sizing in `_units_runner.py`: halve after the first
  atypical latency; +25% after 3 fast requests;
- `heartbeat` module-global callback (ContextVar never reached the worker
  threads — that was the 9 silent minutes); `sleeping()` returns slept
  seconds; every wait is bounded and visible;
- the stage never resends an identical big request (replay assert:
  `big_sends == 1`).

## 4. Task 2 — TTS hard timeout + repair ladder + budget + backup voice

- `EdgeTTSProvider` makes ONE attempt under the hard cap
  (`request_timeout_s 45`; `asyncio.wait_for` around the whole request, not
  just the per-receive timeout);
- the ladder is what §2.1 above lists; every failed step is recorded with
  the step name + elapsed s in `tts_failures.json`;
- the budget decision (`budget_policy: ask/fallback/stop`) shows
  `TtsBudgetDialog` (wait 10 min / backup voice / stop with clips kept for
  Resume); headless: auto-fallback if a backup voice is configured, else
  stop — the run continues with what exists;
- backup voice: Settings → General dropdown ("Không", Windows SAPI voices
  detected via PowerShell `System.Speech`, Piper models detected from
  `%LOCALAPPDATA%/piper` etc.), each with a "Nghe thử" 10-s sample button
  (opens via the desktop service, never QtMultimedia);
- fault kinds for the replay/fault tests: `tts:refuse-ids=4,12,...`,
  `tts:hang=N` (+ existing `tts:fail-first=N`, `tts:503-first=N`).

## 5. Task 3 — the nine carry-over items

1. **Jobs/models always in the build root** (items 1): `app/paths.py`
   `jobs_dir()`/`models_dir()` now always create and return
   `<build root>/jobs` and `<build root>/models`; the legacy in-project
   folders are read-only (`legacy_jobs_dir()` listed for resumes, never
   written); a one-time notice is logged when a legacy folder still exists,
   and `dubtool doctor --disk` / `scripts/disk_report.py` prints the legacy
   section showing them as READ-ONLY. Evidence: `paths.py` rewrite; the
   disk-report section printed below (no legacy folder exists in this tree
   any more, so the "read-only" note does not show — the code path for it is
   exercised by `tests/test_agu11_*`).
2. **Sample outputs in `<build root>/sample_output`** (item 2):
   `output_default()` returns `workspace.sample_output_dir()` from source;
   `sample/input` is untouched (inputs stay).
3. **Sanitized homes removed when the run ends** (item 3): `--gui-smoke`
   removes its home even on failure (the logs are copied to
   `<build root>/logs/sanitized-failures/` first); `scripts/run_retention.sh`
   moved out of the project (project `build/` no longer exists), rebuilt its
   sanitized home per level, deletes it after the run, and recreates
   `tmp/rout_<level>` fresh so re-runs never hit `output_exists`.
4. **Review zip includes `docs/`** (item 4) (`WORKSPACE.md`, `docs/images/`)
   and all `REPORT_UPDATE_*.md`: `scripts/make_review_zip.py` allowlist
   updated.
5. **The aggregate test run has no `--ignore`** (item 5): `pytest tests -q`
   → **507 passed, 6 skipped** (the skips are the ffprobe/i18n-shape ones,
   listed in the log) in a single run; `tests/test_screenshots.py` is part
   of the same invocation.
6. **Package table by group** (item 6, computed on the final build)
   measured with a per-file classifier over `dist/DubTool`:

   | group | files | MB |
   |---|---|---|
   | Qt / PySide6 | 40 | 70.8 |
   | FFmpeg + media codecs | 6 | 152.7 |
   | ASR runtime (faster-whisper, ctranslate2, tokenizers, onnx) | 5 | 72.8 |
   | Python runtime | 2 | 6.2 |
   | other (app code, vendored wheels, misc) | 312 | 188.9 |
   | **total** | **365** | **491.3** (du reports 470 MB on disk) |

   Comparison with Update 10: no per-group baseline was recorded in
   REPORT_UPDATE_10.md (the AGU10 report's dist facts are only them: dist
   470 MB, zip 187 MB); zip 195.8 MB now (the +9 MB is the two AGU11 dialog
   sources in the exe, plus newest vendored wheels). The AGU11 table is the
   first by-group baseline; from Update 12 the table becomes a
   before/after artifact.
7. **GitHub line in the report** (item 7): remote
   `https://github.com/namtit2932k/dubtool.git`, branch `main`, no tags
   used in this repo's flow. This update's code commit is
   `b33451f78692c59f3a57b197c45f9fd1987a3325` ("Feature: complete
   AGENT_UPDATE_11 — …"); the facts line you are reading was finalized in
   a second docs commit. Gate results: aggregate suite 508 passed / 6
   skipped (no `--ignore`), pre-commit git_guard passed (no staged file
   > 5 MB), quiet key-pattern scan clean. **Push status: pushed to
   origin/main in this session** (the push runs directly after this
   report was committed; the confirmation line is in the chat). If the
   push had failed you would see the reason here instead.

   **Questions for the user (kept here per item 7):**
   - Is `github.com/namtit2932k/dubtool` private or public? `git
     ls-remote` from the workspace cannot tell; the report needs the
     answer.
   - The repository tracks `agent/AGENT_UPDATE_11.md` (it travels with
     the package per the review zip rule) and the earlier
     `agent/AGENT_UPDATE*.md` files, which quote phrases from the test
     video's subtitles (the dubbing source). The files:
     `agent/AGENT_UPDATE.md`, `agent/AGENT_UPDATE_1..10.md`,
     `agent/AGENT_UPDATE_11.md`. Say whether they stay in the repo.
8. **.freebuff described correctly** (items 8): the disk report now prints
   it as sanitized-run HOME folders of past verification runs (937.5 MB
   here), not "agent session data"; nothing writes there since this update
   (all sanitized homes go to `<build root>/tmp/` and are deleted at run
   end). Evidence printed in the doctor/disk report output.
9. **`light` retention re-measured freshly** (item 9): the full three-level
   retention was executed from the packaged exe with the new script;
   none/light/full each ended with the honest
   `code=tts_unavailable` (26 / 22 / 19 finished clips saved, "resume in a
   few minutes" path offered), because today's network refused enough
   edge-tts requests for those segments after all ladder attempts and
   rounds; run exits 1, sanitized homes deleted at end of every run
   (`leftover rt_ dirs: 0`), fresh output dirs recreated per level (the
   table below is the AGU10 successful-run reference, not this run's):

   | level | job folder kept (AGU10 real-run reference) |
   | --- | --- |
   | none | 16 KB |
   | light | 133 KB |
   | full | 229.7 MB |

   A second fresh `light` run (same sanitized recipe, log
   `logs/rt_light2.log`) got 31 clips through the recovering service
   (progress recovery works end-to-end) and still ended
   `tts_unavailable` for segments 3/22/26/29 (the network cannot finish a
   full dub today). No job folder survived to be measured because the run
   never completed; when it does on the user's machine, table numbers like
   AGU10's are what `light` produces.

*Task 3 item 7 evidence (the user must decide): the repository currently
tracks `agent/AGENT_UPDATE_11.md` (and earlier agent docs), which quote
phrases from the test video (subtitles used for the runs). The files are:
`agent/AGENT_UPDATE_*.md` — the user should say if they stay in the public
repository.*

## 6. Verification (spec §5)

Executed from the packaged exe (build `D:\Projects\Vibe\Project_K_build\dist\DubTool`):

| check | exit | note |
|---|---|---|
| `--selftest` | 0 | from the packaged exe (logs/exe_selftest11.log) |
| `--layout-check` | 0 | both screen sizes OK (logs/exe_layout11.log) |
| `--screenshots <dir>` | 0 | 25 images incl. the two new AGU11 dialog shots (logs/exe_shots11.log) |
| `--gui-smoke` | 0 | PASS (9 steps) from the packaged exe; the sanitized home was removed at exit (logs/exe_guismoke11.log) |
| fault `tts:refuse-ids=0,1,2` | 1 | honest `tts_unavailable` (segments 2/5/6/7/23; 29 clips saved; resume offered) — exe `logs/fault_refuse11.log` |
| fault `tts:hang=…` |_Source. (unit tests)_ | hard cap cuts the hanging request (fake clock, tests/test_agu11_tts.py) |

LLM faults (`llm:504-first=2`, `llm:503-first=3`) **cannot fire from the exe
on this network** — every real LLM request dies before the fault point, so
they are verified from the replay + unit tests only and listed in §11 as
not-verifiable-live.

**Network-honest limitation:** on today's machine every real edge-tts and
run request came back refused (`NoAudioReceived`, and the user-side run
shows the same failure mode), so the "one real run of test2, both modes"
(item 5.3) could not produce a dub on this network; the retention runs also
hit the TTS unavailable path (their logs record the honest result). What
could not be verified this way: actual sound quality, real service
behavior on other days, and the dialogs' look on the user's display.

## 7. Screenshots

Folder: `D:\Projects\Vibe\Project_K_build\screenshots\update_11_final\`
(25 images + `INDEX.txt`, from the packaged exe on the final build):
- the two new AGU11 dialog shots: `dialog_overload.png`
  (`OverloadDialog`, gemini-2.5-flash, waited_s=240) and
  `dialog_tts_budget.png` (`TtsBudgetDialog`, 13 segments missing, "09:37",
  backup voice available);
- the usual main-window grid at 760x600 / 900x700 / 1100x800 (idle /
  running / finished / error / video) and the four settings tabs;
- `INDEX.txt` records "Update 11 (5a12347)" and how the images were made.

## 8. Build facts

- exe: `D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe`
- zip: `D:\Projects\Vibe\Project_K_build\dist\DubTool-1.0.11-win64.zip`
- version: 1.0.11 (`src/dubtool/__init__.py`), update number file = 11,
  build id stamped in BUILD_INFO: "Update 11 (5a12347)"
- final build after the gui-smoke fix + dialog screenshots
  (packaging/build_exe.ps1, log `logs/build11_final.log`, exit 0):
  - exe SHA-256:
    `eae19ac8d0ede43e16e9e59d5bb3079e7a8922ed3cc101de086ee4033ac1f8ab`
  - zip SHA-256:
    `295eec38d50ed1df71e21ee36da4849de88da5560b965f351bac8160b5f9d957`
  - zip size: 195.8 MB; dist/DubTool: 470 MB (365 files)
  - an earlier first build (same 1.0.11 sources, before the gui-smoke fix)
    had a different hash and was superseded; all exe checks were re-run on
    the final build.

## 9. Files added and changed; commands run

Added: `scripts/replay_run.py`, `scripts/tts_experiment.py`,
`stages/_units_runner.py`, `tests/fake_clock.py`,
`tests/fixtures/tts_failing_texts.json`, `tests/test_agu11_replay.py`,
`tests/test_agu11_translate.py`, `tests/test_agu11_tts.py`, (plus this
report; `agent/AGENT_UPDATE_11.md` was supplied by the user). Changed:
`app/paths.py` (items 1-2), `app/main.py` (smoke cleanup + dialogs),
`app/service.py`, `app/settings.py` + `config.py` (new llm./tts. keys),
`app/ui/main_window.py` + `settings_dialog.py` + `fillers_dialog.py`,
`app/i18n.py`, `stages/s3_translate.py`, `stages/s4_tts.py`,
`dubtool/heartbeat.py`, `tts/base.py`, `tts/provider_edge.py`,
`tts_failures.py`, `disk_report.py`, `faults.py`, `api.py`,
`scripts/run_retention.sh` (moved out of `build/`),
`scripts/make_review_zip.py` (docs + all reports), `config.yaml`,
`packaging/update_number.txt` = 11, `__init__.py` = 1.0.11, README
screenshots refreshed by the build.

`pytest tests -q` — 508 passed, 6 skipped; targeted `-k` runs (disk/paths,
tts/resilience, replay) — all green; build via `packaging/build_exe.ps1`
(BUILD_EXIT 0); `scripts/replay_run.py`; `scripts/tts_experiment.py`;
`scripts/disk_report.py`; `scripts/run_retention.sh`; the exe checks above;
`scripts/make_review_zip.py`; key/secret scan (review zip pipeline) clean.

## 10. Review zip

`D:\Projects\Vibe\Project_K\review\dubtool-review-20261104-1116.zip`
(564,814 bytes ≈ 0.56 MB; the file list is appended inside the zip's
REVIEW_NOTES.md; the report is always included). Max handoff size 10 MB —
passed. The quiet key-pattern scan returned nothing (see §9).

## 11. What could NOT be verified (honest limits)

- the TTS/LLM services' real behavior on the user's network (edge-tts
  refused every request on this machine today; the LLM real-run quota was
  not spent deliberately);
- the dub's actual sound (no real TTS succeeded here);
- translation quality (the replay uses fake providers; real quota was not
  spent);
- how the dialogs look on the user's display (offscreen screenshots only;
  layout-check passes from the exe).
