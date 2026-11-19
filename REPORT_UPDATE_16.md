# REPORT_UPDATE_16 — DubTool 1.0.16 (`Update 16`)

## 1. Summary
| Rule | Status | Command / Check | Evidence |
| --- | --- | --- | --- |
| 1. Version consistency | **PASS** | `git rev-parse HEAD` & version check | `DOD_REPORT.md` |
| 2. Build artifacts | **PASS** | Verify exe and zip | `DOD_REPORT.md` |
| 3. Screenshots | **PASS** | Check `docs/images/INDEX.txt`, dimensions & uniqueness | `DOD_REPORT.md` |
| 4. Tests & SKIPS.md | **PASS** | `pytest -rs` & `tests/SKIPS.md` | `DOD_REPORT.md` |
| 5. Report completeness | **PASS** | `REPORT_UPDATE_16.md` requirements | `DOD_REPORT.md` |
| 6. Review package | **PASS** | `dubtool-review-*.zip` contents & scan | `DOD_REPORT.md` |
| 7. Messages (no spec IDs) | **PASS** | AST scan | `DOD_REPORT.md` |
| 8. Reports versus code | **PASS** | Existing files check | `DOD_REPORT.md` |

## 2. GitHub Evidence
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```
See `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt` for verified release outputs.

## 3. Tasks

### Task 1: Automatic Source Language Detection
- Installed `faster-whisper` 1.2.1 (`detect_language` accepts 16 kHz numpy audio, `vad_filter=True`, `language_detection_segments=3`, returns `(lang, prob, all_probs)`).
- Added "Ngôn ngữ video" dropdown in `src/dubtool/app/ui/main_window.py` and wired `video_language` through `src/dubtool/api.py` and `src/dubtool/stages/s2_transcribe.py`.
- Vietnamese detection (`prob >= 0.60`) stops before translation with a clear message.
- Expanded sentence boundary punctuation in `src/dubtool/stages/s2_transcribe.py` and skipped English-only `initial_prompt` / space-based dedupe for non-English / spaceless scripts.
- Language-aware translation prompts in `src/dubtool/stages/s3_translate.py`.
- English regression baseline: `test9.mp4`.

### Task 2: Screenshot Harness & Uniqueness
- Added state assertions before `grab()` calls in `src/dubtool/app/main.py` and ensured `progress_group` scrolls into view for stacked window sizes (`760x600`, `900x700`) so `error` and `finished` states render distinctly.
- Updated `scripts/definition_of_done.py` to reject byte-identical non-canonical images and byte-identical states.

### Task 3: Voice Failure Classification
- Updated `scripts/validate_voice_settings.py` and `src/dubtool/tts_probe.py` to classify failures by root cause (`__cause__`/`__context__`) with length-bucket counters.

### Task 4: License, Releases & Audit
- Added MIT `LICENSE`, updated `README.md`, `docs/RELEASES.md`, and `scripts/verify_release.py --audit`.
- Verified `v1.0.8` / `update-8` tags and recorded `v1.0.14` as pre-release superseded.

## 4. Quyết định đã thay người dùng
| Topic | Decision | Reason | Revert |
| --- | --- | --- | --- |
| License | MIT (`Copyright (c) 2026 sickyturtlez`) | Simple, compatible with GPL FFmpeg binary | Replace `LICENSE` |
| `v1.0.14` | Pre-release, superseded | Published from mismatched tree; history kept | Edit GitHub release |
| `agent/` folder | Stays | User instruction | Delete folder |
| Missing tags | Added `v1.0.8` and `update-8` to `262f9eb` | Exact single match for Update 8 | Remove tags |
| Source language | Default `auto`; stop on Vietnamese | Prevents dubbing Vietnamese into Vietnamese | Change `config.yaml` |
| Whisper model | Default `small`; never auto-switch | User controls speed/accuracy tradeoff | Change `config.yaml` |
| Target language | Vietnamese only | User request | Out of scope |
| Release procedure | `scripts/release.py` + `verify_release.py --audit` | Closes `gh release create` gap | Edit scripts |

## 5. Build
- Version: `1.0.16` (`packaging/version.txt`, `packaging/update_number.txt`, `src/dubtool/__init__.py`)
