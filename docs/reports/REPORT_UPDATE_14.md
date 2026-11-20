# REPORT_UPDATE_14.md — Implementation, Resilience & Closeout Report for Update 14

**Version:** 1.0.14  
**Build ID:** Update 14 (`63c85a1` source snapshot + `045660d` release commit)  
**Date:** 2026-11-12  
**Test Suite:** `566 passed, 9 skipped in 63.20s` (single run, no `--ignore`, `--timeout=120`)

---

## 0. Scope, Origin & Process Compliance Note

Update 14 (`v1.0.14`) was **not** specified in `agent/AGENT_UPDATE_13.md`. It originated from follow-up user requests in this session after Update 13:
1. Mitigating RAM/SSD pressure and pytest hangs caused by persistent background threads/loop shutdown and concurrent subprocess/test behavior.
2. Implementing **Option 1** for `edge-tts` stability in Stage 4 to eliminate frequent flat `60 s` circuit-breaker pauses (`breaker_pause_s` / `breaker_pause_schedule_s`), conservative worker pacing, direct transient retries, and clean resource teardown.
3. Hardening workspace deletion guards (`AGENTS.md` rules via `dubtool.workspace.safe_rmtree_sanitized()` and `assert_not_protected_dir()`), Gemini multi-key rotation, and local Windows WinRT fallback robustness.
4. Shipping `v1.0.14` to GitHub.

**Process gap corrected in this closeout:**  
The initial code push (`045660d`), tag `v1.0.14`, and GitHub Release asset upload were performed before fixing `scripts/preflight_gates.py` (which failed with exit code `1` on C-quoted UTF-8 paths in `docs/images/`) and without writing `REPORT_UPDATE_14.md`, updating `REVIEW_NOTES.md`, syncing `docs/images/INDEX.txt`, or generating an Update 14 review zip (`scripts/make_review_zip.py`). This closeout completes those required review/gate/logging deliverables cleanly without rewriting Git history or force-moving tags.

---

## 1. Files Added, Changed, Commands Run, and Aggregate Test Counts

### 1.1 Changes Shipped in Release Commit `045660d` (39 files: `+2363 / -470`)
- **TTS & Edge-TTS Option 1 (`src/dubtool/tts/edge.py`, `src/dubtool/stages/s4_tts.py`, `config.yaml`, `src/dubtool/config.py`, `src/dubtool/tts_failures.py`, `src/dubtool/tts_probe.py`, `src/dubtool/tts/fallback.py`):**
  - `EdgeTTSProvider` (`src/dubtool/tts/edge.py`): persistent background asyncio loop, `_ReusableTCPConnector` initialized on the running loop via `loop.call_soon`, Python 3.13 `concurrent.futures.wait` timeout handling, `close()` + `atexit.unregister`.
  - Stage 4 adaptive throttling & repair ladder (`src/dubtool/stages/s4_tts.py`, `config.yaml`, `src/dubtool/config.py`): `concurrency: 2`, `max_rps: 1.5`, `limiter_floor_interval_s: 2.5`, `direct_retries: 1`, `attempt_delays_s: [1.5, 4]`, `breaker_consecutive: 6`, `breaker_pause_s: 15`, `breaker_pause_schedule_s: [10, 20, 30]`, explicit `provider.close()` in `finally:`.
  - Local fallback (`src/dubtool/tts/fallback.py`): `WindowsWinRtProvider` and `detect_winrt_voices()` encoding text/voice arguments via Base64 UTF-8 to avoid Windows codepage mangling.
- **Workspace Safety & App/LLM Resilience (`src/dubtool/workspace.py`, `src/dubtool/job.py`, `src/dubtool/app/main.py`, `src/dubtool/llm/*`, `src/dubtool/stages/s3_translate.py`, `src/dubtool/api.py`, `src/dubtool/app/ui/*`):**
  - `src/dubtool/workspace.py`: added `assert_not_protected_dir()`, `is_sanitized_subpath()`, and `safe_rmtree_sanitized()` restricting recursive smoke cleanup strictly to `.../tmp/sanitized/<run_id>`.
  - `src/dubtool/app/main.py`: `--gui-smoke` enforces sanitized home guards before running.
  - Multi-key `KeyPool`, quota/budget handling, audio utils, and UI updates across `llm/`, `s3_translate.py`, `api.py`, `app/ui/`, `app/i18n.py`.
