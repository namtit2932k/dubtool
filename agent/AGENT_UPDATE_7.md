# AGENT_UPDATE_7.md — Fix the dead API-key buttons, the false "video longer than 30 minutes" warning, and the Gemini timeout failure; add the customer-readable translation file; always rebuild the exe and refresh the screenshots

Seventh update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_6.md` still apply (ground rules, data contracts, resumability, secret safety, the review zip rule in `AGENT_UPDATE.md` 7.2, the reporting format, and the GitHub publishing procedure in `AGENT_UPDATE_6.md` section 8). This file overrides them only where it says so.

The user tested the Update 6 build by running `py -m dubtool.app.main` from a terminal (no `.exe` was produced, and the screenshots they were given still show the old interface). They found three bugs:

1. **The API key management does not work.** No new key can be added, and the Up, Down, Remove, and "Test selected key" buttons cannot be clicked.
2. **A video of about 2 minutes is reported as longer than 30 minutes** when it is imported.
3. **The translation step fails with a "no network connection" message although the network works.** The copied details were:

```
gemini API error: 1 validation error for HttpOptions
timeout
  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='180000ms', input_type=str]
```

The user also asked for:

4. **An additional, simple, easy-to-read file with every exported video**, written for ordinary customers who are not technical. (This request was cut off in an earlier chat message; it is specified in task 4. The user's `translated_fast.json` shows the technical file this one replaces for customers: ids, `max_chars`, `fit_status`, `tts_duration`, text split into fragments.)
5. **A fresh `.exe` build after every update**, and
6. **Fresh screenshots after every update** (the current ones show the old interface).

---

## 0. Findings from reading the delivered Update 6 code (verified in the source)

1. **The key buttons are dead for two separate reasons** (`src/dubtool/app/ui/settings_dialog.py`):
   - `_AddKeysDialog.done()` clears the text field **before** the caller reads it. `_add_keys_manual()` calls `dialog.exec()`, which ends in `done()` (field cleared), and only then calls `dialog.keys()`, which therefore always returns an empty list. "Add" silently does nothing.
   - The enabled state of Remove, Up, Down, and "Test selected" is computed only inside `_refresh_rows()`, which runs right after the list is rebuilt, when no row is selected. `key_list.currentRowChanged` (or `itemSelectionChanged`) is **never connected**, so these buttons stay disabled forever. After a move or a remove, the selection is also lost because the list is rebuilt.
   - Exceptions raised inside Qt slots are swallowed (printed to a stderr that a windowed exe does not have), so such failures are invisible to the user.
2. **The 30-minute warning is a unit bug.** `audio_utils.probe_video_info()` (rewritten with PyAV in Update 6) returns `container.duration`, which PyAV reports in **microseconds**, without converting to seconds (line about `duration = container.duration`, then `f"{float(duration):.6f}"`). A 2-minute video becomes "120000000 seconds". `probe_duration()` converts correctly; `probe_video_info()` does not. The test (`tests/test_probe_av.py`) only asserts `float(info["duration"]) > 0.9`, which passes for the wrong unit.
3. **The Gemini timeout is passed in an invalid format** (`llm/gemini.py`, `client_for`): `http_options={"timeout": f"{int(self._timeout_s * 1000)}ms"}`. The SDK's `HttpOptions.timeout` is an integer number of milliseconds, so creating the client raises a pydantic validation error and **every Gemini request fails**. The tests use a fake SDK, so nothing caught it.
4. **Errors are classified by searching the message text.** `api.py` (around line 155) returns the code `network` whenever the message contains "network", "timed out", "timeout", or "connection". The validation error above contains the word "timeout", so the app told the user the network was down. Deterministic internal errors are also retried 3 times before failing.
5. **217 passing tests did not catch any of the three user-visible bugs.** The tests either mock the SDK or never drive the real click-select-add flow. The remedy is real-SDK contract tests and real Qt interaction tests (tasks 1–3), plus a GUI smoke run on the packaged exe (section 8).
6. **The Update 6 report is incomplete.** `REVIEW_NOTES.md` still describes Update 5 (it even says "Scope: AGENT_UPDATE_5.md"). It contains none of the Update 6 results (console-window watcher, process launches per stage, CPU profile tables, encode-preset table, UI event-loop delay, cancel-during-encode check, cleanup sizes, package sizes, FFmpeg replacement, aggregate test count), no `.exe` was built, the screenshots were not regenerated, and nothing says whether the code was pushed to GitHub.
7. **Smaller leftovers:** `tests/lc_violations.txt` is a stray output file in `tests/`; `scripts/preflight_gates.py` calls `subprocess.run` directly although every subprocess call is supposed to go through `proc.py`.

**Verified as implemented in the Update 6 code (do not redo):** `proc.py` with a static test (except the script above), in-process PyAV probes with a cache, batched shortening in stage 5, `quota_state.py` and `budget.py`, the CPU-usage setting and event coalescing, `clean_workspace.py`, housekeeping, and an expanded `.gitignore` that now covers `.venv-build/`, `shots*/`, `models/`, media, and so on.

---

## 1. Rules for this update

1. Change only what this file lists. Do not alter translation, TTS, timing, subtitle, or layout behavior beyond what is stated.
2. Never print, log, or write an API key. Never read `.env` into output. Keys appear in the UI only as position plus the last 4 characters.
3. Report only what you measured. State what you cannot verify (how the app looks and feels on the user's display, quality of the dub, quality of translations).
4. Do not commit binaries. Keep build output and media out of the review zip.
5. **Definition of done, from now on, for every update (a standing rule).** An update run is not finished until all of these are true, and the report states each one explicitly:
   - Any UI change has a test that drives the **real widgets** with real events (clicks, selection), not a mocked handler.
   - Any code that calls a third-party SDK has a **contract test with the real SDK classes** (no network): the same constructor arguments and option objects as production.
   - The packaged `.exe` has been **rebuilt**, and it passes `--selftest`, `--layout-check`, and `--gui-smoke` from a sanitized environment (section 8).
   - **Screenshots are regenerated from the new build** (task 5).
   - `REVIEW_NOTES.md` is **rewritten** for this update only (no carried-over text from earlier updates), and the review zip is produced.
   - The aggregate offline test suite is run as one run and the real counts are reported.
   - The cleanup report and the GitHub publishing procedure (`AGENT_UPDATE_6.md` section 8) are done, and the report has a "GitHub" line: pushed or not, the commit, the tag, and the reason if not pushed.
   - If any step fails (for example the build), the report says so plainly with the tail of the log. Never write "done" for a step that did not run.

---

## 2. Task 1 — Fix the API key management (and make slot errors visible)

### 2.1 Add-keys dialog
- The dialog must hand its keys to the caller **before** anything is cleared. Replace the `done()` override with a method `take_keys() -> list[str]` that returns the keys and then clears the field; the caller calls it right after `exec()` returns `Accepted`. Clear the field on reject as well. Never leave key text in a widget longer than needed.
- Keep: one key per line, strip whitespace and surrounding quotes, ignore empty lines, drop duplicates, reject placeholders, cap at 10 keys, and show a clear message (not a crash) when the cap or the credential-store size limit is hit.

### 2.2 Button states and selection
- Connect `key_list.currentRowChanged` (and `itemSelectionChanged`) to one method, `_update_buttons()`, that is also called at the end of `_refresh_rows()`:
  - **Add**: always enabled.
  - **Remove** and **Test selected key**: enabled only when a row is selected.
  - **Up**: enabled only when the selected row is not the first. **Down**: only when it is not the last.
  - **Test all keys**: enabled when at least one key exists and no test is running.
- Make `_refresh_rows(select=...)` take the row to select, and restore the selection after every rebuild: after a move, select the moved row; after a remove, select the nearest remaining row; after an add, select the new last key; on first show with keys present and nothing selected, select row 1.
- Replace the `self.sender()` trick with two explicit slots, `_test_selected()` and `_test_all()`. "Test selected" must test only the selected key.

### 2.3 Never fail silently
- Show any error from the credential store (including a missing or locked backend) in the keys tab status line **and** log it; the tab must say clearly that keys could not be saved.
- Install a global handler for exceptions raised in Qt slots (`sys.excepthook` plus Qt's message handler). It writes the traceback (with no key values) to `%LOCALAPPDATA%\DubTool\logs\` and shows a non-technical dialog once per distinct error with "Copy details" and "Export diagnostics" buttons. Test it with a slot that raises.

### 2.4 Tests that drive the real flow (pytest-qt, in-memory fake keyring backend, no mocks of `exec` or of the handlers)
- Open the real `SettingsDialog`. Click the real "Thêm khóa…" button; use a `QTimer` to fill the real add dialog's field and click its real OK button. Assert: the keys were saved, the rows are rendered masked as `Khóa N   ••••abcd`, no row text contains more than the last 4 characters, and the field was cleared afterwards.
- Select a row with a real mouse click on the item. Assert the enabled state of Up, Down, Remove, and Test-selected for the first, a middle, and the last row.
- Click Up and Down: the order changes in the secret store and the selection follows the moved key. Click Remove and answer the real confirmation box (find the active modal widget with a `QTimer`): the key is removed and the next row is selected.
- "Test selected" calls the (fake) tester once, for the selected key only; "Test all" calls it once per key and updates the badges.
- A slot that raises shows the error dialog and writes the log without a key value in it.
- Repeat the add, select, move, remove, and test flow inside `--gui-smoke` (section 8).

---

## 3. Task 2 — Fix the video duration unit

- In `audio_utils.py`, convert PyAV's `container.duration` from microseconds to seconds using `av.time_base` in `probe_video_info()` (and fall back to the video stream's `duration * time_base` when the container duration is missing). Make `probe_video_info()["duration"]` a number of **seconds** in every code path, including the ffprobe fallback.
- Audit every use of `container.duration`, `stream.duration`, and every caller of `probe_video_info()` (the main window's file info and long-video warning, the service, the headless run, and any ETA or progress code). Put the conversion in one helper, `media_duration_seconds(...)`, and use it everywhere so no caller converts on its own.
- Tests with files of known length, generated with ffmpeg: 2.0 s, 120 s (tiny resolution at 1 frame per second, so it generates quickly), and 1861 s (also tiny). Assert that **`probe_video_info` as well as `probe_duration`** equals ffprobe's value within 0.05 s. Replace the old `> 0.9` assertion with a tight bound.
- UI test with the real main window: choose the 120 s file; the info shows `02:00` and **no** long-video dialog. Choose the 1861 s file; the long-video dialog appears **once**, and the info shows `31:01`. Check the hour format (`1:02:03`) in the formatting helper.

---

## 4. Task 3 — Fix the Gemini timeout and the error classification

### 4.1 The timeout
- Pass the timeout as an **integer number of milliseconds**: `http_options={"timeout": int(timeout_s * 1000)}`, or `types.HttpOptions(timeout=int(...))`. Check the installed `google-genai` version's `HttpOptions` field definition (for example with `inspect`) before choosing, and report the version and the field type you found. Pin the SDK version in the requirements.
- Audit every place that creates a Gemini client or passes options to the SDK (translation, shortening, the key test, the model-list refresh, the comparison tool) and make them all use one factory, so there is one place to get it right.

### 4.2 Contract tests with the real SDK (no network)
- Build a real `genai.Client(api_key="test-key", http_options=...)` with exactly the options the production factory uses, and assert that it constructs without a validation error. Build the real request config objects that production code passes (for example the generation config with JSON output) and assert that they validate. These tests must **fail** when the SDK is not installed (the dev environment has it); do not skip them silently.
- Add the same check to `--selftest`: "Gemini client constructs with the configured timeout (no network)". It must pass on the packaged exe.

### 4.3 Classify errors by type, not by text
- Replace the substring checks in `api.py` with classification by exception type and by the chain of causes (`__cause__` and `__context__`):
  - connection failures (`httpx.ConnectError`, `httpx.ConnectTimeout`, DNS failures such as `socket.gaierror`, `ConnectionError`, SSL errors) → `network`;
  - read or response timeouts → a new code `timeout` ("the server took too long to answer"; the user can retry);
  - quota, invalid key, and unknown model keep their existing codes (use the SDK's error status and reason, not message text);
  - validation or programming errors (`pydantic.ValidationError`, `TypeError`, `AttributeError`, `KeyError`, and so on) → a new code `internal`, with a message saying that something went wrong inside the app and it is **not** a connection problem, plus "Copy details" and "Export diagnostics".
- Add the strings `error.timeout` and `error.internal` in Vietnamese and English.
- **Do not retry deterministic internal errors.** Retries apply only to connection errors, timeouts, rate limits, and server errors (5xx). A validation error fails immediately with the `internal` code.
- Tests with real exception instances of each kind, asserting the code and the message key. The unknown-error path must never produce the network message.

---

## 5. Task 4 — The customer-readable translation file

### 5.1 What it is
Every time the app (or the CLI) produces a dubbed video, it also writes **one extra file** that an ordinary customer can open and read: the English original next to the Vietnamese translation, in sentences, with times. It contains **no technical information**: no ids, no model or mode names (Fast/Accurate), no token or timing numbers, no `fit_status`, no `max_chars`, no `tts_duration`, no "reviewed" flags.

### 5.2 Content
- **Rows are sentences, not ASR fragments.** Use `units.json` (sentence groups) with `translated.json`: join the Vietnamese and English of the segments of each unit in order. If `units.json` is missing (older jobs), build the units with the existing `build_units` function. Skip rows whose English or Vietnamese text is empty.
- **Remove repeated words** at segment boundaries in both languages (the same overlap rule as the ASR duplicate cleanup in Update 3 task 7.3), so a sentence the recognizer repeated is not shown twice.
- **Time column:** the time at which the first segment of the sentence is spoken **in the exported video**, taken from `schedule.json` (`play_start`), so a customer reading along sees the text when the dubbed voice says it. Format `mm:ss`, or `h:mm:ss` from one hour. If `schedule.json` is missing, use the segment start.
- **Language of the labels** follows the app's language setting (Vietnamese by default, English available), using `i18n`.

### 5.3 Word document layout (default format, `.docx`)
Create it with the pure-Python library `python-docx` (check its license from the installed package's metadata and list it in `THIRD_PARTY_LICENSES`). Match this layout; if a file named `sample_readable_translation.docx` is available next to this file, match its look.
- **Page:** A4 portrait, 2 cm margins.
- **Title:** "Bản dịch video" (English: "Video translation"), bold, about 20 pt.
- **Info lines** under the title, label in grey and value in bold: Video (the file name), Thời lượng (the duration of the exported video), Ngôn ngữ ("Tiếng Anh → Tiếng Việt"), Ngày tạo (the creation date, `dd/mm/yyyy`).
- **One table with three columns:** "Thời gian" (about 2.3 cm wide), "Tiếng Anh (bản gốc)", "Tiếng Việt (bản dịch)" (the two text columns share the rest equally). Header row shaded light blue-grey and bold, **repeated at the top of every page**. Thin light-grey borders, comfortable cell padding, 1.25 line spacing in the text columns, a very light shading on every second row. **A row never splits across two pages.** The time cell is grey.
- **Font:** Arial 10.5 pt (set the name for all script types so Vietnamese diacritics render everywhere).
- **Footer on every page:** a small grey note, "Bản dịch được tạo tự động bằng AI. Nên nhờ người biết tiếng Việt kiểm tra trước khi sử dụng hoặc đăng tải." (English equivalent), and "Trang X / Y" (page numbers as Word fields).
- Keep python-docx's default template files available when frozen (see 5.6).

### 5.4 Text fallback format (`.txt`)
UTF-8 with a BOM so Windows Notepad shows diacritics. A title and the same info lines, then for each sentence:

```
[00:14]
EN: <English sentence>
VI: <Vietnamese sentence>

