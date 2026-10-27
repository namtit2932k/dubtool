# AGENT_UPDATE_5.md — GUI feedback: model dropdowns, multiple API keys with automatic fallback, remove the key hint, fix the overlapping progress section — then package the .exe

Fifth update. All earlier files (`AGENT.md`, `AGENT_UPDATE.md` … `AGENT_UPDATE_4.md`) still apply: ground rules, data contracts, resumability, secret safety, the review zip rule (`AGENT_UPDATE.md` 7.2), the reporting format. This file overrides them only where it says so.

This is the user's feedback after trying the GUI from the Update 4 stop-point-1 package. The user asked for exactly four things:

1. **The model choice in Settings must be a dropdown list.**
2. **Multiple API keys in Settings.** If the current key is rate limited, switch to another key **sequentially**.
3. **Delete the text "Lấy khóa miễn phí…"** in Settings.
4. **Fix the UI of the progress section: it collapses onto/overlaps another section.**

Update 4's Stage 3 (packaging, `AGENT_UPDATE_4.md` sections 10–11 and its checks) has not been done yet. After tasks 1–5 below, continue straight into it (section 8). The only stop point is at the very end.

---

## 0. Findings from reading the delivered code (each was verified in the source)

1. **Model overrides in Settings have no effect.** `api.run_dub` calls `modes_mod.get(options.mode)` without the settings, and the UI never passes `extra_overrides`. So whatever the user types in Settings → Advanced is ignored at run time; both buttons always use the shipped model ids. A dropdown on top of this would do nothing, so this must be fixed as part of task 1.
2. **The API key is injected into `os.environ` and never removed** (`api.py`: `os.environ["GEMINI_API_KEY"] = key`). Consequences: after the user deletes the key in Settings, `secrets.get_gemini_key()` falls back to that leftover environment variable, so the deleted key keeps working until the app restarts; and every child process (FFmpeg) inherits the key. The key must be passed explicitly to the provider, not through the process environment.
3. **The existing multi-key rotation (`GEMINI_API_KEY_2…` in `llm/gemini.py` + `llm/client.py`) is not good enough for the app:**
   - The app only ever provides one key (`get_gemini_key()`), so rotation is never used from the GUI.
   - `rotate_key()` only moves forward and never comes back; a key that hit a per-minute limit is never reused, and once the last key is limited there is nowhere to go.
   - After a successful rotation the client still sleeps up to 30 s and still burns one of the 3 retry attempts, although a fresh key is available. (The Accurate run lost two 30 s waits this way.)
   - It is **not thread-safe** and Accurate mode runs 3 concurrent chunks: two threads that hit the same 429 can each call `rotate_key()` and skip a key, or the second call returns "no more keys" although a key remains.
   - A 401/403 (invalid key) is not handled by rotation: the same bad key is retried 3 times and the run fails even if other keys are valid.
4. **Layout root cause of the overlapping progress section.** `MainWindow` puts every section (video, output, description, options, two buttons, and the progress group with a 7-row stage list, bar, counter, five buttons stacked vertically, and a log up to 160 px) into one `QVBoxLayout` with `setMinimumSize(760, 680)` and **no scroll area**. When a widget already has an explicit minimum size, Qt does not apply the layout's minimum size, so the sum of the sections' minimum heights exceeds 680 px and the widgets are squeezed below their minimum size hints; the progress group then draws over its neighbors. Also, `LogView` is documented as "collapsible" but is not (no toggle).
5. **Stale-state hazard (reported by the agent itself, not fixed):** after a failed run, `state.json` still marked stages 4, 6, and 7 as done from an earlier run, and reruns skipped them silently (the first `test1_v3_accurate` copy was built from stale Fast artifacts). Resume must be dependency-aware.
6. **The UI writes `<video name>.brief.txt` into the user's video folder** when a description is entered. That can fail on a read-only folder and leaves unexpected files next to the user's videos.
7. Smaller: `run_key_test` returns `invalid_model`, but the Settings dialog does not map it (it shows "unknown"); a 400 response is always classified as an invalid key; the Accurate-mode enforcement pass failed once with invalid JSON and the raw reply was not kept for diagnosis.

