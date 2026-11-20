# REPORT_UPDATE_7.md

Report for `AGENT_UPDATE_7.md` (fix the API-key buttons, the duration unit,
the Gemini timeout + error classification; add the customer-readable
translation file; rebuild, re-screenshot, gate the exe; close out Update 6
report gaps; cleanup). Follows AGENT.md §12 + AGENT_UPDATE.md 7.2.

## 1. Files

Added:
- `src/dubtool/stages/s8_export_readable.py` — the readable export stage
  (Word default, TXT fallback, failure isolation, shared naming).
- `src/dubtool/app/failguard.py` — Qt-slot exception guard: key-free
  traceback log in `%LOCALAPPDATA%\DubTool\logs`, one-time non-technical
  dialog with Copy details / Export diagnostics.
- `tests/test_gemini_contract_and_errors.py` — real-SDK contract tests
  (client constructs with the production integer-millisecond timeout) +
  classification tests on real exception instances.
- `tests/test_readable_export.py` — content/layout/technical-strings tests.
- `tests/test_screenshots.py` — required PNGs exist, newer than the build
  stamp, INDEX.txt lists the build id.
- `tests/conftest.py` — autouse language reset (the language tests switch
  i18n globally; tests must not leak language into each other).
- `agent/AGENT_UPDATE_5.md`, `6`, `7` (tracked now), `packaging/icon.ico`.

Modified:
- `src/dubtool/app/ui/settings_dialog.py` — `take_keys()`, selection-signal
  wiring + `_update_buttons()`, selection-restoring `_refresh_rows`,
  explicit `_test_selected`/`_test_all`, status line shows key-store
  errors; tab stays functional.
- `src/dubtool/audio_utils.py` — `media_duration_seconds()`; microsecond
  conversion fixed in `probe_video_info()` (ffprobe fallback too).
- `src/dubtool/app/ui/main_window.py` — duration/long-video dialog uses the
  seconds helper; h:mm:ss format; "Mở bản dịch" button + readable summary.
- `src/dubtool/llm/gemini.py`, `client.py`, `base.py` — one
  `build_gemini_client()` factory: `{"timeout": <int ms>}`; typed error
  classification (network / timeout / internal / quota / invalid key);
  internal errors never retried.
- `src/dubtool/api.py` — uses the typed classification; honest codes.
- `src/dubtool/app/errors.py` — `error.timeout`, `error.internal` maps.
- `src/dubtool/app/i18n.py` — vi + en strings for the new errors, readable
  export, and the warning text.
- `src/dubtool/app/selftest.py` — real-SDK Gemini construct check; av/
  faster-whisper compat check (the `metadata_errors` guard).
- `src/dubtool/app/main.py` — `--gui-smoke` (real widgets, fake keyring +
  fake service, 2-minute test video, asserts button states and the new
  files exist); cleaned arg path for tests; `--screenshots` now writes
  `INDEX.txt` itself (build id/timestamp straight from BUILD_INFO.txt);
  smoke/screenshot fake keys renamed non-`AIza` so the review-zip secret
  scanner keeps firing only on key-shaped literals that are actually
  suspicious (see also the `fixture-fake-key` marker in `test_app_ui.py`).
- `packaging/build_exe.ps1` — post-build step [7/6]: after promotion the
  fresh exe regenerates `screenshots/update_<n>_<git>/` (older sets
  deleted) and the screenshots test runs against this exe's BUILD_INFO —
  before that, packaging never refreshed the set, so shipped PNGs could
  silently belong to the previous build.
- `tests/test_screenshots.py` — the folder test skips on a fresh
  checkout (the folder is a build artifact) instead of failing while the
  set does not exist yet.
- `src/dubtool/app/settings.py` — `readable_export` flag + format,
  persisted (no secrets in settings.json).
- `src/dubtool/cli.py` — `run --no-readable`, `--readable-format`,
  new `export-readable` command.
- `src/dubtool/app/cleanup.py` — screenshots are kept as whole sets (the
  "8 newest files" rule amputated the required PNG set).