```

with the same disclaimer at the end.

### 5.5 Where, when, and how it is exposed
- **File name and place:** next to the video, in the user's output folder: `<video name>_vi_translation.docx` (or `.txt`). The overwrite and rename rules (`_vi_2`, `_vi_3`, …) are shared with the video so the two files always keep matching numbers.
- **A new last stage, `s8_export_readable`,** runs after stage 7 and **before** the job folder is cleaned up (the app deletes intermediates after success by default; the export must not depend on them surviving). It reads only small files: `translated.json`, `units.json`, `schedule.json`.
- **Failure isolation:** if the export fails, the video result stays successful. Show a warning ("the video is ready; the translation file could not be created"), log the reason, and try the `.txt` fallback automatically when the Word file failed.
- **UI:** in the Options panel add a checkbox "Xuất kèm bản dịch dễ đọc" (default **on**) and a format choice "Word (.docx)" / "Văn bản (.txt)". After a successful run, add a button **"Mở bản dịch"** (use `os.startfile`) next to "Open folder" and "Play", and list both files in the success summary. The layout check must still pass.
- **CLI:** `--no-readable` and `--readable-format docx|txt` on `dubtool run`, plus `dubtool export-readable <job_id> [--format docx|txt] [--out PATH]` to regenerate it from an existing job. `dubtool.api.run_dub` returns the path of the readable file in its result.
- The export is not part of the resume hash (it never changes how the video is produced).

### 5.6 Packaging
- Add `python-docx` (and its `lxml` dependency) to the pinned requirements. In the PyInstaller spec collect python-docx's data files (its default template, for example with `collect_data_files("docx")`) and its hidden imports. **A frozen app without the template files fails at runtime**, so verify it on the packaged exe.
- `--selftest` adds: create a small `.docx` in a temp folder, read it back, and check the Vietnamese text round-trips.

### 5.7 Tests
- From a fixture modeled on `translated_fast.json` (including the repeated sentence "The answer is always yes" at a segment boundary, and fragments that split sentences) plus `units.json` and `schedule.json`: the document has one row per sentence, no repeated sentence, correct times (taken from the schedule, not from the original starts), Vietnamese diacritics intact, and an `h:mm:ss` time for a row after one hour.
- **No technical content:** the text of the document must not contain any of: `max_chars`, `fit_status`, `tts_duration`, `reviewed`, `gemini`, `flash`, the words "Fast"/"Accurate"/"Nhanh"/"Chính xác" as mode labels, or a JSON-looking string.
- The `.docx` is a valid package: it unzips, `word/document.xml` parses, the table has the header row marked as repeating, and the rows are marked as not splitting across pages.
- The `.txt` format has the BOM and the specified blocks.
- File naming and collisions stay in step with the video's; failure isolation (an exception in the export leaves the video result `ok` and falls back to `.txt`); the export works when intermediate files were deleted after the run.
- **Appearance check:** if LibreOffice (`soffice`) is installed, convert the sample document to PDF and then to images and put the PNGs next to the screenshots; report their paths. If it is not installed, say so. You cannot judge how it looks; the user will.

---

## 6. Task 5 — Rebuild the exe and refresh the screenshots after every update

- Run `packaging/build_exe.ps1` at the end of **every** update. If the build fails, report the failure with the tail of the log; do not claim the update is finished.
- Write `dist/DubTool/BUILD_INFO.txt`: the update number, the git short hash (when the folder is a repository), the UTC build timestamp, and the SHA-256 of `DubTool.exe`. `DubTool.exe --version` and the About dialog show the same build id. The report states the absolute path of `dist\DubTool\DubTool.exe`, its size, and the build timestamp, so the user can check they are running the new build.
- **Screenshots come from the packaged exe**, with `--screenshots`. Write them into a **new** folder `screenshots/<build id>/` with an `INDEX.txt` (state, window size, scale, build id), and delete every older screenshot folder (keep only the newest). Never reuse old PNGs.
- Required states: idle, video selected, ready, running, finished (with the "Mở bản dịch" button), error dialog, first run, and the Settings dialog tabs: **API keys (with 3 fake keys, one selected, and one row showing the quota badge)**, Models, General, and Advanced. Use fake data only: no key may be visible in any image.
- A test asserts that every required PNG exists, that its modification time is not earlier than the build timestamp, and that `INDEX.txt` lists the current build id.

---

## 7. Task 6 — Close out Update 6 and tidy up

1. **Produce the Update 6 results that are missing from the report** (run them now, or write "not done" with the reason): the console-window watcher result (expected 0 windows, with the control); process launches per stage before and after; the CPU profile tables for both modes; the encode-preset table and the chosen default; the UI event-loop delay; the cancel-during-encode check; the package size before and after pruning; whether the FFmpeg snapshot was replaced by a release build; the cleanup before and after sizes; the quota state behavior and the requests used per stage; the cause of the Accurate-mode latency and of the enforcement pass that changes nothing.
2. Delete `tests/lc_violations.txt` and any other non-test file in `tests/` (keep fixtures). Route `scripts/preflight_gates.py` through `proc.py` (or add a narrow, commented exception to the static test).
3. Rewrite `REVIEW_NOTES.md` for this update only.
4. Run the cleanup script (dry run, then the regenerable categories) and report the sizes before and after.

---

## 8. Verification on the packaged executable

Rebuild, then verify from a different folder with a sanitized environment (PATH limited to Windows system folders, temporary `APPDATA` and `LOCALAPPDATA`).

Add `--gui-smoke` to `app/main.py` (it works from source and from the packaged exe). It uses a fake credential backend, a fake pipeline service, and a generated 2-minute test video (tiny resolution), and it drives the **real** widgets with real events. It must:
1. Create the main window, import the 2-minute video through the real handler, and assert that the info shows `02:00` and no long-video dialog appears.
2. Open the real Settings dialog, add two keys through the real add dialog, select a row, move it up, remove one with the confirmation answered, and run "Test selected" (fake tester); assert the button states along the way.
3. Start a run in each mode with the fake service, finish it, and assert that the success state shows the "Mở bản dịch" button and that both the video and the readable file exist in the output folder.
4. Print a table of steps with pass or fail and exit non-zero on any failure.

Checks:
- [ ] `--selftest`, `--layout-check`, `--screenshots`, and `--gui-smoke` pass from the **packaged** exe (paste the tables).
- [ ] `--headless-run <sample> <outdir> --mode fast` completes with the real Gemini API (the timeout fix is proven by a real translation), produces the video and `<name>_vi_translation.docx`, and the readable file passes the tests of 5.7 (open it with python-docx and report the row count and the first 5 rows as text). Then do the same for `--mode accurate` **if today's free-tier quota allows**; if it does not, say so.
- [ ] `ffprobe` on the video: one H.264 video stream, one AAC audio stream, same resolution and frame rate as the input, duration within 1 s of the input.
- [ ] No key found anywhere in the dist folder, logs, screenshots, the readable file, or the temp profile (quiet search).
- [ ] The aggregate offline test suite passes in one run; report the real counts (passed, failed, skipped, and why).

---

## 9. Report back

Follow `AGENT.md` section 12 and `AGENT_UPDATE.md` 7.2. Include:

1. Files added and changed; commands run; the aggregate test counts.
2. **Bug fixes:** for each of the three user-reported bugs, the root cause as you confirmed it, the fix, and the test that now covers it (the real-widget tests, the duration tests with known files, and the real-SDK contract test). Include the `google-genai` version and the type of `HttpOptions.timeout` that you found.
3. **Readable translation file:** the paths of a real sample document and its `.txt` version, the row count, the first 5 rows as text, the page images if LibreOffice was available, and the list of technical strings you verified are absent.
4. **Build:** the absolute path of `DubTool.exe`, its size, the build id, the timestamp, and the SHA-256. The `--selftest`, `--layout-check`, `--screenshots`, and `--gui-smoke` tables.
5. **Screenshots:** the folder path and the contents of `INDEX.txt`.
6. **Update 6 close-out:** each item from task 6.1 with its result or "not done: reason".
7. **Cleanup and GitHub:** sizes before and after; the GitHub line (pushed or not, commit, tag, gate results, reason if not pushed, and any question you need answered).
8. What you could not verify: how the app and the Word document look on the user's computer, the sound and translation quality, behavior with the user's antivirus, and other Windows versions or DPI settings.
9. Anything you noticed but did not fix.
10. The review zip path and size.

Then stop and wait for the user's feedback.