---

## 1. Rules for this update

1. Change only what this file lists. Do not redesign other parts of the window or the pipeline.
2. **Never print, log, or write an API key** anywhere except the secret store in task 2. Keys must not appear in `settings.json`, logs, exceptions, screenshots, diagnostics zips, review zips, reports, the process environment, or command lines. Log key **positions** ("key #2") only, never values. Never read `.env` into output.
3. The CLI keeps working (`.env` with `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, … still supported there) and uses the same key pool code.
4. Report only what you measured. State what you cannot judge (how the UI looks and feels, whether the rotation helps against real quotas).
5. Do not commit binaries. Keep build output and media out of the review zip.
6. Produce the review zip at the end (`AGENT_UPDATE.md` 7.2, with `src/dubtool/app/`, `packaging/`, and `scripts/` in the allowlist).

---

## 2. Task 1 — Model choice as dropdowns (and make it actually work)

**UI.** In Settings, replace the two free-text fields (Fast model, Accurate model) with two **dropdowns** (non-editable `QComboBox`), one per mode, labeled like the buttons.

**Items.**
- The shipped list lives in `packaging/modes.yaml` under a new `models:` list (id and an optional short note key). Start it with the two current defaults. You may add other Gemini model ids **only if you verified them** in the official Gemini documentation or the API's model listing; do not guess names. State your source and the date in the report.
- A last item **"Khác…" / "Custom…"** reveals a one-line text field for a custom model id (model names change, and the user must never be locked out). Validate the id (non-empty, letters, digits, `.`, `-`, `_`, `/` only).
- A **"Làm mới danh sách" / "Refresh list"** button asks the API for the models available to the user's key (use the first working key) and adds the Gemini text-generation models it returns. Cache the ids in `settings.json` as `known_models` (ids only, no secrets). On errors (no key, no network, quota) keep the shipped list and show a short message; never freeze the dialog (run the request off the UI thread).
- If the saved model is not in the list (older settings, or a custom id), add it as an item and select it; never silently replace it.
- The **Reset** button restores the shipped defaults for both dropdowns.

**Make the choice effective.**
- `api.run_dub` must resolve the model with the user's settings: `modes_mod.get(options.mode, settings.load())` (or equivalent), so `settings["mode_models"]` takes effect.
- The key test (task 2.6) uses the model selected for Fast.
- The tooltip text of the two generate buttons must show the currently selected model id.

**Checks.**
- [ ] Test: changing a dropdown and saving changes the model id the pipeline hands to the provider (use a fake provider that records the model), for both modes.
- [ ] Test: a saved custom id appears in the dropdown and is used; Reset restores defaults; the refresh button adds API-reported ids to the list (fake API) and tolerates errors.
- [ ] Test: with no override, both modes still use the shipped defaults.

---

## 3. Task 2 — Multiple API keys with sequential fallback

### 2.1 Storage (`app/secrets.py`)

- Store the keys as an **ordered list in one Windows Credential Manager entry** via `keyring` (service `DubTool`, account `gemini_api_keys`, value = JSON list). Windows limits the size of a stored credential, so cap the list at **10 keys** and check the encoded size; refuse to exceed it with a clear message.
- **Migrate** the legacy single key (account `gemini_api_key`) into the list on first load, then delete the legacy entry.
- Strip whitespace and surrounding quotes, ignore empty lines, reject placeholders (the existing placeholder check), and remove duplicates while keeping order.
- Environment fallback: if the stored list is empty, read `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, … (read-only). The environment is never written by the app.
- API: `get_gemini_keys() -> list[str]`, `add_keys(list[str])`, `remove_key(index)`, `move_key(index, delta)`, `has_key()`, `key_source()`; keep `get_gemini_key()` returning the first key for compatibility. Never write keys to any file.
- If no usable keyring backend exists, say so clearly in Settings and fall back to the environment variables as before.

### 2.2 Settings UI

Make Settings a **tabbed dialog** so the new content fits and nothing is cut off: **Khóa API / API keys**, **Mô hình / Models**, **Chung / General** (language, voice, Whisper model and device), **Nâng cao / Advanced** (jobs folder, default output folder). Keep OK/Cancel at the bottom outside the tabs.

The **API keys** tab:
- A list where each row shows the position and a masked value: `Khóa 1  ••••ab12` (last 4 characters only), plus a status badge: not tested / valid / invalid / rate limited.
- The order is the priority: the app uses key 1 first, then falls back to the next ones.
- Buttons: **Thêm khóa…** (opens a small dialog with a multi-line field: one key per line, so the user can paste several at once; it is cleared after saving and the keys are never shown again), **Xóa** (selected key, with confirmation), **Lên / Xuống** (reorder), **Kiểm tra khóa đã chọn**, **Kiểm tra tất cả**.
- A show/hide toggle is not needed (keys are never displayed after saving).
- **The hint text is removed (task 3).**
- A status line that never contains a key (for example "3 khóa đã lưu").

### 2.3 Key test (`app/key_test.py`)

Test one key with one tiny request and a short timeout, off the UI thread. Outcomes: `ok`, `invalid`, `network`, `quota`, `invalid_model`, `unknown`.
- Decide `invalid` from the API's error status or reason for an invalid key, not from "any HTTP 400".
- Map **every** outcome to a message in `i18n` (including `invalid_model`, which currently falls through to "unknown").
- Use the model selected for Fast. Never include the key in any message or log.
- "Test all" tests the keys one after another and updates each row's badge.

### 2.4 Rotation engine (library, shared by the app and the CLI)

Replace the forward-only `rotate_key()` with a small thread-safe **key pool** (for example `llm/keypool.py`) used by `GeminiProvider` and `LLMClient`:

- `GeminiProvider` accepts an explicit `api_keys: list[str]` (the CLI builds it from the environment as today; the app builds it from the secret store). Do **not** use `os.environ` to hand keys to the pipeline. Remove `os.environ["GEMINI_API_KEY"] = key` from `api.run_dub`.
- Per-key state: `invalid` (bool) and `cooldown_until` (monotonic clock). Use an injectable clock so tests do not sleep.
- **Selection is sequential by priority.** For each call, use the first key in list order that is not invalid and not cooling down. When an earlier key's cooldown ends it becomes eligible again, and the earliest eligible key is preferred.
- **On a rate limit (429)** for key *i*: mark key *i* cooling down for `max(retry_after if the API gave one, llm.key_cooldown_s)` (default 60 s). If the API's error payload identifies a **per-day quota**, mark the key unavailable for the rest of this run instead. Inspect the real error structure of the `google-genai` SDK to detect this, and report what you found; if it cannot be identified, use the plain cooldown. Then **retry the same request immediately on the next available key, without sleeping and without consuming a retry attempt.**
- **Only when all keys are unavailable** wait until the earliest `cooldown_until` (at most 120 s per wait). Use the existing `on_rate_limit` callback to reduce translation concurrency **only in this case** (not on a successful switch). Emit a progress message such as "Tất cả khóa đang bị giới hạn, chờ N giây", honor the cancel event during the wait, and give up with a clear error naming how many keys were tried after `llm.max_all_keys_wait_s` (default 600 s) in total.
- **Invalid key (401/403):** mark that key invalid for the run, switch immediately, emit a warning event ("Khóa 2 không hợp lệ, đã bỏ qua"), and fail with the `invalid_key` result only if no valid key remains.
- **Thread safety:** decisions happen under a lock, and a failure is recorded against **the key index that the failed call used** (compare-and-mark), so concurrent failures of the same key cannot skip other keys. The client object may be cached per key.
- Log key positions only ("rate limit on key #2, switched to key #3"). Count switches and expose them in the result so the report and diagnostics can say how many happened.
- Config (all with defaults, in `config.yaml`): `llm.key_cooldown_s: 60`, `llm.max_all_keys_wait_s: 600`.

### 2.5 App wiring

`api.run_dub` takes the key list from `secrets.get_gemini_keys()` and passes it to the provider explicitly. `service.py` and the UI start check use `secrets.has_key()`. Deleting all keys in Settings must make the next run return `invalid_key` immediately (no leftover environment variable). Diagnostics must redact the **exact values of all stored keys** in addition to the existing patterns.

### 2.6 Tests (offline; fake provider and fake clock)

- Pool: selection follows list order; a 429 switches to the next key with **no sleep** and no consumed retry attempt; an earlier key becomes preferred again after its cooldown; all keys limited → waits until the earliest cooldown (fake clock) and then continues; per-day quota marks the key unavailable for the run; invalid key is skipped with a warning; no valid key → `invalid_key`; max total wait → clear error; cancel during a wait returns promptly.
- **Concurrency:** 3 threads hit a 429 on the same key at the same time → exactly one switch, no key skipped, all three calls succeed on the same next key.
- Storage: add several keys (including quotes, blanks, duplicates, placeholders), reorder, remove, the 10-key cap and the size check, migration from the legacy single key, env fallback is read-only.
- **No-leak test:** save 3 fake keys, run a mocked run that errors, export diagnostics, and search the whole app-data folder, logs, diagnostics zip, `settings.json`, the process environment, and captured command lines for any of the 3 values (quiet exit-code-only search). It must find none.
- UI (pytest-qt): rows are masked, multi-line add works and ignores duplicates, reorder and remove work, "Test all" updates the badges (fake tester), the tabs exist, and the hint label does not exist.
- The CLI path still passes its existing tests and uses the pool for `.env` keys.

---

## 4. Task 3 — Remove the key hint text

Delete the label `key_hint` from the Settings dialog and delete the i18n entry `settings.key.hint` in **both** languages (the Vietnamese text starting "Lấy khóa miễn phí tại Google AI Studio…" and its English counterpart). Do not replace it with other text. Update any test that referenced it, and add a test that no i18n key containing `key.hint` exists and that the Settings dialog has no label with that text.

---

## 5. Task 4 — Fix the main-window layout (progress section overlap)

**Requirements**
- Remove the hard-coded `setMinimumSize(760, 680)`. Set the initial window size from the available screen (about 90% of the available height, with sensible maximums) and remember the last size.
- Put the window content inside a `QScrollArea` (`widgetResizable=True`, vertical scrollbar as needed, no horizontal scrollbar) so that on small screens or with high DPI scaling the content scrolls instead of being squeezed.
- **Two-column layout when the window is wide enough (about 1000 px or more):** left column = video, output folder, description, options, and the two generate buttons; right column = the progress panel. Below that width, stack them in one column inside the scroll area. Switch in `resizeEvent`.
- **Progress panel:**
  - The seven stages compact, no inner scrollbar.
  - The progress bar and counter on one line.
  - **One horizontal row** for the action buttons (Cancel / Resume / Start over / Open folder / Play); show only the ones that apply.
  - A **truly collapsible log** (a toggle button with an arrow), collapsed by default, expanding to a fixed height with auto-scroll.
- Labels wrap (word wrap) so Vietnamese text is not cut off, and all widgets keep at least their `minimumSizeHint`.
- The Settings dialog (now tabbed) must fit and scroll sensibly on a 768-pixel-high screen.

**Automated layout check** (a pytest test **and** the flag `--layout-check` usable from source and from the packaged exe). Run it with real fonts (`QT_QPA_PLATFORM=windows` on Windows; the `offscreen` platform has no Vietnamese font). For window sizes 760×600, 900×700, 1100×800, 1366×700, and 1920×1000, and for states idle, video selected, running, finished, and error, assert:
1. No two visible sibling widgets in the same layout have intersecting geometries.
2. Every visible widget is at least as large as its `minimumSizeHint()`.
3. No QLabel, QPushButton, or QCheckBox text is clipped (compare the text's bounding rectangle with the widget's available rectangle, accounting for word wrap).
4. The scroll area, when the content is taller than the viewport, shows a vertical scrollbar and no horizontal one.

The check prints a table of sizes × states with pass/fail and exits non-zero on any failure. Repeat it with `QT_SCALE_FACTOR` set to 1.25, 1.5, and 1.75 in separate processes.

**Screenshots** (`--screenshots <dir>`, extended): the states above at 3 window sizes, plus one set at 150% scale and the tabbed Settings dialog (each tab). List the paths in the report. You cannot judge the visual quality; say so and let the user decide.

---

## 6. Task 5 — Fixes from the review (small but real)

1. **Dependency-aware resume.** Record per stage in `state.json` an `inputs_hash` (upstream output files' sizes and modification times plus the config values that affect that stage: model, mix, subtitle settings, voice). A stage counts as done only if its recorded hash matches the current one; otherwise rerun it. Re-running a stage (or `--force`) automatically invalidates every later stage. Tests: after rerunning stage 3, stages 4–7 execute without `--force`; an unchanged rerun skips everything; a failed run followed by resume never reuses artifacts from a different mode or model.
2. **No sidecar in the user's video folder.** Stop writing `<video>.brief.txt` there. Pass the description to the pipeline through `DubOptions`, and keep per-video descriptions in app data (for example `briefs.json` keyed by a hash of the video path). Handle write errors silently. The CLI's `<video>.brief.txt` lookup stays as it is. Test: choosing a read-only folder for the video does not break the run.
3. **Enforcement-pass diagnosis.** When the enforcement pass fails to parse the model's reply, keep the raw reply (truncated to 500 characters, no keys) in the job log. Report whether the cause was truncation, markdown fences, or something else. Do not change prompts unless the cause is clear and the fix is trivial.
4. Simplify `BriefEdit._on_change` to truncate the text to 1000 characters while preserving the cursor position, if that is trivial.

---

## 7. Checks for this update

- [ ] All previous tests plus the new ones pass; report the counts (passed, failed, skipped, and why).
- [ ] The layout check passes at every size and state, at scale 100%, 125%, 150%, 175%; paste the table.
- [ ] The no-leak search finds no key anywhere (app data, logs, diagnostics zip, settings, environment, command lines).
- [ ] Screenshot paths for every state and each Settings tab.
- [ ] The model dropdown selection is proven to change the model used (fake provider).

---

## 8. Continue to packaging (Update 4, Stage 3)

When sections 2–7 are done, build the executable as specified in `AGENT_UPDATE_4.md` sections 10–11, with these additions:

- `--layout-check` and `--screenshots` must also pass **from the packaged exe**, run from a different folder with a sanitized environment (PATH limited to Windows system folders, temporary `APPDATA` and `LOCALAPPDATA`).
- `--selftest` additionally reports: the keyring backend name, the key count (a number only), that the key pool imports, and the Settings dialog's tabs.
- `--headless-run sample/input/test1.mp4 <outdir> --mode fast` and `--mode accurate` use the keys from the keyring or the environment. Report, per run, how many key switches happened (a number, and the positions involved, never values), and whether any run waited because all keys were limited. A rate limit cannot be forced on demand; say plainly that rotation was proven by the fake-provider tests and, if no real 429 occurred, that it was not observed with the real API.
- All other checks of `AGENT_UPDATE_4.md` Stage 3 apply (ffprobe on outputs, sizes, no key in the dist folder or temp profile, the "could not verify" list).

**STOP POINT.** Final report, review zip, and wait for the user.

---

## 9. Report back

Follow `AGENT.md` section 12 and `AGENT_UPDATE.md` 7.2. Include:

1. Files added and changed; commands run; test counts.
2. For each of the four user requests: what changed and how it was verified (model dropdowns, multi-key list and rotation, hint removed, layout).
3. The layout-check table, the screenshot paths, and the no-leak result.
4. The rotation findings: how the 429 payload identifies a per-day quota (what you found in the SDK), the cooldown values in use, and the switch counts from any real runs.
5. The fixes from task 5, including what the enforcement-pass failure turned out to be.
6. Packaging results (Update 4 Stage 3 checks), sizes, versions, FFmpeg source.
7. What you could not verify, stated plainly: how the UI looks and feels on the user's display, a clean Windows machine, SmartScreen behavior, sound and translation quality, and rotation against real quotas if none occurred.
8. Anything you noticed but did not fix.
9. The review zip path and size.

Then stop and wait for the user's feedback.
