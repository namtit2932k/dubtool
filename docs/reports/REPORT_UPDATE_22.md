# REPORT_UPDATE_22 — DubTool `1.0.22` (Update 22 — Emerald Studio Dark UI, Synced Brand Icon & Portrait Subtitle / S3–S7 Fixes)

## 1. Summary Table

| Area | Status | Key Verification / Evidence |
| --- | --- | --- |
| Emerald Studio Dark Pro UI & Brand Icon Sync | **PASS** | `src/dubtool/app/ui/theme.py`, `src/dubtool/app/ui/widgets.py`, `src/dubtool/app/ui/main_window.py`, `src/dubtool/app/ui/settings_dialog.py`, `src/dubtool/app/ui/first_run.py`, `src/dubtool/app/ui/fillers_dialog.py`, `src/dubtool/app/ui/language_dialogs.py`, `src/dubtool/app/ui/paid_notice.py`, `src/dubtool/app/main.py`, `src/dubtool/app/layout_check.py`, `packaging/icon.ico`, `packaging/icon.png`; Obsidian/Emerald dark theme (`EmeraldProxyStyle` over Fusion with vector checkboxes/radios/chevrons), Win32 DWM dark titlebar, `StageItemDelegate` vector progress list, compact brand header strip, and synced geometric Emerald hexagon + play `.exe` icon matching `docs/images/brand/banner.svg` |
| Portrait Video Subtitles & Output Size (S7) | **PASS** | `config.yaml`, `src/dubtool/config.py`, `src/dubtool/stages/s7_subtitles.py`, `tests/test_subtitle_sizing.py`, `tests/test_v22_improvements.py`; removed hardcoded `max_line_chars: 42` in `config.yaml` so portrait videos (`height > width`) automatically activate `PORTRAIT_LINE_CHARS = 28`, capped portrait `font_px` at `width / 20`, enabled translucent dark backing box (`bg_box: true`, `BorderStyle=3`, `BackColour=&H38000000`) for legibility over existing source hardsubs, and balanced default encode quality to `crf: 21` (preventing 3× file bloat on low-bitrate web videos) |
| Translation Quality & DropZone Accessibility (S3 & UIA) | **PASS** | `src/dubtool/llm/prompts.py`, `src/dubtool/llm/groq.py`, `src/dubtool/llm/parsing.py`, `src/dubtool/app/ui/widgets.py`, `tests/test_v22_improvements.py`; added drama/period-fiction pronoun guidance to avoid forcing tutorial-style `tôi`/`bạn` on multi-character films, added `normalize_vietnamese_casing()` to capitalize sentence-initial letters after `.`/`!`/`?`, and upgraded `DropZone` to inherit `QPushButton` for native keyboard (`Tab`+`Space`/`Enter`) and Windows UIA `InvokePattern` support |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 22`, `scripts/definition_of_done.py --update 22`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
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

## 3. Detailed Changes in Update 22 (`1.0.22`)

- **Portrait Video Subtitle Fixes & Output Size Optimization (`S7`):**
  - `config.yaml` & `src/dubtool/config.py`: omitted fixed `subtitles.max_line_chars: 42` so `subtitles.default_line_chars()` actually uses `PORTRAIT_LINE_CHARS = 28` on 9:16 portrait videos and `42` on landscape videos; added `subtitles.bg_box: true` and adjusted default `subtitles.crf` from `18` to `21`.
  - `src/dubtool/stages/s7_subtitles.py`: capped portrait `font_px` to `min(height / size_ratio, width / 20.0)` so 720×1280 and 1080×1920 videos render crisp 2-line cues instead of wrapping into 4 oversized lines; added optional `bg_box` (`BorderStyle=3`, `BackColour=&H38000000`) to keep burned Vietnamese text readable over source hardsubs while preserving `bg_box=False` compatibility on `build_burn_filter()`.
- **Translation Quality Improvements (`S3`):**
  - `src/dubtool/llm/prompts.py` & `src/dubtool/llm/groq.py`: preserved tutorial/narration pronoun consistency defaults while adding explicit guidance for multi-character dramas and historical/period fiction so models use consistent role-appropriate Vietnamese pronouns instead of forcing `tôi`/`bạn`.
  - `src/dubtool/llm/parsing.py`: added `normalize_vietnamese_casing()` applied across `parse_json_array`, `parse_shortened`, and `parse_batch_shortened` to capitalize lowercase sentence starts after `.`/`!`/`?` without altering ellipses or numeric decimals.
- **Emerald Studio Dark Pro UI, DropZone UIA & Synced Brand Icon:**
  - `src/dubtool/app/ui/theme.py`: created shared Emerald Studio theme engine (`EmeraldProxyStyle`, dark `QPalette`, `EMERALD_QSS`, Win32 DWM `apply_win_dark_titlebar`, `set_button_variant`, and `render_brand_icon_pixmap`).
  - `src/dubtool/app/main.py` & `src/dubtool/app/layout_check.py`: unified theme + bundled Noto Sans font initialization (`_init_ui_theme`) across `run_gui`, `run_screenshots`, `run_layout_check`, and `run_gui_smoke`.
  - `src/dubtool/app/ui/widgets.py`: upgraded `DropZone` to `QPushButton` (enabling keyboard and Windows UIA `InvokePattern` alongside drag-and-drop `dragActive` styling), styled `FileCard` & `LogView`, and added `StageItemDelegate` rendering vector status icons and right-aligned progress badges while keeping `item.text()` intact.
  - `src/dubtool/app/ui/main_window.py`, `settings_dialog.py`, `first_run.py`, `fillers_dialog.py`, `language_dialogs.py`, `paid_notice.py`: added compact `brand_header` strip with synced logo & version pill, button hierarchy (`primary`/`danger`/`ghost`), dark titlebars, and readable dark-surface colors on all dialogs and banners.
  - `packaging/icon.ico` & `packaging/icon.png`: regenerated multi-resolution Windows icon (`16..256 px`) matching the geometric Emerald hexagon + play emblem in `docs/images/brand/banner.svg`.

---

## 4. Verification Summary

- **Live E2E Field Run Baseline (`v1.0.21` motivating Update 22):** 29:15 portrait Chinese drama (`720×1280`, 264 units) completed 7/7 stages in `749.8s` (`s3=75.5s`, `s4=268.0s` 0 failures, `s5=23.1s` `255 ok / 8 stretched / 1 shortened / 0 overflow`, `s6=26.7s`, max sync shift `0.153s`, 0 CJK leaks), directly exposing the portrait subtitle wrap/font bug, CRF 18 bloat, drama pronoun bias, sentence casing misses, and `DropZone` UIA no-op — all fixed and verified in Update 22.
- **Baseline E2E reference:** preserves Update 17/18 (`test9.mp4`, offline VieNeu-TTS v3, 0% overflow, 0 TTS failures) and Update 19 (`test_china04.mp4`, 2.6-hour Chinese→Vietnamese run, 0% overflow, ~15× faster streaming S6 mix).
- **Offline test suite (`pytest tests -m "not packaged" -q --timeout=120 -rs`):** **664 passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — documented in `tests/SKIPS.md`), **0 failed**, including 5 new regression tests in `tests/test_v22_improvements.py` and updated portrait assertions in `tests/test_subtitle_sizing.py`.
- **Packaged `DubTool.exe` checks:** `--version`, `--selftest` (15/15 PASS), `--layout-check` (0 violations across 1.0×/1.25×/1.5× DPI), `--gui-smoke` (10/10 PASS), `--screenshots` + `tests/test_screenshots.py` (3/3 PASS), and visual verification of both the Emerald Studio UI screenshots and rendered `720×1280` portrait subtitle frames.
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 22` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
