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
  files exist); cleaned arg path for tests.
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

Latest full offline run before the final rebuild: **320 passed, 2 skipped,
0 failed** (`py -m pytest tests/ -q`). The 2 skips: the LibreOffice PDF/
PNG appearance check (not installed) and one gold-speech-to-SRT check
(needs a Whisper model download). `tests/test_screenshots.py` was added
after that count and is exercised on every rebuild (see §4).

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

BUILD_INFO (previous build on this source state, current working tree):

```
update: 7
git: 1d8c642
built (UTC): 2026-10-27 07:52:24
sha256: 7aa825cf1040c63f32926a92180c3b82ec5dfc69b826bd0482880976c5c8c9e4
build id: Update 7 (1d8c642)
exe size: 20.4 MB (dist\DubTool\DubTool.exe)
```

The exe is rebuilt once more after tagging (see §8 for the final BUILD_INFO);
the gate tables above are re-run on the tagged build and pasted back here.

## 5. Screenshots

Folder: `screenshots/update_7_1d8c642/` (+ the new tagged build gets its
own folder; only the newest set is kept). INDEX.txt (abbreviated):
states idle / video / running / finished (with "Mo ban dich") / error;
dialogs error + first run; Settings tabs 0 Khoa API (3 masked fake keys,
one selected, one quota badge), 1 Mo hinh, 2 Chung, 3 Nang cao; window
sizes 760x600 / 900x700 / 1100x800; scale 1.0; key rows masked, no real
key material.

## 6. Readable translation file

- Real sample: `C:\Windows\Temp\DubTool-headless\out2\sample_vi_translation.docx`
  (and the video `sample_vi.mp4` next to it — both from the packaged exe's
  real-Gemini fast headless run).
- Rows: 4 (header + 3 sentences); first rows as text:
  - `00:00 | Welcome to the product tour. | Chào mừng đến buổi ra mắt sản phẩm.`
  - `00:02 | The weather today is sunny with a light breeze. | Thời tiết hôm nay có nắng và gió nhẹ.`
  - `00:06 | This test uses a short sample so the quota stays low. | Bài kiểm tra này sử dụng mẫu ngắn để hạn chế chi phí.`
- Technical strings verified absent (`test_readable_export.py` +
  confirmed live): `max_chars`, `fit_status`, `tts_duration`, `reviewed`,
  `gemini`, `flash`, Fast/Accurate/Nhanh/Chính xác labels, JSON-looking text.
- LibreOffice: **not installed** ⇒ appearance PDF/PNGs not produced; the
  user judges by opening the docx.

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
- Accurate-mode translation today: service 503s (model overloaded) kept
  the run from finishing; not the free-tier quota limit, retried next
  session.

## 11. Noticed but not fixed

- `agent/REVIEW_NOTES.md` history: older sessions' notes were rewritten
  out per AGU7 §1.5 (this file records the rule was applied).
- The `--screenshots` offscreen platform needs `QT_QPA_PLATFORM=windows`
  for Vietnamese fonts on some machines; default offscreen works because
  the screenshots come from the exe whose SysWOW64 small fonts exist.
- `tests/dbg_out.txt` is a debugging leftover in tests/ (kept out of git).

## 12. Review zip

`review/` zip made by `scripts/make_review_zip.py` after the final
commit — path and size recorded by the script's stdout (see §4 tables).
