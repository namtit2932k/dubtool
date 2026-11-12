# REVIEW_NOTES.md

## Session: Update 14 (`v1.0.14`) — verified facts only

Working rule: every item below states only directly observed code, build metadata, and command results from this session (with explicit notes for anything not run). Full details live in `REPORT_UPDATE_14.md`.

Headline: version **1.0.14**, build id `Update 14 (63c85a1)`, code/release commit `045660d0ea2830e1aa9d58287f34ad7a217c0763` (`tag: v1.0.14`), offline suite **566 passed, 9 skipped in 63.20s** (single run, no `--ignore`, `--timeout=120`).

---

## 1. Origin & Closeout Remediation

- Update 14 (`v1.0.14`) was created ad-hoc outside `agent/AGENT_UPDATE_13.md` to resolve user-reported RAM/SSD freezes during pytest/runs, frequent `60 s` `edge-tts` circuit-breaker pauses (**Option 1**), Gemini multi-key rotation fixes, local WinRT fallback hardening, and workspace deletion safety guards (`AGENTS.md`).
- The initial release commit (`045660d`), tag `v1.0.14`, and GitHub Release upload (`v1.0.14`) shipped before running `scripts/preflight_gates.py` to PASS and before writing `REPORT_UPDATE_14.md`, updating `REVIEW_NOTES.md`, or generating a review zip. This closeout resolves those gaps cleanly on `main`.

---

## 2. What Changed in Update 14 (Verified in Code)

- **`EdgeTTSProvider` & Stage 4 (`src/dubtool/tts/edge.py`, `src/dubtool/stages/s4_tts.py`, `config.yaml`, `src/dubtool/config.py`):**
  - `_ReusableTCPConnector` initialized on the running loop (`loop.call_soon`), Python 3.13 `TimeoutError` handling via `concurrent.futures.wait`, `close()` in `finally:` + `atexit.unregister`.
  - Option 1 conservative TTS settings: `concurrency: 2`, `max_rps: 1.5`, `limiter_floor_interval_s: 2.5`, `direct_retries: 1`, `attempt_delays_s: [1.5, 4]`, `breaker_consecutive: 6`, `breaker_pause_s: 15`, `breaker_pause_schedule_s: [10, 20, 30]`.
- **Workspace & Verification Safety (`src/dubtool/workspace.py`, `src/dubtool/app/main.py`):**
  - Added `assert_not_protected_dir()`, `is_sanitized_subpath()`, and `safe_rmtree_sanitized()`.
  - `--gui-smoke` enforces `_is_sanitized_env_active()` before running.
- **WinRT Fallback, LLM KeyPool & Budget/UI (`src/dubtool/tts/fallback.py`, `src/dubtool/llm/*`, `src/dubtool/app/ui/*`):**
  - `WindowsWinRtProvider` passes voice/text/output paths via Base64 UTF-8 to PowerShell.
  - Multi-key `KeyPool`, quota/budget tests, and UI updates in `045660d` (39 files changed).
- **Closeout & Gate Fixes:**
  - `scripts/preflight_gates.py`: fixed `git -c core.quotePath=false` and binary `text=False` in `blob_content()` so non-ASCII tracked image paths pass.
  - `scripts/update_readme_images.py` & `docs/images/INDEX.txt`: synced `docs/images/INDEX.txt` to `1.0.14` (`Update 14 (63c85a1)`).

---

## 3. Measured Verification (This Session)

- **Aggregate offline test suite:**
  - Command: `pytest tests -q --timeout=120` (no `--ignore`)
  - Result: **566 passed, 9 skipped in 63.20s (0:01:03)**, exit `0`.
- **Packaged exe (`D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe`) in sanitized environment:**
  - `--version`: `DubTool 1.0.14` / `build id: Update 14 (63c85a1)` (exit `0`)
  - `--selftest`: **PASS (`13/13` checks)** (exit `0`)
  - `--layout-check`: **PASS (`25/25`, `0` violations)** (exit `0`)
  - `--gui-smoke`: **PASS (`10` steps)** (exit `0`)
  - Artifacts:
    - Exe: `D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe` (`24,437,351` bytes, SHA-256 `6F17346BE137CA23C199CCAD9F727B2A6652360858BADC4356EC0E8BC6612EAA`)
    - Dist dir: `488,909,217` bytes
    - Zip: `D:\Projects\Vibe\Project_K_build\dist\DubTool-1.0.14-win64.zip` (`197,057,665` bytes, SHA-256 `DE6E3773446070C36A969E075FBB27CD05C8D25FCCCC400CBA60FD3BDE9C58F7`)
- **Screenshots & README Links:**
  - `scripts/update_readme_images.py` copied `INDEX.txt` + `ready.png`, `running.png`, `finished.png`, `settings_api_keys.png` from `screenshots/update_14_63c85a1`.
  - `scripts/check_readme_links.py`: **check_readme_links: OK** (exit `0`).
- **Pre-push gates & Secret Scans:**
  - `scripts/preflight_gates.py`: **ALL PUBLISHED-CONTENT GATES PASS** (exit `0`).
  - `scripts/scan_history_secrets.py`: **747 objects scanned, CLEAN** (exit `0`).

---

## 4. What Could Not Be Verified

- Live `--tts-probe` or full real-video run against Microsoft `edge-tts` after `045660d` (Option 1 was verified with unit/mock tests).
- Real `--fault` CLI runs on `1.0.14` (previously run on `1.0.13`).
- Live funded OpenAI or Anthropic API calls; subjective audio quality and display rendering on the user's monitor.

---

## 5. GitHub & Repository Notes

- **Release Commit (`v1.0.14`):** `045660d0ea2830e1aa9d58287f34ad7a217c0763`
- **GitHub Release:** `https://github.com/namtit2932k/dubtool/releases/tag/v1.0.14`
- **Tags (local & remote aligned):** `update-6`, `update-7`, `update-9`, `v1.0.6`, `v1.0.7`, `v1.0.9`, `v1.0.10`, `v1.0.11`, `v1.0.12`, `v1.0.13`, `v1.0.14`.
- **Untracked files:** Local `AGENTS.md` (workspace safety rules) kept untracked intentionally like `agent/*.md`.
- **License:** No `LICENSE` file chosen yet (All rights reserved; bundled FFmpeg GPL / PySide6 & edge-tts LGPL constraints unchanged).

---

## 6. Review Package

Generated by `scripts/make_review_zip.py` in `D:\Projects\Vibe\Project_K\review\`.