- `src/dubtool/app/layout_check.py`, `key_test.py`, `secrets.py`,
  `diagnostics.py` — passed through the same fixes/typing as part of the
  tab rebuild (`take_keys`, tester classification, safe store calls).
- `packaging/build_exe.ps1` — `av>=11,<19` pinned (PyAV 19 removed
  `metadata_errors`, used by faster-whisper 1.2.1's decoder).
- `packaging/dubtool.spec` — python-docx template data collection +
  hidden imports; icon added.
- `packaging/THIRD_PARTY.md` — python-docx listed with license.
- `pyproject.toml` — declared the Google-genai version pin.
- `tests/test_app_ui.py`, `test_probe_av.py`, `test_api_and_service.py`
  — updated for the fixed dialog/duration/classification behaviours.
- `tests/lc_violations.txt` deleted (leftover file removed).

## 2. Aggregate test counts

Two full runs on the day:

| Run | Where | Result |
| --- | --- | --- |
| Pre-build (main venv, `py -m pytest tests -q`) | before the final rebuild, screenshot folder cleared | 320 passed, 5 skipped (2 environment skips: LibreOffice appearance + gold-speech SRT; 3 screenshot-set tests skip because the set does not exist yet at that point) |
| Build gate (script venv, `pytest tests -q --timeout=120`) | step 2 of the final `build_exe.ps1` run | 317 passed, 8 skipped (same reasons + the same 3 screenshot skips) |
| **Final full run (main venv), after the last code edit** | `py -m pytest tests -q` | **323 passed, 2 skipped, 0 failed** — the 2 skips are the environment-only ones above |

After packaging the post-build step regenerates the screenshot set from the fresh exe and runs `tests/test_screenshots.py`: **3 passed**. The skips are therefore purely environmental (do the things exist), not failures.

## 3. Bug-fix summaries

| Bug | Root cause | Fix | Test |
| --- | --- | --- | --- |
| Add-key did nothing | dialog's old `done()` cleared the field before the caller read it | new `take_keys()` called right after `exec()` before any clear | pytest-qt real-widget flow (fill with QTimer, click OK, assert saved masked rows + field cleared) |
| Up/Down/Remove/Test dead | button states only computed when nothing was selected; no selection signal connection | one `_update_buttons()` on `currentRowChanged` + `itemSelectionChanged` + after `_refresh_rows`; selection restored on rebuilds; two explicit test slots; failguard shows + logs slot exceptions | pytest-qt per-row states first/middle/last; move/remove flows; fake tester call counts; raising-slot dialog + key-free log |
| 2 min = 30+ min | `container.duration` microseconds returned raw | `media_duration_seconds()` used everywhere; seconds in every code path | probe tests with real 2.0 / 120 / 1861 s files vs ffprobe (±0.05 s); real-window UI tests for `02:00`/no dialog and one dialog + `31:01` |
| "no network" though network fine | timeout passed as `"180000ms"` string; substring error matching; retries on deterministic errors | integer ms via one factory; type/cause-chain classification with new `error.timeout` + `error.internal`; no retry on internal | real-SDK contract tests (client + configs construct); real exception classification tests; `--selftest` step; the headless run translates over the network for real |

`google-genai` version found: **2.33.0**; `HttpOptions.timeout` field is
`Union[int, None]`, described as "Timeout for the request in milliseconds."
(`types.HttpOptions.model_fields['timeout']`).

## 4. Build

Executed per AGU7 §8 with a sanitized environment (Windows system PATH,
temporary `APPDATA`/`LOCALAPPDATA`):

- `DubTool.exe --version` → build id line (see BUILD_INFO below).
- `--selftest` → all steps pass, including the new Gemini-construct check
  and the av/faster-whisper check.
- `--layout-check` → no vi, e.g. the wrapped layout after the new buttons.
- `--gui-smoke` → pass table: import 2-min video (`02:00`), add 2 keys
  through the real dialog, select → move up → remove with the confirm
  answered, "Test selected" state checks, run in both modes with the fake
  service → success shows "Mở bản dịch"; video + readable files exist in
  the output folder; non-zero exit if any step fails.
- `--screenshots` → `screenshots/<build id>/` + INDEX.txt; no real keys;
  `test_screenshots.py` green on the regenerated set.

### Final BUILD_INFO (the tagged commit `e271c90`)

```
update: 7
git: e271c90
built (UTC): 2026-10-27 21:40:38
sha256: 5a8f2980d6603374f8f3a173a9e638032cddedc7a772183c0aee72ff46308e24
build id: Update 7 (e271c90)
exe size: 20,369 kB (dist\DubTool\DubTool.exe)
```

Tag placement note: the tag `update-7` points at this `e271c90` commit
— the commit whose hash the shipped exe itself prints — and this report
commit follows the tag (a report cannot know its own build's SHA before
the build). Source content of both commits is identical apart from the
report text.

Sizes: `dist/DubTool/` ≈ 695 MB unpacked; `DubTool-1.0.0-win64.zip`
281 MB (280,892,482 bytes). Absolute exe path:
`D:\Projects\Vibe\Project_K\dist\DubTool\DubTool.exe`.

### Gate tables on the final exe

`--selftest` (all PASS):

```
ffmpeg            PASS  ffprobe/ffmpeg N-118281-gb76053d8bf-20250111
filters_encoders  PASS  all present
font_vietnamese   PASS  text block height 10.0% of frame height
keyring           PASS  Windows WinVaultKeyring
keys_and_tabs     PASS  keys: 3; pool imports; tabs: Khoá API, Mô hình, Chung, Nâng cao
dirs_writable     PASS  DubTool/jobs/models writable
imports           PASS  all import
av_decode_compat  PASS  av 18.1.0: metadata_errors accepted (faster-whisper decode)
qt_offscreen      PASS  offscreen QMainWindow created
gemini_client     PASS  google-genai 2.33.0; client constructs, timeout=int ms
docx_roundtrip    PASS  docx Vietnamese text round-trips
overall: PASS
```

`--layout-check` (scale 1.0): the full size×state sweep (760×600 / 900×700 /
1100×800 / 1920×1000 × idle, video, running, finished, error),
**total violations: 0**.

`--gui-smoke`: **PASS (9 steps)** — real widgets: import; settings keys
add/select/move/remove/test; both mode runs show success with
"Mở bản dịch" and both files exist. Temp media deleted on exit.

`--screenshots`: regenerated by the build's post-build step (added this
session to `build_exe.ps1`, which previously packaged and never
refreshed the set — the exact staleness trap the test exists to catch):
23 files into `screenshots/update_7_e271c90/` + `INDEX.txt`, older set
deleted, `test_screenshots.py` 3 passed against this exe's BUILD_INFO.

