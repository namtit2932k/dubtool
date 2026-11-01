# AGENT_UPDATE_9.md — Lock the UI while running, a cleaner import view, a real "progress details" panel, a working "open folder" button, a proper About box and version, README images that work, a lighter package, and the gaps left by Update 8

Ninth update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_8.md` still apply: ground rules, data contracts, resumability, secret safety, the review zip rule, the reporting format, the **Definition of done** in `AGENT_UPDATE_7.md` section 1 rule 5 (real-widget tests, real-SDK contract tests, rebuilt exe, fresh screenshots, rewritten `REVIEW_NOTES.md`, one aggregate test run, cleanup report, and the GitHub line from `AGENT_UPDATE_6.md` section 8). This file overrides them only where it says so.

The user reviewed the Update 8 package and tried the app. These are the user's requests, in the user's words, and what this update does about each:

1. **Disable the options in the main window while a run is in progress (screenshot 1), and also the options in Settings.**
2. **A more sensible view after a video is imported (screenshot 2):** remove the drag-and-drop area once a file is chosen, and add an **X** button to deselect the imported file.
3. **The arrow button under the progress area (screenshot 3) must become a clearly labeled "progress details" button.** Clicking it opens the area below, which shows in detail what the app is doing, so the user can tell it is not stuck.
4. **About box (screenshot 4):** add the author name `sickyturtlez`, remove the FFmpeg line, and version the app `1.0.8` for update 8 (that is, `1.0.<update number>`; this update is `1.0.9`).
5. **The "Mở thư mục xuất" button (screenshot 5) does nothing.**
6. **The jobs folder and "Dọn dẹp ngay" button in Settings are unclear** (what they are for and how they work).
7. **The package folder is very heavy** (`ctranslate2.dll`, `ffmpeg.exe`, `ffprobe.exe`, …): make it lighter if possible.
8. **The screenshots in the README are broken.**

(The user also asked how to make voice generation faster and where the API keys are stored. Those are discussion questions that are answered in the chat, not tasks for this update. Do **not** implement anything about TTS speed in this update.)

---

## 0. Evaluation of the Update 8 package and findings (verified in the delivered code and reports)

### 0.1 What Update 8 delivered (do not redo)
Every module the report names exists in the source (`punctuate.py`, `fillers.py`, `faults.py`, `netcheck.py`, `av_check.py`, `tts/fallback.py`, `llm/budget.py`, `llm/quota_state.py`, `llm/keypool.py`, `eval_translation.py`, `app/ui/fillers_dialog.py`, `app/asr_stats.py`, `packaging/fillers.yaml`). The offline suite is reported as 412 passed and 2 skipped. TTS and Gemini recovery were proven on the packaged exe with injected faults (`--fault tts:fail-first=6`, `--fault llm:503-first=4`), including 14 genuine "No audio was received" errors absorbed in one run. The Whisper variants table was measured on `test2`: current settings 41.0% of segments end with sentence-final punctuation; `condition_on_previous_text=true` 11.1%; `initial_prompt` 52.5%; `medium` 49.1%; real-time factors 0.25 (small) and 0.76 (medium). The filler detector found **no** token above the threshold in `test2`, including `girl`.

### 0.2 Gaps and findings
1. **The build the user ran is not the Update 8 build.** The user's About box shows `Version: 1.0.0` and `build id: Update 7 (e271c90)`, while the report says the Update 8 build id is `Update 8 (a7b511a)`. Either the user ran an older folder, or the packaged output is easy to confuse. The zip is still named `DubTool-1.0.0-win64.zip`, so nothing in the file names tells the builds apart.
2. **Two different version constants exist:** `src/dubtool/__init__.py` has `__version__ = "0.1.0"` while `app/main.py` has `APP_VERSION` (shown as 1.0.0). The About box prints the second one.
3. **The "open output folder" bug has a clear cause.** `app/ui/main_window.py: _open_in_explorer()` starts `explorer` through `proc.run()`. The helper applies `STARTUPINFO` with `SW_HIDE` and `CREATE_NO_WINDOW` to every child (the console-window fix from Update 6), so **Explorer's window is created hidden**. The "play video" and "open translation" buttons use `os.startfile()` and are not affected.
4. **The running state only disables the two Generate buttons.** `main_window.py` (lines around 617–642) disables `fast_btn` and `accurate_btn`; the drop zone, output folder button, description box, option checkboxes, slider, voice and format dropdowns, and the Settings menu stay active during a run.
5. **The drop zone stays visible after a video is imported**, and there is no way to deselect the video.
6. **The log toggle is an unlabeled `QPushButton("▸" / "▾")`** (28 px wide, `widgets.py`), and the log area shows almost nothing useful (for example only "Giai đoạn Nhận diện tiếng nói dùng nhiều CPU — đây là hành vi bình thường."). Long stages (speech recognition, voice generation, video encoding, waiting for a model) give no sign of life.
7. **README images point to files that cannot exist.** `README.md` references `screenshots/03_ready.png` and `screenshots/04_running.png`, but the build script writes `screenshots/update_<N>_<hash>/...` and deletes older folders, and `screenshots/` is in `.gitignore`, so the links are broken locally and on GitHub.
8. **The package was not pruned.** `packaging/dubtool.spec` only excludes `tkinter`, `test`, `unittest`, `xmlrpc`, and `pydoc_data`; there are no Qt module or plugin excludes. `packaging/build_exe.ps1` still ships `ffprobe.exe` although media probing runs in-process through PyAV since Update 6. Reported sizes: `dist/DubTool` 693.9 MB unpacked, `dist/DubTool-1.0.0-win64.zip` 267.9 MB.
9. **Verification gaps in the Update 8 report:**
   - The real end-to-end runs on the user's videos did **not** exercise punctuation restoration with the real model (the second run's injected faults made the punctuation window be rejected and the original text kept), and the completeness re-translate pass **did not run** at all ("no re-translate pass ran in these runs").
   - The fidelity preset numbers for `balanced` versus `faithful` on `test2` (required in `AGENT_UPDATE_8.md` 6.3) are not reported.
   - `eval-translation` exists but no output file was produced for the user.
   - The **GitHub line is still missing** (the report says "see the closing message of this session"). The repository folder contains a `.git/` of about 106 MB; it is not known what is in it.
   - `REVIEW_NOTES.md` states that an earlier draft "contained fabricated module names" and had to be rewritten. That is a reliability warning: claims in reports must be backed by command output (rule 4 below).
   - The cleanup reclaimed only 42.4 MB; the project folder is now about 3.0 GB (`.venv-build` 997 MB, `dist/DubTool` 694 MB, `sample/` 341 MB, the zip 268 MB, `vendor/` 245 MB, `jobs/` 233 MB, `.git/` 106 MB, `models/` 87 MB).
10. **The measured system clock offset is +54.36 s** (from the network check of Update 8). A wrong clock can make HTTPS and token-based services misbehave. It is not proven to be related to the TTS errors, but it is cheap to surface.

---

## 1. Rules for this update

1. Change only what this file lists. New behavior sits behind config keys where it changes outputs; UI changes need no config.
2. Never print, log, or write an API key. Never read `.env` into output.
3. Report only what you measured. State what you cannot verify (how the windows look on the user's display, the dub's sound, the translation quality).
4. **Every number and file claim in your reports must be backed by the command and the output you ran** (paste the relevant lines). Do not describe what "should" exist. After writing `REVIEW_NOTES.md` and the report, re-check each named file and number against the working tree.
5. Do not implement anything about TTS speed (the user only asked for options in the chat).
6. The Definition of done from `AGENT_UPDATE_7.md` applies, including the GitHub line in the report itself (not "see the closing message").

---

## 2. Task 1 — Lock the UI while a run is in progress

### 2.1 One busy state
Add a single `set_busy(bool)` method on the main window that is the only place that decides which widgets are editable. It is called when a run starts and when it ends in any way (success, error, cancel, close of a dialog that aborts the run).

**Disabled while busy** (state them in code as an explicit list, and test them):
- the drop zone and the file card's X button (task 2), and drag-and-drop of a new file onto the window;
- the output folder button;
- the video description box (make it read-only);
- every checkbox and dropdown in Options (burn subtitles, save `.srt`, keep intermediate files, readable export and its format, voice), the original-audio slider, the options group's own checkbox, and any later option;
- both Generate buttons (already disabled).

**Stay enabled:** Cancel, the progress details button (task 3), the menu entries "About" and "Export diagnostics", the window controls, and after a run ends the Resume, Start over, Open folder, Play, and Open translation buttons.

### 2.2 Settings while busy
When the Settings dialog is opened during a run, show a banner at the top ("Đang chạy — chưa thể thay đổi cài đặt" / "A run is in progress — settings are read-only") and disable **every editable control in every tab** (API keys list and its buttons, models dropdowns, language, voice, Whisper model, CPU level, encode speed, priority, fillers, jobs/cleanup settings, and all checkboxes). Only the Close button and read-only actions that do not affect the run stay enabled: "Kiểm tra kết nối" and "Xuất chẩn đoán". Do not apply the dialog's changes when it was opened read-only.
If the run ends while the dialog is open, re-enable the controls.

### 2.3 Tests (real widgets)
- Start a fake run through the real handler and assert that **each** widget in the list of 2.1 is disabled and the "stay enabled" ones are enabled.
- Enumerate all widgets of these types in the main window and in the Settings dialog (button, checkbox, combo box, slider, line edit, plain text edit, spin box, list widget) while busy and assert that every one is disabled, except an explicit allowlist (Cancel, details toggle, About/diagnostics, Close, Test-connection, Export-diagnostics). A widget added in the future that is forgotten will fail this test.
- After a success, an error, and a cancel, everything is enabled again and the values the user had set are unchanged.
- Dropping a file onto the window while busy is ignored.

---

## 3. Task 2 — A cleaner view after importing a video

- Before a video is chosen: the drop zone as today.
- After a video is chosen: **hide the drop zone** and show a **file card** in the same place: the file name (elided in the middle if too long, with the full path in a tooltip), the details line (duration, resolution, frame rate, size) and an **X** button at the right with the tooltip "Bỏ chọn video" / "Remove video".
- Clicking **X** clears the selection: the file card disappears, the drop zone returns, the Generate buttons are disabled again, the info is reset, and the description box shows the text saved for no video (empty). X is disabled while busy.
- Dropping a new file onto the card (when not busy) replaces the selection after the same long-video check as a normal import.
- The layout check (`--layout-check`) must still pass for the card at all sizes.

Tests (real widgets): the card appears after a real import of the generated 120-second test video with the right info; X returns to the drop zone and disables Generate; replacing by drop works; X is disabled while busy; the file name elision does not overlap the X button at the narrowest window size.

---

## 4. Task 3 — "Progress details": a real activity view

### 4.1 The button
Replace the unlabeled `▸/▾` toggle with a clearly labeled flat button whose text is **"Chi tiết tiến trình"** (English "Progress details") with an arrow that changes direction (`▸` closed, `▾` open) inside the label. Use `i18n` strings. Remember the open or closed state across runs in `settings.json` (default closed), and open it automatically when a run ends with an error.

### 4.2 What the area shows
When open it shows, in this order:
1. A header line that is always current: **"Đang làm: <stage name> — <what exactly>"** (for example "Đang làm: Tạo giọng đọc — đoạn 23/80", "Đang làm: Nhận diện tiếng nói — 1:12 / 3:37", "Đang làm: Dịch — chờ phản hồi từ model (45 giây)").
2. A **liveness line** that updates every second: "Cập nhật lần cuối: N giây trước" and a small animated indicator while a worker is running. If no pipeline event has arrived for `ui.quiet_after_s` (default 20 s), add "— vẫn đang chạy" when the worker thread and its child processes are alive; if the worker thread died without a final event, show an error state instead of waiting forever.
3. A scrolling **activity log** of meaningful events with the time (`HH:MM:SS`): stage start and finish with elapsed time; coarse per-item progress (for voice generation one line per item, coalesced to at most 10 lines per second and capped at 2,000 lines as today); cache hits ("Dùng bản đã lưu cho 20 đoạn"); waiting states ("Dịch vụ giọng đọc đang bận, thử lại sau 60 giây", "Model đang quá tải, thử lại sau 30 giây"); retries; key switches (positions only, never values); model fallbacks; punctuation, filler, and completeness steps; the final summary.
4. Two small buttons: **"Sao chép chi tiết"** (copies the log text, redacted) and **"Mở thư mục log"** (uses the fixed open function of task 5).
Also show the sub-activity next to the running stage in the stage list (for example "Tạo giọng đọc 23/80").

### 4.3 Make long stages report real progress (heartbeats)
The pipeline must emit a progress event at least every `progress.heartbeat_s` (default 10 s) from every long operation, so the panel never goes quiet:
- **Speech recognition:** progress from the end time of the latest transcribed segment divided by the video duration (the segment generator already yields results progressively).
- **Video encode and audio mix:** run FFmpeg with `-progress pipe:1 -nostats` and parse `out_time` (or `out_time_us`) to compute the percentage against the known duration; read the pipe without blocking the UI thread and keep the existing cancel behavior (terminate the process tree).
- **Voice generation:** counts, plus a heartbeat during cooldowns with the remaining seconds.
- **Translation and other model calls:** a heartbeat with the elapsed seconds of the outstanding request.
- Also feed these into the overall bar and the ETA, replacing the "indeterminate" behavior for speech recognition and encoding.
Add the new event kinds to `ProgressEvent` without breaking the existing consumers.

### 4.4 Tests
Real widgets with a fake service: the button text and arrow; opening shows the header, the liveness line, and the log; a quiet period (fake clock) shows "vẫn đang chạy", and a dead worker shows the error state; events are coalesced and capped; copy puts redacted text on the clipboard; the state is remembered; auto-open on error. A unit test for the FFmpeg `-progress` parser with sample output and for the speech-recognition progress calculation. An integration check with a real short FFmpeg encode that the parsed percentage rises from 0 to 100.

---

## 5. Task 4 — About box, author, and version

### 5.1 About box
Show exactly: the app name, `Phiên bản: 1.0.9` (use `i18n`), the build id line, `Tác giả: sickyturtlez` (English "Author"), and the licenses line ("Giấy phép và nguồn: xem THIRD_PARTY.md…"). **Remove the FFmpeg line** (the string `about.ffmpeg`, `_ffmpeg_version()` call in the dialog, and the unused translation keys). Define the author as a constant `AUTHOR = "sickyturtlez"` in `src/dubtool/branding.py` and use the constant in the dialog. Do not add a watermark or any other credit anywhere else.

### 5.2 One version, `1.0.<update number>`
- Single source of truth: `src/dubtool/__init__.py: __version__`. Set it to **`1.0.9`** for this update, and make `app/main.py` (`APP_VERSION`), `pyproject.toml` (dynamic version), `packaging/version.txt`, the executable's version resource, the zip name (`DubTool-1.0.9-win64.zip`), `BUILD_INFO.txt`, `--version`, the diagnostics, and the About box all read it. Remove the other constants.
- **Rule for every later update:** the version is `1.0.N` where N is the number of the update being delivered. Put this rule in `README_BUILD.md`, and make the build script read the update number from `packaging/update_number.txt` (or the version itself) so nobody has to remember it.
- `BUILD_INFO.txt` keeps `build id: Update 9 (<hash>)` and now also the version.
- Add a test that all of these places report the same version, and that `DubTool.exe --version` prints `1.0.9` and the build id.
- The build script prints, at its end, the **absolute path of the exe and the zip that the user should run**, and leaves **only one** `dist/DubTool` folder and **one** zip (older ones are deleted, as `AGENT_UPDATE_6.md` 6.4 already requires).

---

## 6. Task 5 — Fix "open output folder"

- **Cause (finding 3):** Explorer is started through the hidden-window helper. Fix: on Windows use `os.startfile(str(folder))`; do not start GUI programs through the hidden-window path. Add an explicit parameter to `proc.run`/`proc.popen` (`gui=True`) that skips `SW_HIDE` and `CREATE_NO_WINDOW` for GUI children, and use it for the one case that needs arguments: reveal a file with `explorer /select,"<file>"` (so the output video is highlighted when it exists).
- **Which folder:** open the folder of the **last successful result** (`result.out_video.parent`), not the current value of the folder field, which the user may have changed. Fall back to the field when there is no result. If the folder does not exist, show a clear message instead of failing silently.
- The static test of Update 6 (no direct `subprocess` calls) must still pass; add a test that every use of `explorer` goes through `gui=True` or `os.startfile`.
- **Real check on Windows in `--gui-smoke`:** call the open-folder action on a temporary folder with a unique name, wait up to 5 seconds, and detect an Explorer window (`CabinetWClass`) whose title contains the folder name with `EnumWindows`; then close it (`WM_CLOSE`). The step passes only when the window was found. Report the result.

---

## 7. Task 6 — Make the jobs folder and "Dọn dẹp ngay" understandable

Do not change what they do. Make it clear in the interface:
- In Settings → Advanced, put them in a group titled **"Tệp tạm của các lần dịch"** ("Working files of past runs") with a short, plain explanation: "Mỗi lần dịch, DubTool lưu các tệp tạm (âm thanh, bản dịch, giọng đọc đã tạo, nhật ký) trong thư mục này. Nhờ vậy có thể tiếp tục nếu bị gián đoạn và không phải tạo lại. Video và bản dịch xuất ra không nằm ở đây."
- Show the folder path (with an **"Mở thư mục"** button that uses the fixed open function), its **current size**, and the number of runs stored.
- The setting "Giữ job tối đa (ngày)" gets a one-line explanation: "Các lần dịch cũ hơn số ngày này sẽ được xóa khi mở ứng dụng."
- **"Dọn dẹp ngay"** first shows a confirmation that lists what will be deleted (number of runs, total size, the oldest date), and states what will **not** be deleted: output videos and translations, the running job, and the speech-recognition model files. Offer two choices in that dialog: "Xóa các lần dịch cũ hơn N ngày" (default) and "Xóa tất cả tệp tạm (trừ lần đang chạy)".
- The Options checkbox "Giữ các tệp trung gian" gets a tooltip: "Nếu tắt, tệp tạm của lần dịch được xóa ngay khi hoàn tất; bạn sẽ không thể tiếp tục hoặc tái sử dụng chúng."
- Tests: the group, texts, size, and count are present; the confirmation lists the right numbers (temporary jobs folder with fake jobs); the deletion never touches an output folder; the running job is kept.

---

## 8. Task 7 — README images that work

1. Create `docs/images/` (tracked in Git, small optimized PNGs, each under about 300 KB; downscale or compress if needed). Keep `screenshots/` ignored.
2. `scripts/update_readme_images.py` copies the chosen states (at least: ready, running with the details panel open, finished with the three buttons, Settings → API keys) from the newest `screenshots/update_<N>_<hash>/` folder into `docs/images/` with stable names, and the build script runs it after the screenshots step.
3. `README.md` uses relative links to `docs/images/<name>.png` only, and describes the **current** interface (after this update).
4. `scripts/check_readme_links.py` verifies that every image and relative link in `README.md` and `README_BUILD.md` resolves to a **tracked** file (use `git ls-files` when a repository exists, otherwise the filesystem) and fails otherwise. Add it to the pre-push gates (`AGENT_UPDATE_6.md` 8.3) and to the aggregate tests.
5. Verification: report the list of images and sizes, and the result of the link check.

---

## 9. Task 8 — Make the package lighter (measure first, prune only what is verified)

### 9.1 Measure
Print the 25 largest files and folders inside `dist/DubTool`, with sizes, and for each one say what needs it (for example `ctranslate2.dll`: speech recognition; `ffmpeg.exe`: mix, encode, subtitle burn; Qt libraries; PyAV's bundled media libraries; onnxruntime). Include a total per group (Qt, FFmpeg and media libraries, speech recognition runtime, Python runtime, other).

### 9.2 Candidates (each only after checking and testing)
1. **Remove `ffprobe.exe`** (and its bundling step in `build_exe.ps1`) if no code path still needs it. Probe every sample container type (mp4, mkv, mov, webm, avi, m4v) with PyAV and report the results; remove only when all succeed, and make the PyAV failure path give a clear error message instead of calling a missing program. Update `selftest`, tests, and `THIRD_PARTY.md`.
2. **Qt pruning:** add `excludes` in the spec for Qt modules the app does not import (for example WebEngine, QML/Quick, 3D, multimedia, SQL, test modules) and prune unused plugins and translations (keep the Windows platform plugin, the image formats actually used, the styles actually used, and the English and Vietnamese translations). Check which Qt DLLs end up in the folder (for example software OpenGL, PDF, Network, Svg) and remove those that the self-test, the layout check, the GUI smoke, and a real run show are unnecessary. If you are unsure about a DLL that only matters on machines without graphics drivers (for example the software OpenGL fallback), **keep it** and say so.
3. **FFmpeg build:** report the size of `ffmpeg.exe` and whether a smaller **release** build still provides every filter and encoder this app uses (`libx264`, `libass`/`subtitles`, `amix`, `alimiter`, `sidechaincompress`, `atempo`, `silenceremove`, `drawtext` only if used). Switch only when every check passes (subtitle burn, mix, encode, the layout of the subtitles, `--selftest`), and verify the publisher's checksum and record the version and source in `THIRD_PARTY.md`. Note that PyAV ships its own media libraries as well; report whether they overlap with what the app needs, but do not replace the encode path in this update.
4. **Speech recognition runtime:** confirm that only the CPU build is shipped (no GPU/CUDA libraries); report the size of `ctranslate2` and of every dependency it pulls in. Do not change the engine.
5. **Compression:** keep the zip as the distribution format; report the zip size before and after. Do not add UPX.
6. Remove duplicated or stray files (for example copies of the same DLL, test data, `.pyc`/`.pdb` files, documentation inside packages) that the self-test and a real run do not need.

### 9.3 Verify and report
After pruning, rebuild and run `--selftest`, `--layout-check`, `--screenshots`, `--gui-smoke`, and `--headless-run` in Fast mode on a real video from a sanitized environment. Report a before and after table (folder size, zip size, per group, number of files). **Do not promise a size**; report what was achieved and what could not be removed and why.

---

## 10. Task 9 — Close the gaps left by Update 8

1. **Real end-to-end quality runs on the user's videos** (`test2`, and `test3` if present), Fast mode with the real model, **no fault injection**: report the structural metrics before and after punctuation restoration (share of segments ending with sentence-final punctuation, longest word run without a final mark, number of units, longest unit duration), how many punctuation windows were accepted or rejected, the number of units flagged by the completeness detector and the result of the re-translate pass, the filler findings, and the first 15 rows of the readable document as text. State the number of requests used per stage.
2. **Fidelity preset numbers on `test2`:** for `balanced` and `faithful`, the `fit_status` counts, the number of shifted clips, the maximum shift, and the total lag at the end of the video.
3. **Produce the evaluation file:** run `dubtool eval-translation <job> --variants current,punctuated,faithful` on `test2`, copy the result into the review zip, and give its path.
4. **Present, do not decide:** the measured effect of an `initial_prompt` on punctuation (41.0% to 52.5% of segments, with no extra time) and of `medium` (49.1%, about 3 times slower). Add a Settings → General checkbox "Gợi ý dấu câu cho nhận dạng giọng nói" bound to `asr.initial_prompt`, **default unchanged (off)**, with a one-line description of the trade-off. The user decides whether to turn it on.
5. **The `.git` folder (about 106 MB):** report `git count-objects -vH`, the 10 largest objects in the history (path and size), and whether any binary, virtual environment, model, or media file was ever committed. If large or forbidden files are in the history, **do not push**; report it and ask the user how to proceed (rewriting local history that has never been pushed is possible only with the user's explicit approval).
6. **GitHub line in the report itself:** pushed or not, commit, tag, gate results, the reason if not pushed, and any question you need answered. The README image check (task 7) is now one of the gates.
7. **Clock offset hint:** when the measured offset is larger than 30 seconds (it was +54.36 s), add a one-line hint to the `tts_unavailable` message and to the diagnostics: "Đồng hồ máy lệch khoảng N giây so với máy chủ — hãy đồng bộ giờ Windows". Do not change the clock. The hint states a possibility, not a cause.
8. **Report the exact thing to run:** the absolute path of `dist\DubTool\DubTool.exe`, its build id, version, and SHA-256, so the user can compare it with the About box.

---

## 11. Verification

Rebuild the exe and verify from a sanitized environment as in `AGENT_UPDATE_7.md` section 8. Extend `--gui-smoke` with: busy state disables the listed widgets in the main window and in Settings; the file card and its X; the labeled progress details button with the live header and liveness line; the About box text (contains `sickyturtlez` and `1.0.9`, does not contain "FFmpeg"); the open-folder step with Explorer window detection; the Advanced tab's working-files group.

Required screenshots (from the packaged exe, new build folder): the main window with a video imported (file card), the busy state of the main window, Settings opened during a run (read-only banner), the details panel open with log lines, the About box, Settings → Advanced with the working-files group, and the finished state with the three buttons. Also copy the README images (task 7).

Checks:
- [ ] The version is `1.0.9` in the About box, `--version`, `BUILD_INFO.txt`, the zip name, and the exe's version resource; the build id says `Update 9`.
- [ ] `--gui-smoke` passes, including the Explorer window detection (paste the table).
- [ ] The layout check passes at all sizes and scale factors with the file card and the details panel.
- [ ] The README link check passes and the README shows the new images.
- [ ] The size table of task 8, before and after.
- [ ] A real run in Fast mode from the packaged exe produces the video and the readable document; no key found anywhere (quiet search).
- [ ] The aggregate offline suite passes in one run; report the real counts (passed, failed, skipped, and why).

---

## 12. Report back

Follow `AGENT.md` section 12, `AGENT_UPDATE.md` 7.2, and the Definition of done. For every number and every named file, paste the command and its output. Include:

1. Files added and changed; the aggregate test counts.
2. The user's eight requests, each with what changed and how it was verified (the busy-state widget list and test, the file card, the details panel with a real heartbeat run, the About box and version, the open-folder check with the Explorer detection result, the working-files group, the README images, the size table).
3. The build: absolute path of the exe and the zip, build id, version, SHA-256, and the screenshot folder with `INDEX.txt`.
4. The close-out of Update 8 (task 9), item by item with real output.
5. Cleanup sizes, and the questions that still need the user's answer (`.venv-build`, `sample/`, `vendor/`, `models/`).
6. The GitHub line.
7. What you could not verify: the look and feel on the user's display, the dub's sound, translation quality (the user judges), and antivirus or SmartScreen behavior.
8. Anything you noticed but did not fix.
9. The review zip path and size.

Then stop and wait for the user's feedback.
