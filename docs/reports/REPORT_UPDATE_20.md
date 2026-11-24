# REPORT_UPDATE_20 — DubTool `1.0.20` (Update 20 — Hotfix & Hardened Build)

## 1. Summary Table

| Area | Status | Key Verification / Evidence |
| --- | --- | --- |
| Gemini API Key Verification Fix (`auto-rotate`) | **PASS** | `src/dubtool/app/key_test.py`, `src/dubtool/app/ui/settings_dialog.py`, `tests/test_app_ui.py`; switched Gemini key test to free `client.models.list(config={"page_size": 1})` and guarded `_selected_model_id()` against non-Gemini ids (`auto-rotate`, `qwen/...`, `gpt-...`, `claude-...`), eliminating false `✘ không hợp lệ` (`404 NOT_FOUND`) on valid Gemini keys |
| Settings Dialog Cross-Provider Badge Isolation | **PASS** | `src/dubtool/app/ui/settings_dialog.py`, `tests/test_app_ui.py`; removed legacy bare-integer `self._badges[row]` fallback, tagged async test completions with `tested_provider`, and scoped badge clearing per provider so Gemini verdicts never leak onto Groq/OpenAI/Anthropic |
| Stage 3 Targeted CJK Repair & Slot Concurrency Spread | **PASS** | `src/dubtool/llm/parsing.py`, `src/dubtool/llm/client.py`, `src/dubtool/llm/auto_rotate.py`, `tests/test_s3_optimizations.py`, `tests/test_auto_rotate.py`; added `find_untranslated_cjk()` + `_repair_untranslated_cjk()` to repair only the 1–8 leaked item(s) while preserving clean batch translations, plus per-slot `_in_flight` tracking so concurrent workers pick distinct idle slots |
| Pinned Vendor Assets (FFmpeg 8.1.2 GPL-3.0 + Noto Sans) & Build Provenance | **PASS** | `packaging/vendor_manifest.json`, `scripts/fetch_vendor.py`, `packaging/build_exe.ps1`, `packaging/requirements-lock.txt`, `packaging/THIRD_PARTY.md`, `packaging/RELEASE_NOTES_TEMPLATE.md`, `tests/test_dependencies_sync.py`; pinned FFmpeg 8.1.2 GPL-3.0 (`essentials_build`), strict Python 3.13 x64 validation, full `BUILD_INFO.txt` provenance, and 15/15 `--selftest` |
| Documentation, Architecture & Localization Refactor | **PASS** | `src/dubtool/app/i18n/` (`__init__.py`, `vi.py`, `en.py`), `docs/ARCHITECTURE.md`, `README.md`, `docs/README.vi.md`, `docs/images/brand/banner.svg`; modularized 284 VI/EN strings, added architecture guide, and polished bilingual README |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 20`, `scripts/definition_of_done.py --update 20`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
| `v1.0.19` | `e04784a7` | Yes | `BUILD_INFO=e04784a7, sha256=c262fdfe95ff…` | ok |
| `v1.0.18` | `a9c4680a` | Yes | `BUILD_INFO=a9c4680a, sha256=387c4b58777c…` | ok |
| `v1.0.17` | `6bc8192a` | Yes | `BUILD_INFO=6bc8192a, sha256=ed404ad50746…` | ok |
| `v1.0.16` | `76ba8313` | Yes | `BUILD_INFO=76ba8313, sha256=fad95de80966…` | ok |
| `v1.0.15` | `102528cd` | Yes | `BUILD_INFO=102528cd, sha256=ba9990083f37…` | ok |
| `v1.0.14` | `045660d0` | Pre-release | Mismatched tree (`63c85a18` != `045660d0`), superseded by `v1.0.15` | pre-release, superseded |
| `v1.0.13` | `63c85a18` | No | Historical | tag only |
| `v1.0.12`–`v1.0.10` | `04531dd3`–`1f25a1a9` | Yes | Pre-Update-15 release | no evidence |
| `v1.0.9`–`v1.0.6` | `ad373d84`–`1d8c6428` | No | Historical (`v1.0.8` on `262f9ebd`) | tag only |

---

## 3. Detailed Changes in Update 20 (`1.0.20`)

- **Production Hotfixes for `v1.0.19` Field Issues:**
  - **Gemini key check false `✘ không hợp lệ`:** `src/dubtool/app/key_test.py` now validates Gemini keys using the free `client.models.list(config={"page_size": 1})` endpoint (zero tokens, immune to non-Gemini `model_id` such as `"auto-rotate"` and transient `503` on a single Flash-Lite model), and `SettingsDialog._selected_model_id()` falls back to `"gemini-3.5-flash-lite"` whenever `model_id` is non-Gemini.
  - **Cross-provider badge leak:** `src/dubtool/app/ui/settings_dialog.py` now stores and reads key-test badges exclusively by `(provider, row)`, clears badges per provider via `_clear_provider_badges()`, attaches `tested_provider` to async test results, and cleans up `"__testing__"` states on unexpected errors.
  - **Stage 3 targeted CJK repair:** `src/dubtool/llm/parsing.py` exposes `find_untranslated_cjk(items, glossary)` and `src/dubtool/llm/client.py` adds `_repair_untranslated_cjk()` in `translate_batch()` so when a batch has valid JSON with only 1–8 leaked Hanzi/Kana/Hangul items (e.g. classical xianxia terms on Groq `qwen/qwen3.8-27b`), all clean items are preserved and only the leaked item(s) are repaired in a focused call instead of aborting Stage 3.
  - **Auto-Rotate concurrent slot spread:** `src/dubtool/llm/auto_rotate.py` tracks `_in_flight` counts per slot and prioritizes idle slots (`in_flight == 0`) in `_pick_from_tier()`, preventing parallel workers from firing simultaneous bursts onto the same Groq key.
- **Reproducible Packaging, CI Hardening & Pinned FFmpeg 8.1.2 GPL-3.0:**
  - Pinned FFmpeg **8.1.2 GPL-3.0 (`essentials_build`)** and **Noto Sans (`NotoSans.ttf` + `OFL.txt`)** with SHA-256 verification in `packaging/vendor_manifest.json` + `scripts/fetch_vendor.py`, updated `packaging/THIRD_PARTY.md`, `packaging/licenses/THIRD_PARTY_NOTICES.txt`, and `packaging/RELEASE_NOTES_TEMPLATE.md`.
  - Standardized `pyproject.toml` extras + `packaging/requirements-lock.txt` (`pip==26.3`), strict Python 3.13 x64 validation and full build provenance in `BUILD_INFO.txt`, plus extracted-ZIP smoke verification in GitHub Actions (`Windows · Python 3.11`, `Windows · Python 3.13`, `Windows · Python 3.13 · Release Build Verify`).
  - Split `src/dubtool/app/i18n.py` into `src/dubtool/app/i18n/{__init__,vi,en}.py`, added `docs/ARCHITECTURE.md`, and refreshed `README.md` + `docs/README.vi.md` with custom `docs/images/brand/banner.svg`.
  - Updated `scripts/release.py` Step 2 to run `pytest tests -m "not packaged"` before Step 4/5 builds the fresh `.exe` and runs `pytest tests/test_screenshots.py`.

---

## 4. Verification Summary

- **Offline test suite (`pytest tests -m "not packaged" -q --timeout=120 -rs`):** **648 passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — documented in `tests/SKIPS.md`), **0 failed**, including 4 new regression tests covering Gemini `auto-rotate` key verification, cross-provider badge isolation, targeted S3 CJK repair, and concurrent Auto-Rotate slot spread.
- **Packaged `DubTool.exe` checks:** `--version`, `--selftest` (15/15 PASS), `--layout-check` (0 violations), `--gui-smoke` (10/10 PASS), and `--screenshots` + `tests/test_screenshots.py` (3/3 PASS) verified from clean commit via `scripts/release.py 20`.
- **Baseline E2E reference:** preserves Update 17/18 (`test9.mp4`, offline VieNeu-TTS v3, 0% overflow, 0 TTS failures) and Update 19 (`test_china04.mp4`, 2.6-hour Chinese→Vietnamese run, 0% overflow, ~15× faster streaming S6 mix) while resolving the real-world `v1.0.19` field bugs reported on `auto-rotate` / Groq / Settings.
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 20` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
