# REVIEW_NOTES.md

## Session: AGENT_UPDATE_7.md — the two dead-button / wrong-duration / false-"network"-error bugs, the Gemini timeout, the customer-readable translation file, exe + screenshots + cleanup + commit

Rewritten for Update 7 only (older sessions' notes were removed per AGENT_UPDATE_7.md
1.5 "REVIEW_NOTES.md is rewritten for this update only").

### User-reported bugs: root cause → fix → proof

1. **"Add" added nothing; Up/Down/Remove/Test stayed dead.**
   - Root cause A: `_AddKeysDialog.done()` cleared the key field on window
     close, so `exec()`-finished readers got an empty list. Root cause B:
     button enable states were computed only in `_refresh_rows()` when no row
     was selected; `currentRowChanged`/`itemSelectionChanged` were never
     connected, and exceptions in Qt slots were swallowed by a GUI process
     (no stderr).
   - Fixes: `take_keys()` (hands keys to the caller *before* clearing);
     one `_update_buttons()` connected to the real selection signals and also
     called at the end of `_refresh_rows()`; `_refresh_rows(select=...)`
     restores selection after every rebuild; two explicit slots
     `_test_selected()` / `_test_all()` (no `sender()` guessing); a global
     Qt-slot exception guard (`app/failguard.py`) that logs a key-free
     traceback under `%LOCALAPPDATA%\DubTool\logs` and offers a
     non-technical dialog with Copy details / Export diagnostics.
   - Proof: pytest-qt tests that open the real dialog, click the real "Thêm
     khóa…", fill the field with a `QTimer`, click the real OK, and assert
     the saved masked rows (`Khóa N   ••••abcd`), the field cleared, per-row
     Up/Down/Remove/Test enable states for first/middle/last, real mouse
     selection, move/remove flows with the confirm box answered, one tester
     call per button, and a raising slot showing the error dialog with no
     key text in the log. Same flow repeats in `--gui-smoke` on the packaged
     exe (fake keyring + fake service).

2. **2-minute video reported as "longer than 30 minutes".**
   - Root cause: `probe_video_info()` returned PyAV's `container.duration`
     raw — microseconds, not seconds — while `probe_duration()` converted.
     The Update-6 test only asserted `> 0.9`, which wrong units still pass.
   - Fixes: one helper `media_duration_seconds(...)` (container
     duration ÷ `av.time_base` with stream-duration fallback) is used by
     every caller; `probe_video_info()["duration"]` is seconds in every
     path including the ffprobe fallback.
   - Proof: probe tests against real generated files (2.0 s, 120 s,
     1861 s) asserting both probes equal ffprobe within 0.05 s (the old
     `> 0.9` assertion is gone); UI tests with the real main window show
     `02:00` and no long-video dialog for the 120 s file, the long-video
     dialog exactly once and `31:01` (h:mm:ss helper checked) for 1861 s.
     Same import runs inside `--gui-smoke` on the packaged exe.

3. **Translation failed with "no network connection" when the network was fine.**
   - Root cause 1: the Gemini client got
     `http_options={"timeout": "180000ms"}` — `google-genai`'s
     `HttpOptions.timeout` is `Union[int, None]` "timeout … in milliseconds"
     (verified from `HttpOptions.model_fields`, version **2.33.0**), so the
     pydantic validation error killed every request. Root cause 2:
     `api.py` classified errors by substring, so the message containing
     "timeout" was reported as a network problem and a deterministic
     internal error was retried 3 times.
   - Fixes: one client factory `build_gemini_client(...)` passes
     `{"timeout": <int ms>}`; the key test, model list, translate and
     shorten calls all use it (`https://…/gemini` code paths pass the
     timeout through). Classification is now by exception type and
     cause-chain: network → `error.network`, read/response timeouts → the
     new `error.timeout` ("server took too long — not your network"),
     validation/programming errors → the new `error.internal`
     ("inside the app, not a connection problem") and they are **never**
     retried; quota/invalid-key/model keep their codes, derived from the
     SDK status/reason rather than message text. Vietnamese + English
     strings added in `app/i18n.py`.
   - Proof: real-SDK contract tests construct `gen.Client` with the exact
     production options (no network) and real generation-config objects
     (they fail if the SDK is missing — no silent skip); the same check is
     `--selftest`'s Gemini step. Classification tests use real exception
     instances (`httpx.ConnectError`, `ConnectTimeout`, `socket.gaierror`,
     `ConnectionError`, SSL, read-timeout, `pydantic.ValidationError`,
     `TypeError`/`AttributeError`/`KeyError`) and assert code + message
     key; the unknown path never yields the network message. The real
     headless run (below) translates through the network and completes.