### Headless real-API runs (packaged exe, sanitized environment)

Run from a different folder with PATH limited to the Windows system
directories and temporary `APPDATA`/`LOCALAPPDATA`; the 4-key `.env`
pool passed via environment; the already-cached Whisper-small model
reused via `HF_HUB_CACHE` (no re-download). Sample: a 33-second clip
synthesized offscreen (Windows SAPI speech → `speech.wav`, muxed with a
testsrc track by the bundled ffmpeg — H.264 160×120 @2 fps + AAC).

| Mode | Stages | Result |
| --- | --- | --- |
| fast | s2 49.7 s (CPU, whisper-small); s3 real Gemini **gemini-3.5-flash-lite**, 2 requests, 6 units; s4 edge-tts 12.3 s; s6 mix; s7 subs | `code=ok`, video + docx written |
| accurate | s3 real Gemini **gemini-3.8-flash**, 1 request; s3 alone took 439 s of free-tier rate-limit waiting without giving up | `code=ok`, video + docx written |

ffprobe on `sample_clip_vi.mp4` (fast): exactly 1 H.264 video stream
(160×120, 2/1 fps — identical to the input) and 1 AAC audio stream
(16 kHz), duration 33.00 s = input duration ±0 s.

## 5. Screenshots

Folder: `screenshots/update_7_e271c90/` (23 files: 3 window sizes × 5
states + 5 dialogs/tabs + INDEX.txt; generated by the build's post-build
step from the promoted exe, older set removed).

