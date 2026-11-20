# REPORT_UPDATE_18 — DubTool `1.0.18` (Update 18)

## 1. Summary Table

| Area | Status | Key Verification / Evidence |
| --- | --- | --- |
| Stage 3 Cancel & 503 Resilience | **PASS** | `src/dubtool/llm/client.py`, `src/dubtool/punctuate.py`, `src/dubtool/stages/s3_translate.py`, `tests/test_llm_cancel.py` (6/6 PASS); cancel aborts immediately during 503 retries and punctuate short-circuits after 2 consecutive outages |
| Stage 4 Windowed Stdio & Fail-Fast | **PASS** | `src/dubtool/app/main.py` (`_ensure_stdio`), `src/dubtool/tts/base.py` (`TTSEngineInitError`), `src/dubtool/tts/vieneu.py`, `src/dubtool/stages/s4_tts.py`; fixes `'NoneType' object has no attribute 'write'` in windowed `.exe` and fails fast on engine init errors |
| Timing Sync & Whisper Timestamp Repair | **PASS** | `src/dubtool/punctuate.py` (`repair_word_timings`), `src/dubtool/stages/s2_transcribe.py` (`vad_parameters`, `merge_short` gap guard), `src/dubtool/stages/s3_translate.py`; fixes leading orphan words and internal silences >= 1.5s |
| Gemini Model Catalog & Fallback Isolation | **PASS** | `packaging/models.yaml`, `src/dubtool/app/settings.py`, `src/dubtool/stages/s3_translate.py`; added `gemini-3.1-flash-lite`, `gemini-3-flash-preview`, `gemini-flash-lite-latest`; default primary `gemini-3.5-flash-lite`, default fallback `gemini-3.1-flash-lite`; `fast_model_id` no longer hijacks `fallback_model` |
| Desktop UI/UX, Voice Preview & Resizable Settings | **PASS** | `src/dubtool/app/ui/main_window.py`, `src/dubtool/app/ui/settings_dialog.py`, `src/dubtool/app/voice_preview.py`, `src/dubtool/app/ui/widgets.py`, `src/dubtool/app/layout_check.py`, `src/dubtool/app/i18n.py`; `--layout-check` 0 violations (including `scroll-content-overflow-x`), `--gui-smoke` 10/10 PASS |
| Branding, README, Repo Hygiene & Release | **PASS** | `README.md`, `docs/README.vi.md`, `docs/images/logo.png`, `packaging/icon.ico`, `packaging/dubtool.spec`, `docs/reports/`, `scripts/definition_of_done.py`, `scripts/update_readme_images.py`; `DOD_REPORT.md` (9/9 PASS), `RELEASE_EVIDENCE.txt`, `scripts/release.py` |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 18`, `scripts/definition_of_done.py --update 18`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
| `v1.0.17` | `6bc8192a` | Yes | `BUILD_INFO=6bc8192a, sha256=ed404ad50746…` | ok |
| `v1.0.16` | `76ba8313` | Yes | `BUILD_INFO=76ba8313, sha256=fad95de80966…` | ok |
| `v1.0.15` | `102528cd` | Yes | `BUILD_INFO=102528cd, sha256=ba9990083f37…` | ok |
| `v1.0.14` | `045660d0` | Pre-release | Mismatched tree (`63c85a18` != `045660d0`), superseded by `v1.0.15` | pre-release, superseded |
| `v1.0.13` | `63c85a18` | No | Historical | tag only |
| `v1.0.12`–`v1.0.10` | `04531dd3`–`1f25a1a9` | Yes | Pre-Update-15 release | no evidence |
| `v1.0.9`–`v1.0.6` | `ad373d84`–`1d8c6428` | No | Historical (`v1.0.8` on `262f9ebd`) | tag only |

---

## 3. Detailed Changes in Update 18 (`1.0.18`)

- **Stage 3 Cancellation & 503 Handling:**
  - `src/dubtool/llm/client.py`: checks `cancel` before and after rate-gate/backoff waits and inside retry exception handlers so cancellation raises `StageCancelled` immediately instead of firing another 503 attempt.
  - `src/dubtool/punctuate.py`: propagates `StageCancelled` without swallowing it and short-circuits remaining windows after 2 consecutive server/service failures (`503`/`504`/`429`/timeout).
  - `src/dubtool/stages/s3_translate.py`: propagates `StageCancelled` cleanly across `_prepare_sentences`, `run_chunks_parallel`, `enforce_budget`, and `_completeness_pass`; decoupled `fast_model_id` from `fallback_model` so changing the fallback model no longer alters S3's primary model; fixed `fallback_provider.model` variable name in the overload fallback notification.
- **Stage 4 Windowed Stdio & Fail-Fast:**
  - `src/dubtool/app/main.py` & `src/dubtool/tts/vieneu.py`: guaranteed non-`None` `sys.stdout`/`sys.stderr` for PyInstaller windowed (`console=False`) runs and disabled HF progress bars when no TTY is attached, fixing `AttributeError: 'NoneType' object has no attribute 'write'`.
  - `src/dubtool/tts/base.py` & `src/dubtool/stages/s4_tts.py`: introduced `TTSEngineInitError` so Stage 4 aborts immediately on engine initialization failure instead of retrying every remaining segment.
- **Timing Sync Repair:**
  - `src/dubtool/punctuate.py` (`repair_word_timings`) & `src/dubtool/stages/s2_transcribe.py`: snaps Whisper leading orphan tokens forward to the main utterance body, splits segments across internal silences `>= 1.5s`, passes `vad_parameters={"min_silence_duration_ms": 500}`, and prevents `merge_short` from merging short segments across silences `> 0.5s`.
- **Gemini Model Catalog & Defaults:**
  - `packaging/models.yaml` & `src/dubtool/app/settings.py`: verified live Google AI Studio availability; added `gemini-3.1-flash-lite`, `gemini-3-flash-preview`, and `gemini-flash-lite-latest`; lowered `translate_concurrency` to `1` for `gemini-3.6-flash` and `gemini-3.8-flash`; kept `gemini-3.5-flash-lite` as first-run primary default and set `gemini-3.1-flash-lite` as first-run fallback default.
- **Desktop UI/UX, Voice Preview & Resizable Settings:**
  - `src/dubtool/app/ui/main_window.py` & `src/dubtool/app/ui/widgets.py`: shrinkable combo boxes and dynamic minimum width eliminate horizontal clipping on narrow windows; aligned `QProgressBar` (`setTextVisible(False)`) and `counter`, aligned the bottom details buttons 50/50, cleared stale stage descriptions on stage transitions and finish (`Hoàn tất`), added immediate `"Đang hủy…"` feedback on cancel, and added the offline **Nghe thử giọng** button under the voice selector.
  - `src/dubtool/app/voice_preview.py`: background offline VieNeu-TTS preview synthesis with local WAV caching and asynchronous `winsound` playback.
  - `src/dubtool/app/ui/settings_dialog.py`: made `SettingsDialog` resizable with size grip and scroll-wrapped tabs, wrapped long labels (`WrapLongRows` + `_wrap_label`) so narrowing never clips dropdowns, added **Nghe thử giọng** under the voice selector, added default jobs-folder placeholder + Browse buttons, and grouped retention controls cleanly inside the temporary-files box.
  - `src/dubtool/app/layout_check.py`: added `scroll-content-overflow-x` check.
- **Branding, README & Repository Hygiene:**
  - Updated `README.md` and added `docs/README.vi.md` with centered hero header, white transparent logo (`docs/images/logo.png`), badges, and download CTA.
  - Updated `packaging/icon.ico` (black-background white icon for `DubTool.exe` and app window) and preserved `docs/images/logo.png` in `scripts/update_readme_images.py`.
  - Moved historical update reports (`docs/reports/REPORT_UPDATE_7.md`–`docs/reports/REPORT_UPDATE_17.md`) into `docs/reports/` and updated `scripts/definition_of_done.py` to support `docs/reports/`.

---

## 4. Verification Summary

- **Offline test suite (`pytest tests -q --timeout=120 -rs`):** **595 passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — documented in `tests/SKIPS.md`), **0 failed**, including 10 new regression tests in `tests/test_hotfix_regressions.py` and 6 in `tests/test_llm_cancel.py`.
- **Packaged `DubTool.exe` checks:** `--version`, `--selftest`, `--layout-check` (0 violations), `--gui-smoke` (10/10 PASS), and `--screenshots` verified from clean commit via `scripts/release.py 18`.
- **Baseline reference:** builds directly on Update 17's verified offline VieNeu-TTS v3 E2E pipeline (`test9.mp4` 0% overflow, 0 TTS failures) with real-world fixes validated on the 29-minute multilingual video run (`test_china02.mp4`).
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 18` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