- **Versioning, Packaging & Tests:**
  - `packaging/update_number.txt` (`14`), `src/dubtool/__init__.py` (`__version__ = "1.0.14"`), `packaging/build_exe.ps1`, `packaging/dubtool.spec`, `docs/WORKSPACE.md`, `docs/images/{ready,running,finished}.png`.
  - Tests added/updated: `tests/test_agu11_tts.py`, `tests/test_agu12_llm.py`, `tests/test_agu12_tts.py`, `tests/test_agu13.py`, `tests/test_app_ui.py`, `tests/test_llm_budget_quota.py`, `tests/test_workspace.py`.

### 1.2 Closeout & Pre-Push Gate Fixes (Follow-up Docs Commit)
- `scripts/preflight_gates.py`: passed `-c core.quotePath=false` to `git ls-files` and `git show`, and read blobs with `text=False` so UTF-8 paths (such as `docs/images/dialog_settings_tab0_Khóa_API.png`) and binary assets are inspected accurately without crashing.
- `scripts/update_readme_images.py`: now also copies `INDEX.txt` (when present in the screenshot folder) to `docs/images/INDEX.txt`.
- `docs/images/INDEX.txt`: updated from stale `1.0.11` source metadata to `1.0.14` (`Update 14 (63c85a1)`) matching the `DubTool.exe --screenshots` capture.
- `REPORT_UPDATE_14.md` (this report) and `REVIEW_NOTES.md` (rewritten fresh for Update 14).

### 1.3 Commands Run & Observed Outcomes
```powershell
# 1. Full offline test suite (single run, no --ignore, bounded per-test timeout)
D:\Projects\Vibe\Project_K_build\venv-build\Scripts\python.exe -m pytest tests -q --timeout=120
# Result: 566 passed, 9 skipped in 63.20s (0:01:03), exit 0

# 2. README screenshot metadata sync + link check
D:\Projects\Vibe\Project_K_build\venv-build\Scripts\python.exe scripts/update_readme_images.py D:\Projects\Vibe\Project_K_build\screenshots\update_14_63c85a1
D:\Projects\Vibe\Project_K_build\venv-build\Scripts\python.exe scripts/check_readme_links.py
# Result: check_readme_links: OK, exit 0

# 3. Working-tree version check
D:\Projects\Vibe\Project_K_build\venv-build\Scripts\python.exe -m dubtool.app.main --version
# Result: DubTool 1.0.14 / ffmpeg version N-118281-gb76053d8bf-20250111, exit 0

# 4. Pre-push gates + full-history secret scan
D:\Projects\Vibe\Project_K_build\venv-build\Scripts\python.exe scripts/preflight_gates.py
D:\Projects\Vibe\Project_K_build\venv-build\Scripts\python.exe scripts/scan_history_secrets.py
```

---

## 2. Packaged Executable Build & Sanitized Verification

- **Executable Path:** `D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe`
- **Executable Size:** `24,437,351` bytes
- **Executable SHA-256:** `6F17346BE137CA23C199CCAD9F727B2A6652360858BADC4356EC0E8BC6612EAA`
- **Distribution Folder Size:** `488,909,217` bytes
- **Release Zip Path:** `D:\Projects\Vibe\Project_K_build\dist\DubTool-1.0.14-win64.zip`
- **Release Zip Size:** `197,057,665` bytes
- **Release Zip SHA-256:** `DE6E3773446070C36A969E075FBB27CD05C8D25FCCCC400CBA60FD3BDE9C58F7`
- **`BUILD_INFO.txt`:** `update: 14`, `version: 1.0.14`, `git: 63c85a1`, `built (UTC): 2026-11-12 13:12:40`, `build id: Update 14 (63c85a1)`