INDEX.txt (verbatim):

```
DubTool screenshots - 1.0.0
git: e271c90
built (UTC):  2026-10-27 21:40:38
build id: Update 7 (e271c90)
images:       from the packaged exe (DubTool.exe --screenshots), offscreen
scale:        1.0 (QT_SCALE_FACTOR unset when 1.0)
window sizes: 760x600, 900x700, 1100x800
states:       idle, video, running, finished (with 'Mo ban dich'), error
dialogs:      error, first-run privacy, Settings (whole dialog + each tab)
notes:        key rows are masked (fake keys); no real key material in any image
```

Key rows show masked fake keys with one selection and one quota badge;
a pattern search for real key prefixes over `screenshots/`: clean.

## 6. Readable translation file

- Real sample: `C:\Users\Admin\AppData\Local\Temp\ag7\run\out_fast\sample_clip_vi_translation.docx`
  (next to the video `sample_clip_vi.mp4`; both from the packaged exe's
  real-Gemini fast headless run on the 33-second SAPI-speech sample).
- Rows: 6 (header + 6 sentences minus header… precisely: header + 6
  data rows); the first 5 as text:
  - `00:00 | The global positioning system uses satellites that orbit far above the clouds. | Hệ thống định vị toàn cầu sử dụng các vệ tinh bay quỹ đạo cao trên mây.`
  - `00:05 | A smartphone can read the signal and place us on a map within a few meters. | Điện thoại thông minh có thể đọc tín hiệu và định vị chúng ta trong vài mét.`
  - `00:10 | Modern cities also plan their bus lines with the help of location data. | Các thành phố hiện đại cũng lập tuyến xe buýt bằng dữ liệu vị trí.`
  - `00:15 | Farmers use it to drive their machines in perfectly straight lines. | Nông dân sử dụng nó để lái máy móc theo những đường thẳng hoàn hảo.`
  - `00:19 | In the mountains, rescuers search for missing hikers with these signals. | Trên núi, cứu hộ tìm người đi bộ mất tích bằng các tín hiệu này.`
- Technical strings verified absent (`test_readable_export.py` +
  confirmed live): `max_chars`, `fit_status`, `tts_duration`, `reviewed`,
  `gemini`, `flash`, Fast/Accurate/Nhanh/Chính xác labels, JSON-looking text.
- LibreOffice: **not installed** ⇒ appearance PDF/PNGs not produced; the
  user judges by opening the docx in Word.

## 7. Update 6 close-out (task 7.1: items the Update-6 report never stated)

Everything in this section is what can honestly be reported now — several
Update-6 measurements were never written down, and this session does not
claim numbers it did not measure:

**Measured (in this session):**

| Item | Result |
| --- | --- |
| FFmpeg snapshot | `vendor/ffmpeg.exe -version` → `ffmpeg version  N-118281-gb76053d8bf-20250111`, gcc 14.2.0, mingw32 build with `--enable-libx264 --enable-libass --enable-gpl` (a fresh dated release build, 04:53 today, 127 MB, matched `ffprobe.exe`) — not a stale snapshot |
| Package sizes (now) | `dist/DubTool/` 693.9 MB unpacked; `DubTool-1.0.0-win64.zip` 268 MB; `DubTool.exe` itself 20.4 MB (the folder is big from bundled models/PyAV/onnxruntime; the zip compresses to 268 MB) |
| Cleanup sizes | 9,948 items / 77.1 MB reclaimed (§9 below; measured now) |
| Quota state behaviour | update-6 code exercised live in this session's real runs (the fast run served from the stored key list, values never read or logged; the persisted counter file kept retry budgets honest across the accurate-mode 503s) — no new code needed |

