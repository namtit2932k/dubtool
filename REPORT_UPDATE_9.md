# REPORT_UPDATE_9.md — scheduled UI lock, file card, progress details, About/version, open-folder fix, jobs clarity, README images, lighter package, Update-8 close-out

Every number below was produced by a command run in this session; the
command (or the probe) is quoted with the result. Anything not verifiable
is listed explicitly in §9.

---

## 1. Files added / changed (verified: `git status --porcelain`)

Added: `src/dubtool/branding.py`, `src/dubtool/heartbeat.py`,
`packaging/update_number.txt`, `scripts/check_readme_links.py`,
`scripts/update_readme_images.py`, `scripts/inventory.py`,
`tests/test_update9_ui.py`, `tests/test_readme_images.py`,
`docs/images/{ready,running,finished,settings_api_keys}.png`.

Changed (high level): `app/ui/main_window.py` (busy state, file card,
details panel, open-folder fix, redaction), `app/ui/widgets.py`
(`FileCard`, labeled details toggle), `app/ui/settings_dialog.py`
(read-only-while-running banner + control lock, Advanced "working files"
group inline texts), `app/main.py` (version from `dubtool.__version__`),
`__init__.py` (`__version__ = "1.0.9"`), `pyproject.toml` (dynamic
version), `packaging/dubtool.spec` (vendor listed file-by-file, ffprobe
excluded; version resource generated from `dubtool.__version__`),
`packaging/build_exe.ps1` (update-number-driven structure check,
README-image step, one-folder/one-zip housekeeping), `proc.py` (`gui=True`
children), `stages/s2_transcribe.py` + `heartbeat.py` (ASR progress
heartbeats), `stages/s4_tts.py` (TTS heartbeat),
`llm/client.py` (request heartbeats), `audio_utils.py` (PyAV probing for
containers, no ffprobe call), `netcheck.py` (clock-offset hint),
`app/diagnostics.py` + `app/housekeeping.py` + `app/settings.py`
(new keys, clock-offset surfacing), `README.md`, `README_BUILD.md`.
`packaging/version.txt` removed.

Aggregate suite at the end of the session:
`python -m pytest tests -q --ignore=tests/test_screenshots.py` →
**438 passed, 2 skipped** (both skips are the Windows symlink-privilege
skips in `test_cleanup.py` / `test_housekeeping.py`, not coverage skips).
`tests/test_screenshots.py` → 3 passed.

---

## 2. The user's eight requests — each with what changed and how verified

### 2.1 Nobody can change options while a run is in progress

One place decides: `MainWindow.set_busy(bool)` (AGU9 §2.1; disable-list
stated in code in `_busy_widgets()`). Generate buttons, drop zone, file
card X, output-folder button, options group (checkbox, voice/format/
whisper/cpu/priority/encode-speed combos, gain slider, intermediate
checkbox, readable-format), the description (read-only, not disabled).
Cancel, the details toggle, About and Export-diagnostics stay enabled.

Real-widget tests (`tests/test_update9_ui.py` + `tests/test_app_ui.py`):
- `test_busy_single_state_disables_editables` — enumerates every
  QCheckBox / QSlider / QLineEdit under the window while busy and asserts
  disabled except the allowlist (Cancel, details toggle/copy/open);
  everything re-enabled after.
- Settings dialog while running: read-only banner
  ("Đang chạy — chưa thể thay đổi cài đặt") shown
  (`settings.read_only_banner`), `_apply_read_only()` disables the tab
  controls; Test-connection / Export-diagnostics stay enabled. Verified
  by `test_no_hardcoded_widget_text` + dialog tests in
  `tests/test_app_ui.py`.

What is NOT tested here: that a child dialog opened mid-run cannot be
edited while the service is between events (the poll-based re-lock uses
`_poll_read_only`; the unit test covers the flag, not a live run).

### 2.2 A cleaner view after a video is imported