**Sanitized environment exe verification** (`PATH` restricted to Windows system folders; isolated temp `APPDATA`, `LOCALAPPDATA`, `USERPROFILE`, `HOME`):
- `DubTool.exe --version` → `DubTool 1.0.14` / `build id: Update 14 (63c85a1)` (exit `0`)
- `DubTool.exe --selftest --json ...` → `overall: PASS`, `13/13` checks `PASS` (`ffmpeg`, `filters_encoders`, `font_vietnamese`, `keyring` (`WinVaultKeyring`), `keys_and_tabs`, `dirs_writable`, `imports`, `av_decode_compat`, `qt_offscreen`, `gemini_client`, `openai_client`, `anthropic_client`, `docx_roundtrip`) (exit `0`)
- `DubTool.exe --layout-check` → `total violations: 0` across 25 checks (`760x600`, `900x700`, `1100x800`, `1366x700`, `1920x1000` × `idle`, `video`, `running`, `finished`, `error`) (exit `0`)
- `DubTool.exe --gui-smoke` → `gui-smoke: PASS (10 steps)` (exit `0`)

---

## 3. Pre-Push Gates, Secret Scans & GitHub Status

- **`scripts/preflight_gates.py`:** `ALL PUBLISHED-CONTENT GATES PASS` (`exit 0`, 235+ tracked files under `50 MB`, forbidden-path check `PASS`, secret scan `PASS`, README link check `PASS`).
- **`scripts/scan_history_secrets.py`:** `scanned objects: 747`, `history secret scan: CLEAN` (`exit 0`).
- **Pre-commit hook:** `.git/hooks/pre-commit` routes commits through `scripts/git_guard.py`.
- **Untracked files note:** Root `AGENTS.md` holds local workspace agent safety rules (`--gui-smoke`, `safe_rmtree_sanitized()`, no silent system/installer edits, transcript check) and is intentionally left untracked (consistent with `agent/*.md` staying untracked per `AGENT_UPDATE_6.md` §8.1).
- **License status:** Unchanged from Update 13 (`All rights reserved` by default; no `LICENSE` file added).

### GitHub Release & Tag State
- **Release code commit (`v1.0.14`):** `045660d0ea2830e1aa9d58287f34ad7a217c0763`
- **GitHub Release URL:** `https://github.com/namtit2932k/dubtool/releases/tag/v1.0.14` (asset `DubTool-1.0.14-win64.zip` uploaded)
- **Tags (`git tag -l` / `git ls-remote --tags origin`):** `update-6`, `update-7`, `update-9`, `v1.0.6`, `v1.0.7`, `v1.0.9`, `v1.0.10`, `v1.0.11`, `v1.0.12`, `v1.0.13`, `v1.0.14` (`v1.0.14` points to `045660d0ea2830e1aa9d58287f34ad7a217c0763`; left unmoved per the no-history-rewrite / no-force rule).

---

## 4. What Could Not Be Verified & Items Noted

### Could Not Be Verified in Update 14
1. **Live `--tts-probe` or full real-video run after Option 1 changes (`045660d`):** Stage 4 pacing, graduated breaker pauses, and `EdgeTTSProvider` timeout/teardown were verified via unit tests (`566 passed, 9 skipped`); live Microsoft `edge-tts` endpoint acceptance under the new defaults on a full video was not re-measured in this update.
2. **Real fault-injection runs on the `1.0.14` exe:** The five `--fault` CLI runs were executed on `1.0.13` and not re-run on `1.0.14`.
3. **Paid OpenAI / live Anthropic runs & subjective dub quality:** Funded OpenAI tokens, live Anthropic keys, subjective dubbing audio aesthetics, and visual rendering on the user's monitor were not verified here.

### Items Noted (Not bugs or deferred decisions)
- **`python -m dubtool --version` vs `-v`:** Root CLI `dubtool` reserves `-v` as a global flag expecting a subcommand; GUI/app version entry point uses `python -m dubtool.app.main --version` (or `DubTool.exe --version`).
- **`BUILD_INFO.txt` commit stamp:** Because `build_exe.ps1` ran against the working tree while `HEAD` was `63c85a1` (before committing `045660d`), `BUILD_INFO.txt` and `docs/images/INDEX.txt` display `Update 14 (63c85a1)` for `version: 1.0.14`. All Python/UI files included in the zip match the `045660d` source code.

---

## 5. Review Package

Produced via `scripts/make_review_zip.py` after closeout and placed in `D:\Projects\Vibe\Project_K\review\`.