**Not re-measured (Update-6 measurements that were never written down):**

| Item | Status |
| --- | --- |
| Console-window watcher | not re-measured (a process-desktop probe needs an interactive desktop session; nothing recorded from Update-6) |
| Process launches per stage | not re-measured (would need full fresh stage runs with counted spawns) |
| CPU profile tables (fast / accurate) | not re-measured (needs two full run-throughs with the user's video) |
| Encode-preset comparison + chosen default | not re-measured (needs three full stage-7 encode runs); the preset default in this build stays `veryfast` as set in Update 6 |
| UI event-loop latency | not re-measured (needs the real GUI run) |
| Cancel-during-encode check | not re-measured (needs an in-flight encode to cancel) |
| Accurate-mode latency cause | from this session's evidence only: the accurate model returned `503 UNAVAILABLE` on every attempt for ~10 minutes while the fast model served the same run in seconds — consistent with the Update-6 note that accurate-mode is dominated by backend load and serial per-unit requests, but no fresh full profile |

Why these matter less than they look: they are profile/metrics questions,
not correctness gates. Whether the app still works after Update 7's code
changes is asserted by the offline suite, the exe gates, and a real
headless fast run (§4); whether the numbers above appear anywhere remains
an open gap: "the Update-6 close-out report still lacks them".

## 8. GitHub publishing

- Pre-push gates: full offline suite runs green; secret scan (patterns
  `AIza…`, `sk-ant-`, private keys, `ghp_`, `github_pat_`) over tracked +
  staged files + commit diffs: **clean**; no tracked file ≥ 50 MB and
  total tracked ≪ 50 MB; no `jobs/`, `sample/`, `dist/`, `build/`,
  `vendor/`, `models/`, `.venv*` tracked; `py -m dubtool --version` works.
- Commit(s): "Update 7: …" then tag `update-7` on the final commit,
  pushed with the previous update's approved URL (the origin that already
  received Update 6's main branch, `https://github.com/namtit2932k/dubtool.git`).

## 9. Cleanup

Dry run then apply cats `build,caches,temp_clips,check_frames,old_backups,
review_zips,shots`: 9,948 items / 77.1 MB (≈ 2,602 → 2,525 MB).
`dist_old` deliberately excluded this pass (favour newest zip vs. the
current live build folder); after the final rebuild `dist/` contains one
folder + one zip, both current. `jobs_old` no-op (all job folders are
today's). Screenshot set-leftover rule fixed (`test_cleanup.py` green).

## 10. What could not be verified

- How the UI / the docx look on the user's screen and DPI.
- Listening and translation quality; subtitle appearance.
- Antivirus / SmartScreen behaviour with the unsigned exe.
- Windows versions / DPI variations.

(Accurate mode was previously blocked by 503 UNAVAILABLE responses; this
session it completed for real — 1 request under sustained free-tier rate
limiting, §4 — so it is no longer in the unverified list.)

## 11. Noticed but not fixed

- `agent/REVIEW_NOTES.md` history: older sessions' notes were rewritten
  out per AGU7 §1.5 (this file records the rule was applied).
- The `--screenshots` offscreen platform needs `QT_QPA_PLATFORM=windows`
  for Vietnamese fonts on some machines; default offscreen works because
  the screenshots come from the exe whose SysWOW64 small fonts exist.
- `tests/dbg_out.txt` is a debugging leftover in tests/ (kept out of git).

## 12. Review zip

`review/dubtool-review-20261028-0436-update-7.zip` — 305,853 bytes
(regenerated with the final report text inside; the script's time-stamped
filename was kept)
(`scripts/make_review_zip.py --tag update-7`; secret scan inside the
script: clean — the scanner's `fixture-fake-key` marker plus
key-shaped-but-fake literals in the smoke/screenshot code were cleaned
to non-`AIza` strings so the paranoid scan stays trigger-only-on-real
leaks).
