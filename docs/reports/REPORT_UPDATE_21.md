# REPORT_UPDATE_21 — DubTool `1.0.21` (Update 21 — Full Codebase Audit & Windows UIA Hardening)

## 1. Summary Table

| Area | Status | Key Verification / Evidence |
| --- | --- | --- |
| Settings, Deletion Safety & Secret Redaction | **PASS** | `src/dubtool/app/settings.py`, `src/dubtool/app/housekeeping.py`, `src/dubtool/app/cleanup.py`, `src/dubtool/app/failguard.py`, `src/dubtool/app/diagnostics.py`, `src/dubtool/app/secrets.py`, `tests/test_audit_v21_fixes.py`; fixed fresh-install `keep_level` flip (`light` -> `none`), guarded `jobs_dir` & housekeeping/cleanup with `assert_not_protected_dir()` + DubTool job markers + NTFS junction protection, unified full provider secret redaction (`AIza`, `AQ.`, `gsk_`, `sk-ant-`, `sk-`, `*_API_KEY`), UTF-16LE Credential Manager byte check & safe legacy migration |
| LLM Pool, Quota Math & Multi-Worker Concurrency | **PASS** | `src/dubtool/llm/client.py`, `src/dubtool/llm/keypool.py`, `src/dubtool/llm/quota_state.py`, `src/dubtool/llm/parsing.py`, `src/dubtool/llm/anthropic.py`, `tests/test_audit_v21_fixes.py`; fixed shadowed `KeyExhaustedError` handler, `KeyPool.wait_for_key()` infinite spin on invalid/mixed keys, 30s inflation of 3–5s Groq TPM hints, +24h `next_pacific_midnight()` drift during 07:00–15:00 ICT, thread-local `request_units`/`request_chars`/`request_label`, `_panel_text_rate_pause` NameError, strict int ID validation, and Anthropic `529`/`overloaded_error` mapping |
| Pipeline Atomicity, Resume, Performance & Subprocess Safety | **PASS** | `src/dubtool/api.py`, `src/dubtool/stages/s1_extract_audio.py`, `src/dubtool/stages/s3_translate.py`, `src/dubtool/stages/s4_tts.py`, `src/dubtool/stages/s5_fit_duration.py`, `src/dubtool/stages/s6_mix.py`, `src/dubtool/stages/s7_subtitles.py`, `src/dubtool/audio_utils.py`, `src/dubtool/subtitles.py`, `src/dubtool/proc.py`, `src/dubtool/cli.py`, `src/dubtool/config.py`, `src/dubtool/netcheck.py`; atomic `.part` writes in `s1`/`s6`/`s7`, artifact existence checks before resume skip, fixed `_completeness_pass` sync to `dub_units.json` & Groq `parse_json_items`, binary search `_words_in_range`, batched `s4` persist, `s5` cancel & adaptive shortening, and `proc.run()` timeout + stdin forwarding with cancel |
| Desktop GUI Stability & Windows UIA Accessibility | **PASS** | `src/dubtool/app/ui/settings_dialog.py`, `src/dubtool/app/ui/main_window.py`, `src/dubtool/app/ui/fillers_dialog.py`, `src/dubtool/app/voice_preview.py`, `tests/test_audit_v21_fixes.py`; fixed `SettingsDialog` `AttributeError` (`_accurate_model`), `jobs_edit` UI freeze (`editingFinished`), `closeEvent`/`DialogBridge` deadlock, deleted-`QObject` thread emissions, wrong-provider key mismatch check before `take_keys()`, `FillersDialog` input toggle, `PaidNotice` cancel persistence, deferred menu `QTimer.singleShot(0, ...)` for non-blocking Windows UIA `Invoke`, and unique UIA accessible names on options controls |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 21`, `scripts/definition_of_done.py --update 21`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
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

## 3. Detailed Changes in Update 21 (`1.0.21`)

- **Data Safety, Security & Settings Fixes:**
  - `src/dubtool/app/settings.py`: fixed `load()` so fresh installs without `keep_intermediate` preserve `keep_level="light"`, and validated `jobs_dir` against `workspace.assert_not_protected_dir()`.
  - `src/dubtool/app/housekeeping.py` & `src/dubtool/app/cleanup.py`: enforced `workspace.assert_not_protected_dir()`, restricted pruning to directories carrying DubTool job markers, added Windows NTFS junction detection (`os.path.isjunction`), and fixed `cleanup.py` `newest_dir` initialization and `_by_mtime` review-zip ordering.
  - `src/dubtool/app/failguard.py`, `src/dubtool/app/diagnostics.py`, `src/dubtool/app/ui/main_window.py`: eliminated unscrubbed `log.exception()` leakage in `failguard.py`, fixed monotonic `slot-error-*.log` indexing, extended `diagnostics.py` `_KEY_PATTERNS` to cover `sk-ant-`, `sk-`, `AQ.`, `GOOGLE_API_KEY`, `GPT_API_KEY`, `CLAUDE_API_KEY`, and redacted error-dialog clipboard copy.
  - `src/dubtool/app/secrets.py`: validated Credential Manager payloads against Windows UTF-16LE byte limits (`<= 2500` bytes), persisted migrated legacy keys before deleting legacy entries, and fixed `env_key_sources()` to list numbered `_2.._10` env vars even when the base var is unset.
- **LLM Pool, Quota & Multi-Worker Concurrency Fixes:**
  - `src/dubtool/llm/client.py`: moved `except KeyExhaustedError:` ahead of `except (LLMError, ...)`, moved `request_units`/`request_chars`/`request_label` into `threading.local()`, synchronized `rate_pause_until` & `stage_wait` with a lock, and replaced undefined `_panel_text_rate_pause` with localized `llm.rate_pause`.
  - `src/dubtool/llm/keypool.py`: failed fast in `wait_for_key()` when all keys are invalid or invalid+daily (preventing 100% CPU spin) and honored explicit short positive `retry_after` hints (e.g. Groq 3–5s TPM resets) instead of inflating them to 30s.
  - `src/dubtool/llm/quota_state.py`: included `add = 0` in `next_pacific_midnight()` (fixing +24h lockout drift during 07:00–15:00 ICT) and guarded `load()` with `threading.RLock`.
  - `src/dubtool/llm/parsing.py` & `src/dubtool/llm/anthropic.py`: validated integer IDs before sorting and rejected duplicate IDs in `parse_batch_shortened`; mapped Anthropic `529`/`504`/`overloaded_error` to `ServerErrorLLMError`.
- **Pipeline Atomicity, Resume, Performance & Process Management Fixes:**
  - `src/dubtool/stages/s1_extract_audio.py`, `src/dubtool/stages/s6_mix.py`, `src/dubtool/stages/s7_subtitles.py`, `src/dubtool/audio_utils.py`, `src/dubtool/api.py`: wrote `s1` and `s6` outputs atomically via `.part` + `replace()` with `finally:` cleanup (plus `s7` temp cleanup), verified stage artifacts exist on disk before skipping on resume, skipped `job.save()` when `keep_level == "none"`, reset `llm_budget` token in `finally:`, allowed `ollama` without cloud keys, used `job.resolve_input()` for `asr_stats`, and honored `output_name_base` when saving `.srt`.
  - `src/dubtool/stages/s3_translate.py`, `s4_tts.py`, `s5_fit_duration.py`, `subtitles.py`: fixed `_completeness_pass` to use `parse_json_items`, batch `<= 15`, and sync repairs & `uncertain` flags to `translated.json` and `dub_units.json`; switched `_words_in_range` to binary search; batched `s4` `persist()` every 20 items + `finally:` and kept `os_replace_strong` atomic; passed `cancel` + `model_override` into `s5` shortening with round-decaying `max_chars` and Groq/Auto-Rotate batch cap `15`; fixed `subtitles.py` `unit_id` fallback and `split_cue_times` deficit scaling.
  - `src/dubtool/proc.py`, `cli.py`, `config.py`, `netcheck.py`: enforced `timeout` and forwarded `input` when `cancel` is set in `proc.run()`, fixed `cmd_run` PyAV check in `cli.py`, preserved nested dict config overrides & stripped `GROQ_API_KEY` quotes in `config.py`, and recorded measured clock offset via `record_offset()` in `netcheck.py`.
- **Desktop GUI & Windows UIA Fixes:**
  - `src/dubtool/app/ui/settings_dialog.py`: fixed `AttributeError` (`_accurate_model` -> `model_id`), cached key list & quota map in `_refresh_rows()`, checked `mismatched_keys()` before `take_keys()`, switched `jobs_edit` to `editingFinished` with protected-dir guard, and guarded `_WorkResult.fire` with `shiboken6.isValid`.
  - `src/dubtool/app/ui/main_window.py`, `fillers_dialog.py`, `voice_preview.py`: deferred menu dialog openings via `QTimer.singleShot(0, ...)` so Windows UIA `Invoke` returns immediately without hanging UIA trees, added unique `objectName`/`accessibleName` to options controls and `lang_video_combo`, connected `FillersDialog` combo changes to toggle the translation input, stopped persisting `paid_notice_ok` on Cancel, moved `export_diagnostics` off the UI thread, removed 1 Hz `settings.load()` polling, pumped events in `closeEvent()` to prevent `DialogBridge` deadlock, and made voice preview WAV caching atomic with `shiboken6.isValid` emission guard.

---

## 4. Verification Summary

- **Offline test suite (`pytest tests -m "not packaged" -q --timeout=120 -rs`):** **658 passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — documented in `tests/SKIPS.md`), **0 failed**, including 10 new regression tests in `tests/test_audit_v21_fixes.py`.
- **Packaged `DubTool.exe` checks:** `--version`, `--selftest` (15/15 PASS), `--layout-check` (0 violations), `--gui-smoke` (10/10 PASS), `--screenshots` + `tests/test_screenshots.py` (3/3 PASS), and live Windows UIA verification via `agent-desktop` on the isolated packaged `DubTool.exe` confirming non-blocking Settings dialog opening and distinct UIA control names.
- **Baseline E2E reference:** preserves Update 17/18 (`test9.mp4`, offline VieNeu-TTS v3, 0% overflow, 0 TTS failures) and Update 19 (`test_china04.mp4`, 2.6-hour Chinese→Vietnamese run, 0% overflow, ~15× faster streaming S6 mix).
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 21` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
