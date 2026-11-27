# REPORT_UPDATE_23 — DubTool `1.0.23` (Update 23 — Whole-Unit Completeness Guard, Voice Presence +2.5 dB, Stacked Progress Auto-Scroll & GitHub Pages Landing Page)

## 1. Summary Table

| Area | Status | Key Verification / Evidence |
| --- | --- | --- |
| Whole-Unit Completeness Guard & CJK Validation (S3) | **PASS** | `src/dubtool/stages/s3_translate.py`, `src/dubtool/complete.py`, `tests/test_v23_fixes.py`; in `sentence` mode, `_completeness_pass()` now inspects and rewrites whole `dub_units` rather than proportionally sliced segments in `translated.json`, then cleanly propagates repairs via `apply_unit_translation()`, validates against untranslated CJK via `check_no_untranslated_cjk()`, and runs `normalize_vietnamese_casing()` after resume `_join_deduped()` |
| Vietnamese Voice Presence Mix Default (S6) | **PASS** | `config.yaml`, `src/dubtool/config.py`, `src/dubtool/stages/s6_mix.py`, `tests/test_v23_fixes.py`; raised default `mix.dub_gain_db` from `0` to `+2.5 dB` ahead of the `-1.0 dB` limiter so Vietnamese voice sits closer to heavily compressed web source audio while maintaining zero clipping |
| Single-Column Progress Auto-Scroll (UI) | **PASS** | `src/dubtool/app/ui/main_window.py`, `tests/test_v23_fixes.py`; clicking **Tạo** in single-column stacked layout (`< 1000 px`) schedules `MainWindow._scroll_to_progress()` to scroll `QScrollArea` directly to `progress_group` (no-op in two-column split layout) |
| Official GitHub Pages Landing Page (`taste-skill`) | **PASS** | `site/index.html`, `site/favicon.svg`, `site/share.jpg`, `site/assets/`, `.github/workflows/pages.yml`, `README.md`, `docs/README.vi.md`; built and verified with `tasteskill 2.5.1` (`copy-lint`: 0 FAIL/0 WARN, `moments check`: PASS, `layout/check.mjs` across 1440/820/768/390: 0 findings, `gate.mjs` 3-round browser verification) and automated GitHub Pages deployment to `https://namtit2932k.github.io/dubtool/` |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 23`, `scripts/definition_of_done.py --update 23`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
| `v1.0.22` | `6ac63198` | Yes | `BUILD_INFO=6ac63198, sha256=b9b37165f261…` | ok |
| `v1.0.21` | `226bc5ec` | Yes | `BUILD_INFO=226bc5ec, sha256=8682b79e053e…` | ok |
| `v1.0.20` | `6355909d` | Yes | `BUILD_INFO=6355909d, sha256=e0b397690a8d…` | ok |
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

## 3. Detailed Changes in Update 23 (`1.0.23`)

- **Whole-Unit Completeness Guard & Multilingual Prompt (`S3`):**
  - `src/dubtool/stages/s3_translate.py`: in `unit_mode == "sentence"`, `_completeness_pass()` now operates directly on whole sentence units from `dub_units.json` instead of proportionally sliced segments in `translated.json`. When a unit is genuinely incomplete and rewritten, `apply_unit_translation()` redistributes the clean sentence back down to `translated.json` segments and syncs `uncertain` flags consistently.
  - Added `llm_parsing.check_no_untranslated_cjk()` inside `_completeness_pass()` validation and applied `normalize_vietnamese_casing()` after `_join_deduped()` on resume paths.
  - `src/dubtool/complete.py`: generalized `RETRANSLATE_SYSTEM` and `RETRANSLATE_USER` from English-only wording to multilingual source-text wording while explicitly preserving narrative/period pronouns and zero CJK leakage.
- **Audio Mixing Loudness Balance (`S6`):**
  - `config.yaml`, `src/dubtool/config.py`, `src/dubtool/stages/s6_mix.py`: raised default `mix.dub_gain_db` from `0` to `2.5` dB before the `-1.0 dB` limiter.
- **Single-Column UX Polish (`UI`):**
  - `src/dubtool/app/ui/main_window.py`: added `_scroll_to_progress()` scheduled via `QTimer.singleShot(0, ...)` after `progress_group.show_running()` so users on stacked single-column windows immediately see the active Progress panel after clicking **Tạo**.
- **Official Product Landing Page (`site/` + GitHub Pages):**
  - Added static site under `site/` (`index.html`, `favicon.svg`, `share.jpg`, `assets/`) built with `tasteskill 2.5.1` (`forest-pale-lime` dark studio palette, `Instrument Sans` + `DM Mono`, `collage` hero, full-width `app-frame` 7-stage showcase, 2×2 feature cards + interactive hub diagram, `liquid-type` statement with live CPU benchmark bar, 3 API mode cards with real full-colour provider icons, full-bleed FAQ, and `close-word` footer).
  - Added `.github/workflows/pages.yml` to deploy `site/` automatically to `https://namtit2932k.github.io/dubtool/` via GitHub Actions Pages.

---

## 4. Verification Summary

- **Live E2E Field Run Baseline (`v1.0.22` motivating Update 23):** 2h33m04s 1080p Chinese period drama (`test_china07.mp4`, `2.37 GB` VP9, `1,543` sentence units) completed all 7 stages on live `DubTool.exe` `v1.0.22` in `3,674.6s` (`61m 15s`, `2.50×` faster than real-time on CPU), output `2.61 GB` (+10.2% with `CRF 21`), `0` CJK leaks, `0` overlaps, max sync shift `0.161s`, `0` clipped samples (`-19.8 dB` mean / `-1.0 dB` max), `1,464 ok / 62 stretched / 17 shortened / 0 overflow` — exposing the `54/1,543` mid-sentence `_completeness_pass` splice defect, stacked 1-column scroll UX gap, and `-8.5 dB` perceived voice loudness gap, all resolved in Update 23.
- **Baseline E2E reference:** preserves Update 17/18 (`test9.mp4`, offline VieNeu-TTS v3, 0% overflow, 0 TTS failures) and Update 19 (`test_china04.mp4`, 2.6-hour Chinese→Vietnamese run, 0% overflow, ~15× faster streaming S6 mix).
- **Offline test suite (`pytest tests -m "not packaged" -q --timeout=120 -rs`):** **672 passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — documented in `tests/SKIPS.md`), **0 failed**, including 5 new regression tests in `tests/test_v23_fixes.py`.
- **Packaged `DubTool.exe` checks:** `--version`, `--selftest` (15/15 PASS), `--layout-check` (0 violations across 1.0×/1.25×/1.5× DPI), `--gui-smoke` (10/10 PASS), `--screenshots` + `tests/test_screenshots.py` (3/3 PASS).
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 23` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