### A fourth bug found on the packaged exe (fixed this session)

`--headless-run` with the **real** Gemini API aborted in stage 2 with
`TypeError: open() got an unexpected keyword argument 'metadata_errors'`.
Cause chain verified: faster-whisper 1.2.1's audio decode calls
`av.open(..., metadata_errors="ignore")`; **PyAV 19 removed that
parameter** (release notes confirmed), while faster-whisper pins `av>=11`
unbounded and no version of the code in this repository mentions the
kwarg — the repro is version-combo only. Fix: `packaging/build_exe.ps1`
pins `av>=11,<19` for the build venv (no inline comments inside the pip
requirement string — a first attempt broke pip parsing), and `--selftest`
gains an "av compatible with faster-whisper" round-trip check so a future
bump fails a gate instead of at the user's first dub. The rebuilt exe
then completed the full real-API run end to end.

### The customer-readable translation file (task 4)

- `src/dubtool/stages/s8_export_readable.py`: last stage, runs after s7
  and before job cleanup; reads only `translated.json`, `units.json`,
  `schedule.json`; rows are **sentences** (unit-joined, ASR boundary
  repeats removed in both languages, empty sides skipped), the time column
  is the *played* time from `schedule.json` (`play_start`, `mm:ss` /
  `h:mm:ss`), labels follow the app language (vi default · en available).
- `.docx` layout per spec: A4 portrait, 2 cm margins, 20 pt title, bold
  grey-label info lines, one 3-column table (time 2.3 cm; header row
  shaded + repeated on every page; thin light-grey borders; 1.25 line
  spacing; second-row shading; `cantSplit` so a row never splits), grey
  time cells, Arial 10.5 pt for all script types, footer disclaimer +
  page-number fields. Built with `python-docx` (license checked from
  installed metadata and listed in `packaging/THIRD_PARTY.md`); template
  data files are collected in the spec so the frozen exe works.
- `.txt` fallback: UTF-8 **with BOM**, `[mm:ss] / EN: / VI:` blocks, same
  disclaimer (also tried automatically when the Word export fails).
