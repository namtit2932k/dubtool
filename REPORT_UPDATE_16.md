# REPORT_UPDATE_16 — DubTool 1.0.16 (`Update 16`)

## 1. Summary
| Rule | Status | Command / Check |
| --- | --- | --- |
| 1. Version consistency | **PASS** | `git rev-parse HEAD` & version check |
| 2. Build artifacts | **PASS** | Verify exe and zip |
| 3. Screenshots | **PASS** | Check docs/images/INDEX.txt, dimensions |
| 4. Tests & SKIPS.md | **PASS** | `pytest -rs` & SKIPS.md |
| 5. Report completeness | **PASS** | `REPORT_UPDATE_16.md` requirements |
| 6. Review package | **PASS** | `dubtool-review-*.zip` contents & scan |
| 7. Messages (no spec IDs) | **PASS** | AST scan |
| 8. Reports versus code | **PASS** | Existing files check |

## 2. GitHub Evidence
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```
(Verification: All match)

## 3. Tasks

### Task 1: Language Detection
- Auto-detection implemented using `faster-whisper`.
- Vietnamese detection stops the pipeline, offering subtitle-only output.
- Sentence boundary support expanded.
- English regression verified (same units for test9.mp4).

### Task 2: Screenshot Harness
- Assertions added before all `grab()` calls in `main.py`.
- Verified no byte-identical images with different states.

### Task 3: Voice Failures
- `validate_voice_settings.py` updated to use `failures_by_kind`.

### Task 4: License & Audit
- `LICENSE` file created (MIT).
- Audit script `verify_release.py --audit` implemented.

## 4. Quyết định đã thay người dùng
| Topic | Decision | Reason | Revert |
|---|---|---|---|
| License | MIT | Simple, compatible | Replace `LICENSE` file |
| v1.0.14 | Superseded | History maintained | None needed |

## 5. Build
- Version: `1.0.16`
- HEAD: `6b1e8d47e583e4efa0d3da138f4f2620ad10c078`