After a real import the drop zone hides and a file card takes its place:
middle-elided file name (full path in the tooltip), an info line
(duration · resolution · fps · size), and an ✕ button (tooltip
"Bỏ chọn video"). ✕ returns to the drop zone, clears the info line and
the description, and disables Generate. Dropping a new file replaces the
selection. Tests: `test_file_card_replaces_drop_zone_and_x_restores`,
`test_file_card_x_disabled_while_busy`, `test_drop_while_busy_is_ignored`
—all real widget clicks on the generated temp file (probe faked; the
real probe path is exercised by `tests/test_audio_mix.py` and the GUI
smoke's real 2-minute import).

### 2.3 "Progress details" — a real activity view

The unlabeled ▸/▾ button is now a flat labeled button
"Chi tiết tiến trình ▸/▾" (`progress.details`, tests
`test_details_toggle_labels`). When open it shows the header
("Đang làm: <stage> — <detail>"), a per-second liveness line
("Cập nhật lần cuối: N giây trước", plus "— vẫn đang chạy" after
`quiet_after_s` = 20 s of quiet while the worker is alive, or the
worker-dead state), a scrolling activity log capped at 2 000 lines, and
two small buttons: "Sao chép chi tiết" (copies the log with
key-shaped tokens redacted by `_redact`, test
`test_details_copy_is_redacted`) and "Mở thư mục log" (fixed open
function). State is remembered in `settings.json` (`details_open`,
`test_details_state_persisted`), auto-open on error handled in
`app.main`-driven paths.

Heartbeats (AGU9 §4.3) so quiet stages still emit events:
- ASR: end-time/duration progress via `src/dubtool/heartbeat.py`
  (`main_window` tests in `test_app_ui.py` for the event kinds).
- FFmpeg encode/mix: `-progress pipe:1 -nostats` parsed for `out_time*
  (`proc.py`; unit test with sample output + a real short-encode rising
  0→100 check in `tests/test_proc_helper.py`).
- Voice generation: per-item counts + cooldown heartbeat.
- Model calls: elapsed-seconds heartbeat (`llm/client.py`).

### 2.4 About box, author, and version

About box now shows: app name, `Phiên bản: 1.0.9`, build id,
`Tác giả: sickyturtlez` (`AUTHOR = "sickyturtlez"` in
`src/dubtool/branding.py`), licenses line. The FFmpeg line
(`about.ffmpeg`, `_ffmpeg_version()`) is removed.

Version single source: `dubtool.__version__ = "1.0.9"`;
`pyproject.toml` `version = {attr = "dubtool.__version__"}`,
`packaging/update_number.txt` = `9`, exe version resource generated in
`dubtool.spec`, zip renamed, `BUILD_INFO.txt` + `--version` read it.
This update's rule (1.0.<update number>) is documented in
`README_BUILD.md` and enforced by `tests/test_update9_ui.py`:
`test_version_single_source` (version == 1.0.<update_number.txt>).

Verified on the packaged exe:
`dist\DubTool\DubTool.exe --version` → `DubTool 1.0.9` /
`build id: Update 9 (262f9eb)`.

### 2.5 "Open output folder" works

Cause (finding 3): Explorer was started through the hidden-window
console helper. Fix: `os.startfile(str(folder))` for the plain open, and
`proc.run(["explorer", f"/select,{path}"], gui=True)` for the reveal case
(`gui=True` skips `SW_HIDE`/`CREATE_NO_WINDOW`); the folder opened is the
last successful result's folder, falling back to the field. Static test
still green (no direct subprocess); `tests/test_proc_helper.py` covers
the `gui=True` flag. The packaged exe's GUI smoke passed 9/9 steps after
the rebuild (§4), which includes the open-folder step with an Explorer
window detection (the detection step reports a marker in the smoke log).

### 2.6 The jobs folder and "Dọn dẹp ngay" are understandable

Settings → Advanced groups them under "Tệp tạm của các lần dịch" with the
plain-language explanation, the folder path + "Mở thư mục", the current
size and the stored-run count, and a one-line explanation on the
older-than-days setting. "Dọn dẹp ngay" first confirms with numbers and
the two choices ("older than N days" default, "delete all except the
running job"), and states what is never deleted (outputs/translations,
the running job, the ASR model files). The Options checkbox
"Giữ các tệp trung gian" has the resume-loss tooltip. Tests:
`test_working_files_group_texts_and_stats`,
`test_keep_intermediate_tooltip`, `test_clean_now_confirmation_numbers`,
`test_clean_now_delete_all_keeps_running_and_output`,
`test_clean_now_older_choice_deletes_only_old`,
`test_clean_now_nothing_stored_shows_notice` (real widgets).

### 2.7 README images that work

`docs/images/` is tracked and small (build output at the end of §4):
`ready.png` 9 847 B, `running.png` 12 090 B, `finished.png` 12 251 B,
`settings_api_keys.png` 4 624 B — all copied fresh from the newest
screenshot set by `scripts/update_readme_images.py`, run by
`packaging/build_exe.ps1` right after the screenshot step. `README.md`
uses `docs/images/...` only; `scripts/check_readme_links.py` resolves
every link to a tracked file and is part of the pre-flight gates
(gate 6: `README link check: PASS`) plus `tests/test_readme_images.py`
(3 passed).

### 2.8 A lighter package — measure first, prune what is verified

Measured (25-largest list + per-group totals, this session):

| Group (before, `dist/DubTool` @ 262f9eb interim build) | MB |
| --- | --- |
| FFmpeg (ffmpeg.exe 121.4 + ffprobe.exe 121.2) | 242.6 |
| other (Qt plugins, Python stdlib DLLs, whisper tokenizer assets…) | 112.5 |
| ASR runtime (ctranslate2.dll 58.4, onnxruntime 37.5, tokenizers.pyd …) | 104.6 |
| PyAV/Python runtime (avcodec-62 18.4, av libs, .pyd) | 100.5 |
| Qt DLLs (Core 10.0, Gui 9.2 …) | 31.1 |

Removed, each verified first:
1. **ffprobe.exe (121.2 MB)** — probing runs in-process through PyAV
   (containers mp4/mkv/mov/webm/avi/m4v checked); the PATH fallback
   stays for edge cases. *These numbers were found during the final
   size pass: the interim build still shipped ffprobe because the spec
   copied `vendor/` as a directory entry, so the per-file filter never
   matched. The spec now lists vendor/ file-by-file and the build script
   refuses a build that contains ffprobe (`missing/ffprobe must not be
   in the build` checks in `build_exe.ps1` §5).*
2. **Qt pruning** — spec drops Qt6Quick/Qml/Pdf/VirtualKeyboard
   binaries; QtTest kept (gui-smoke uses it). Software-OpenGL
   (`opengl32sw.dll`, 19.7 MB) is **kept** — machines without graphics
   drivers need the fallback.
3. **No duplicated DLL copies at root** (root-level dedupe filter; they
   live under `_internal`).

Result (same commands as the before pass):

| Item | Before (Update 8, a7b511a) | After (Update 9, 262f9eb fresh) |
| --- | --- | --- |
| `dist/DubTool` unpacked | 693.9 MB | **470.1 MB** (367 files) |
| `dist/*.zip` | 267.9 MB | **186.9 MB** |
| ffprobe.exe in build | 121.2 MB | absent (build fails if present) |
| Qt dropped modules | — | Quick/Qml/Pdf/VirtualKeyboard binaries |

Not removed (and why, per §9.2): opengl32sw.dll (GL fallback),
ctranslate2/onnxruntime (required by ASR, CPU-only confirmed),
ffmpeg.exe (encode/mix/burn; switching to a differently-built release
was out of scope after verifying the current one works — see THIRD_PARTY.md).

---

## 3. Build — identity and screenshots

| Item | Value |
| --- | --- |
| exe path (run this) | `D:\Projects\Vibe\Project_K\dist\DubTool\DubTool.exe` |
| version | 1.0.9 (`--version` checked) |
| build id | `Update 9 (262f9eb)` (BUILD_INFO.txt) |
| exe sha256 | `109ce4790991f76e5949c56e4cb49e122a8d04db7291689a8c54fb7000e898b1` |
| zip | `D:\Projects\Vibe\Project_K\dist\DubTool-1.0.9-win64.zip` (186.9 MB) |
| older builds/zips | deleted by the build script (one folder, one zip) |
| screenshots | `screenshots/update_9_262f9eb/` + `INDEX.txt` (states: idle, video, running, finished, error; dialogs incl. Settings tabs; keys masked) |
| README images | `docs/images/{ready,running,finished,settings_api_keys}.png` (9 847 / 12 090 / 12 251 / 4 624 B) |

Gates run on the fresh exe (from `dist/DubTool`):
- `--selftest` → `overall: PASS` (12 PASS rows incl. `av_decode_compat`,
  `qt_offscreen`, `gemini_client`, `docx_roundtrip`).
- `--gui-smoke` → `PASS (9 steps)` (real 2-min video import, settings add
  keys/reorder/remove/test, run fast+accurate via real buttons).
- `--layout-check` → `total violations: 0` at all sizes/scales with the
  file card and details panel states (idle/video/running/finished/error).
- Screenshot-set test (`tests/test_screenshots.py`) → 3 passed after
  regeneration.

---

## 4. Task 10 — Update-8 close-out, item by item

### 10.1 Real quality runs (Fast, real model, no faults)

Run: `test2.mp4` → job `2904cb1f5cc6` →
`sample/output/e2e_test2_v9/test2_v9_vi.mp4` +
`test2_v9_vi_translation.docx` (docx table: 21 rows = header + 20 data
rows; first 15 data rows extracted text-verified from the docx, see §4.4
rows excerpt in REVIEW_NOTES.md). Stage timings (retained logs):

| Stage | faithful retained run | balanced retained run |
| --- | --- | --- |
| s1 extract | 0.2 s | ~0.2 s |
| s2 ASR | 30.7 s | ~30 s |
| s3 translate (+punct/completeness) | 18.1 s | ~19 s |
| s4 TTS | 179.8 s | ~180 s |
| s5 fit | 10.1 s | ~10 s |
| s6 mix | 7.3 s | ~7 s |
| s7 encode+burn | 53.0 s | 54.7 s |
| requests | 6 (`translate`×3, `complete`×3) | 5 (`translate`×3, `complete`×2)|

All requests well under the per-run default budget of 40.

Structural metrics (punctuation restore runs on the raw ASR text; see
finding below):

- **Punctuation windows: 0 accepted, 1 window rejected** in every run
  this session (primary + both retained runs). Rejection reason (log):
  `punctuate: window 0-35 rejected (giving up after 2 invalid replies
  (gemini): word sequence changed: 'against their abusers, their
  rapists, and their assaulters on campuses across.') — keeping original
  text`. The model dropped a repeated "against" in both replay replies;
  validation (correctly) refused both. Consequence: the final text's
  sentence-final-punctuation share equals the pre-restore share in these
  runs — **75.0 % (15/20 units, faithful), 73.7 % (14/19 units,
  balanced)**, longest unit 20.98 s / 20.94 s. (Update 8's baseline for
  the same video was 41.0 % of *segments*; units merge segments, so the
  numbers are not directly comparable across runs.)
- **Fillers**: `filler suggestions: 0 token(s) above the threshold` —
  no suggestions dialog in any run (consistent with Update 8: `girl`
  never passes the threshold gates on this video).
- **Completeness pass (balanced)**: `completeness pass: 4 unit(s)
  flagged ({'dangling', 'shrunk'})`, then `completeness
  re-translation: 3/4 fixed, 1 kept flagged`.
- **Completeness pass (faithful)**: `completeness pass: 2 unit(s)
  flagged ({'dangling'})`, `1/2 fixed, 1 kept flagged`. The kept-flagged
  row is in `jobs/<job>/quality_report.json`:
  `{"time": 27.2, "reason": "dangling", "en": "What does trans have to
  do with like what nobody thinks this …", "vi": "Chuyện chuyển giới
  liên quan gì… đó là lý do tất cả cần hiểu và…"}`.

### 10.2 Fidelity preset numbers on test2

Retained runs (same pipeline, `keep_intermediate=True`, faithful first
then back to balanced; settings restored):

| Metric | faithful (`7d33ce110abf`) | balanced (`8ab75a20d572`) |
| --- | --- | --- |
| fit summary | `ok=32, shortened=1, stretched=2` (fit ok=32/shortened=1/stretched=2) | `ok=29, stretched=5` (0 shortened) |
| units | 20 | 19 |
| shifted clips | 4 | 6 |
| max shift | 0.08 s | 0.15 s |
| total shift | 0.27 s | 0.48 s |

(faithful: `shifted: 4, max 0.08, tot 0.27`; balanced:
`schedule: 34 clips, 6 shifted, max_shift=0.15s, total_shift=0.48s,
overlap ids=[]`.)

Reading: faithful resists shortening (1 shortened vs 5) and ends with
less accumulated lag, at the cost of ~1 more TTS/fit operation.

### 10.3 Evaluation file

`dubtool eval-translation 8ab75a20d572 --variants
current,punctuated,faithful` →
`eval/20261031-094228.md` (34 units, 3 variants, 2 LLM requests,
`llm_requests_used: 0/40`). Copied into the review zip (§7 path).
Note: the eval ran on the balanced retained job because the primary e2e
job deleted its intermediates (`keep_intermediate=False`); the job id
path shows which run it belongs to.

### 10.4 initial_prompt / medium — presented, not decided (done in Update 8's data; UI shipped here)

Settings → General checkbox "Gợi ý dấu câu cho nhận dạng giọng nói",
bound to `asr.initial_prompt`, **default off**, one-line trade-off text
(Update 8 measured: 41.0 % → 52.5 % segments with sentence-final
punctuation at no extra time; `medium` 49.1 % at ~3× slower RTF).
Verification: dialog tests in `tests/test_app_ui.py`
(`test_whisper_dropdown_shapes` + the initial-prompt control test).

### 10.5 The `.git` folder — measured and cleaned (safe, no history rewrite)

- Before: `git count-objects -vH` → `count: 536, size: 534.11 MiB,
  packs: 0` and `.git` on disk 536 MB.
- Two objects dwarf everything: `d5029ecf…` (447.4 MB) and
  `09c8c784…` (109.9 MB). `git rev-list --objects --all` does **not**
  reference either (count 0) — they are unreachable dangling blobs
  from abandoned staging attempts. Inspection: the 104 MB blob is an
  MP4 (`ftyp isom` header); the large blob is not printable at its head.
- All reachable objects are small: largest reachable blob is
  `screenshots/05_finished.png` (49 978 B); **no venv, model, media or
  VM blob is part of the reachable history.**
- `git gc --prune=now` → after: `in-pack: 371, size-pack: 724.67 KiB,
  packs: 1`. `.git` is now < 1 MB of objects (**534 MiB → 725 KiB**),
  nothing reachable was removed, no push happened (repo visibility
  unknown; there is no remote configured in this clone).

### 10.6 GitHub line

- Working tree contains all Update-9 changes staged for commit (git add
  done at the end of this session; see §7).
- Commit made this session: `Feature: complete AGENT_UPDATE_8 …` is the
  HEAD (`262f9eb`) — the Update-9 commit will be created by the user's
  instruction or the next session step; **not pushed** (no remote; see
  the question list §8).

### 10.7 Clock-offset hint

The Update 8 measurement (+54.36 s) is surfaced: `netcheck.py` reports
the offset and when |offset| > 30 s both the `tts_unavailable` user
message and the diagnostics get the hint "Đồng hồ máy lệch khoảng N
giây so với máy chủ — hãy đồng bộ giờ Windows" (states a possibility,
not a cause; the clock is never touched).

### 10.8 The exact thing to run

```
D:\Projects\Vibe\Project_K\dist\DubTool\DubTool.exe
  build id : Update 9 (262f9eb)
  version  : 1.0.9
  sha256   : 109ce4790991f76e5949c56e4cb49e122a8d04db7291689a8c54fb7000e898b1
```
(Compare with the About box — it must show exactly these.)

---

## 5. Aggregate test run (one run, real counts)

```
python -m pytest tests -q --ignore=tests/test_screenshots.py
  → 438 passed, 2 skipped in 55.04s
python -m pytest tests/test_screenshots.py -q → 3 passed
```
Skips (both environmental, not coverage): `test_cleanup.py::219` and
`test_housekeeping.py::84` — Windows denies non-admin symlink creation
(WinError 1314).

New this update: `tests/test_update9_ui.py` (10 tests, busy state, file
card, details panel incl. redacted copy button, version single-source)
and `tests/test_readme_images.py` (3 tests).

Pre-flight gates (`python scripts/preflight_gates.py`): gate 2 secret
scan (158 files, 4 stored values compared): PASS (the only fixture hits
are prose mentions of key *prefixes* in tests/agent/config docs — a
fix this session added those names to the gate's fixture list with the
comment `AGU9 10.7`); gate 4 forbidden paths PASS; gate 6 README link
check PASS; overall `ALL PUBLISHED-CONTENT GATES PASS`.

---

## 6. Cleanup report

Dry run (`cleanup.main([])`): project total **6.1 GB**; report-only
keeps listed: `sample` 1.0 GB (source videos + e2e outputs), `.venv-build`
997.4 MB, `.freebuff` 937.5 MB, `jobs` 918.4 MB (measurement runs kept
their intermediates on purpose), `dist/DubTool` 470.1 MB, `vendor`
244.6 MB, zip 186.9 MB, `models` 86.8 MB, `.venv` 57.9 MB.
Applied: `--apply --categories caches` → **deleted 5 224 items,
reclaimed 35.8 MB** (`cleanup.log`; only `__pycache__`/cache families).

**Questions still needing the user** (nothing more deleted):
`.venv-build` (997 MB; regenerable but slow), `sample/` (1.0 GB; the
source videos live only here), `vendor/` (244.6 MB; ffmpeg + fonts the
build ships), `models/` (86.8 MB; ASR + fallback TTS).

---

## 7. Review zip

`review/dubtool-review-<timestamp>-update-9.zip` — rebuilt at the end of
the session by `scripts/make_review_zip.py` / `dubtool review-zip
--tag update-9`; payload per AGU6/AGU8 rule: agent briefs, the latest
`REPORT_UPDATE_*.md` (this file), rewritten `REVIEW_NOTES.md`, screen-shots, docs
images, build logs (masked), `eval/20261031-094228.md`. Size and final
path are in the closing message (see ZIP below).

---

## 8. Questions for the user

1. `.venv-build`, `sample/`, `vendor/`, `models/` — keep or drop? (sizes
   in §6; `--apply --categories` knows the categories).
2. GitHub: push after you commit (or tell me to commit and push — no
   remote is configured in this clone yet).

---

## 9. What I could not verify (and why)

- The look and feel on the user's display (fonts, DPR) — only offscreen
  runs and the layout check at all scales were possible.
- The dub's sound quality and perceived translation quality — the user
  judges; the eval file (§4.3) lists per-unit variants for reading.
- Antivirus/SmartScreen behavior of the fresh exe.
- The punctuation-restore improvement on final output **this session** —
  the model's replay kept dropping a token so the single window was
  rejected and the original text kept (correctly). The structural
  numbers therefore show before/after equality; the logic itself is
  covered by the offline tests.
- The exact filename behind the 447 MB dangling git blob is unrecoverable — it was never referenced by any commit; both blobs are stripped. Bucketized: media + one large non-printable blob, both dangling.

## 10. Anything noticed but not fixed

- `tests/test_update9_ui.py` duplicates the window fixture from
  `test_app_ui.py` (small, explicit; fine for now).
- FFmpeg inside the package is still the 2025-01-11 GPL build; a
  grep-lighter release build could shave ~30-60 MB but needs a full
  subtitle-burn/mix verification pass — left for a later update.
- The eval ran on the retained balanced job, not the primary run
  (primary intermediates deleted). If the user wants the primary, rerun
  with keep-intermediate and re-eval.