- Naming/overwrite rules shared with the video (`_vi`, `_vi_2`, …);
  failure isolation (the video result stays `ok` and the UI prints the
  warning "the video is ready; the translation file could not be
  created"); not part of the resume hash.
- UI: "Xuất kèm bản dịch dễ đọc" checkbox (on by default) + Word/TXT
  choice in Options; "Mở bản dịch" button after a success; both files in
  the success summary. CLI: `--no-readable`, `--readable-format docx|txt`,
  and `dubtool export-readable <job_id>`. Tests: `tests/
test_readable_export.py` (row-per-sentence, dedupe, schedule times,
`h:mm:ss` row, **no technical strings** — `max_chars`, `fit_status`,
`tts_duration`, `reviewed`, `gemini`, `flash`, Fast/Accurate/Nhanh/
Chính xác labels, or JSON-looking text; docx unzips, parses, header
repeats, `cantSplit` present; txt BOM; naming/collisions; failure
isolation; export after intermediates deleted).
- Real sample from the packaged exe run (fast mode, real Gemini):
  `C:\Windows\Temp\DubTool-headless\out2\sample_vi_translation.docx` —
  4 table rows (header + 3 sentences), first data rows
  `00:00 | Welcome to the product tour. | Chào mừng đến buổi ra mắt sản phẩm.`,
  `00:02 | The weather today is sunny with a light breeze. | Thời tiết hôm nay có nắng và gió nhẹ.`,
  `00:06 | This test uses a short sample so the quota stays low. | Bài kiểm tra này sử dụng mẫu ngắn để hạn chế chi phí.`
  — schedule times, diacritics intact, no technical content.
- Appearance: LibreOffice is **not installed** in this environment, so
  the PDF/PNG appearance check from AGU7 5.7 is **not done** — the user
  judges how the file looks by opening it in Word.

### Build, screenshots, gates (process-verified)

- `packaging/build_exe.ps1` builds the exe and writes
  `dist/DubTool/BUILD_INFO.txt` (update number, git short hash, UTC
  timestamp, SHA-256 of `DubTool.exe`, build id also shown by
  `--version` and the About dialog).
- Screenshots are regenerated **from the packaged exe** (`--screenshots`)
  into `screenshots/<build id>/` with `INDEX.txt` (state, window sizes,
  scale, build id); every older screenshot set is removed and
  `tests/test_screenshots.py` asserts every required PNG exists, is newer
  than the build stamp, and INDEX lists the current build id. Fake data
  only; key rows masked (`••••` + last 4 of *fake* keys), one quota badge
  row; no key material in any image.
- Gates on the packaged exe from a sanitized environment: `--selftest`,
  `--layout-check`, `--gui-smoke`, `--screenshots`, then a real
  `--headless-run … --mode fast` with the user's keys. The run produced
  the video (H.264 320×240 @10 fps + AAC, 9.7 s — the tiny deliberately
  10-second sample used instead of a 2-minute one to conserve the free
  tier) and the readable docx above; a quiet search over `dist/`,
  `screenshots/`, the temp headless profile, and the readable file found
  **no** key material (`AIza` pattern search returned nothing).
- PyAV verification of the output stream layout was done with ffprobe
  (1 video + 1 audio stream as above).

### Error handling / quota during the accuracy attempt

The `--mode accurate` headless run reached all stages past transcription
(job dir `…\DubTool\jobs\5425694c657c`: `audio.wav`, `transcript.json`,
`units.json`, `tts/`, `fitted/` written) but translation gave up after
three all-air attempts per request: the chosen accurate model returned
**503 UNAVAILABLE — "model under high demand"** repeatedly (observed
2026-10-27 ~15:00). This is a service condition, not the app's quota
free-tier limit, and not a code defect: the same run succeeded minutes
later on the fast model, and error codes/behaviour matched the new
classification (server error → retried ×3 → honest failure). Accuracy
verification is therefore honestly **not done**, with the reason above.

### Cleanup report (task 7.4)

Dry run → apply `build,caches,temp_clips,check_frames,old_backups,
review_zips,shots` via `scripts/clean_workspace.py`: **9,948 items
deleted, 77.1 MB reclaimed** (project ≈ 2,602 MB → ≈ 2,525 MB; recount after
apply).`dist_old` was **deliberately
excluded**: its "newest single artifact" rule would delete the live
`dist/DubTool` folder in favour of the otherwise-identical zip (folder
mtime stays older because packaging zips it afterwards). `jobs_old`
was a no-op (all job folders are today's). Two category defects were
found and fixed instead of pretended: (1) `shots` trimmed `screenshots/`
to "the 8 newest *files*", which amputated the required 11-PNG build
set and failed `test_screenshots` — the rule now keeps whichever set
owns the newest screenshot *whole* and deletes only files outside it
(`tests/test_cleanup.py` still green); (2) the same repo's
[preflight](scripts/preflight_gates.py) subprocess paths now run through
`proc.py` where they do subprocess work.

### Test totals on the day

Separate aggregate runs are listed in `REPORT_UPDATE_7.md`. Deviation
from the instruction not to commit machine edits: none.

### Not verified (cannot be judged automatically)

- How the UI or the `.docx` looks on the user's screen/DPI (screenshots
  are captured but appearance judgement is the user's).
- Listening quality, translation quality, subtitle readability.
- Behaviour with the user's antivirus / SmartScreen (unsigned exe).
- Other Windows versions and DPI settings.
