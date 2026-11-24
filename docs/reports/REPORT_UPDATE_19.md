# REPORT_UPDATE_19 — DubTool `1.0.19` (Update 19)

## 1. Summary Table

| Area | Status | Key Verification / Evidence |
| --- | --- | --- |
| Auto-Rotate Free-Tier Multi-Key Pool (`auto-rotate`) | **PASS** | `src/dubtool/llm/auto_rotate.py`, `packaging/models.yaml`, `src/dubtool/llm/catalog.py`, `src/dubtool/app/settings.py`, `tests/test_auto_rotate.py` (13/13 PASS); default first-run model; round-robins all saved Gemini Flash-Lite + Groq `qwen/qwen3.8-27b` keys, adaptive 503 cooldown (`15s → 30s → 45s`), 20-unit CJK-capped chunks, 3 workers when `>= 4` free keys, never touches paid providers |
| Groq Cloud Free-Tier Provider | **PASS** | `src/dubtool/llm/groq.py`, `src/dubtool/app/secrets.py`, `src/dubtool/app/key_test.py`, `tests/test_groq_provider.py` (10/10 PASS); live-verified models (`qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`), -67% compact prompt, `384..950` OTPM cap, `KeyPool` multi-key rotation, cross-provider fallback, `Used > 0` 429 classification |
| Stage 3 Translation Speed, Resilience & Quality | **PASS** | `src/dubtool/stages/s3_translate.py`, `src/dubtool/llm/gemini.py`, `src/dubtool/llm/client.py`, `src/dubtool/llm/parsing.py`, `src/dubtool/llm/prompts.py`, `tests/test_s3_optimizations.py` (10/10 PASS); `punctuate.enabled: off` default, Gemini `response_json_schema`, `MAX_TOKENS` detection + truncated-JSON split, infinite self-fallback loop eliminated, CJK leak rejection (`check_no_untranslated_cjk`), Brief pronoun priority on both Gemini & Groq |
| Stage 6 Streaming Mixer & Explorer Reveal Fix | **PASS** | `src/dubtool/audio_utils.py`, `src/dubtool/stages/s6_mix.py`, `src/dubtool/app/ui/main_window.py`, `tests/test_s6_and_explorer_fixes.py` (5/5 PASS); 30-second streaming `soundfile` + `numpy` mixer (~20 MB constant RAM, no `_silence.wav`, RF64 support, ~15× faster on 2.6h video); Win32 `SHOpenFolderAndSelectItems` fixes Explorer reveal for paths containing spaces |
| Stage 2 Cancel & Cross-Model S1/S2 Reuse | **PASS** | `src/dubtool/stages/s2_transcribe.py`, `src/dubtool/api.py`; wired `cancel` into Whisper collection loop and removed `provider:model` from `_stable_job_id` so switching translation models reuses S1/S2 |
| Settings UI Polish & Release | **PASS** | `src/dubtool/app/ui/settings_dialog.py`, `tests/test_hotfix_regressions.py`; deduplicated `.env` rows already in Credential Manager, `_WrapLabel` dynamic `heightForWidth` eliminates clipping on narrow/high-DPI resize, aligned job-retention spinboxes; `--layout-check` 0 violations, `--gui-smoke` 10/10 PASS |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 19`, `scripts/definition_of_done.py --update 19`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
| `v1.0.18` | `a9c4680a` | Yes | `BUILD_INFO=a9c4680a, sha256=387c4b58777c…` | ok |
| `v1.0.17` | `6bc8192a` | Yes | `BUILD_INFO=6bc8192a, sha256=ed404ad50746…` | ok |
| `v1.0.16` | `76ba8313` | Yes | `BUILD_INFO=76ba8313, sha256=fad95de80966…` | ok |
| `v1.0.15` | `102528cd` | Yes | `BUILD_INFO=102528cd, sha256=ba9990083f37…` | ok |
| `v1.0.14` | `045660d0` | Pre-release | Mismatched tree (`63c85a18` != `045660d0`), superseded by `v1.0.15` | pre-release, superseded |
| `v1.0.13` | `63c85a18` | No | Historical | tag only |
| `v1.0.12`–`v1.0.10` | `04531dd3`–`1f25a1a9` | Yes | Pre-Update-15 release | no evidence |
| `v1.0.9`–`v1.0.6` | `ad373d84`–`1d8c6428` | No | Historical (`v1.0.8` on `262f9ebd`) | tag only |

---

## 3. Detailed Changes in Update 19 (`1.0.19`)

- **Auto-Rotate Free-Tier Multi-Key Pool (`auto-rotate`, Default Model):**
  - `src/dubtool/llm/auto_rotate.py`: combines all saved Gemini Flash-Lite (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-flash-lite-latest`) and Groq (`qwen/qwen3.8-27b`) keys into an interleaved round-robin pool. Never accesses paid providers (OpenAI / Anthropic).
  - Adaptive 503 model cooldown (`15s → 30s → 45s`, resetting on success), instant 0ms failover across keys/providers, Groq length-overflow handoff to ready Gemini slots, deduplicated wait heartbeats (`src/dubtool/heartbeat.py`), and immunity against single-model downgrade in `s3_translate.py`.
  - Dynamic throughput scaling: 20-unit chunks (capped at 450 CJK / 1,000 Latin chars when Groq is in pool) and 3 parallel workers when `>= 4` free keys are configured (`2` workers otherwise).
  - Set `DEFAULT_MODEL_ID = "auto-rotate"` in `src/dubtool/llm/catalog.py` and `src/dubtool/app/settings.py`.
- **Groq Cloud Integration (`src/dubtool/llm/groq.py`):**
  - Reuses the bundled `openai` SDK against `https://api.groq.com/openai/v1` (0 MB binary growth), supporting live-verified free models (`qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`), `gsk_` prefix detection/redaction, `GET /openai/v1/models` key test, and cross-provider overload fallback.
  - Compacts system + user prompts (-67% input tokens: `1,277 → 422`), clamps `max_tokens` to `384..950` for Groq Free Tier's 1,000 OTPM cap, rotates multiple Groq keys via `KeyPool`, and distinguishes `Used > 0` per-minute 429s from single-request `RequestTooLargeError`.
- **Stage 3 Resilience & Translation Quality:**
  - Disabled pre-translation LLM `punctuate` by default (`punctuate.enabled: off`), preserving local zero-latency `repair_word_timings` + `resegment_by_sentences`.
  - Enabled `response_json_schema` on `GeminiProvider`, mapped HTTP 400 schema rejections to `SchemaRejectedError`, detected `finish_reason=MAX_TOKENS` → `RequestTooLargeError`, auto-split truncated JSON replies in `LLMClient`, fixed infinite self-fallback loops (`tried_models` + `switch_lock`), and preserved long model names in `_redact()`.
  - Added `parsing.check_no_untranslated_cjk()` across `translate_batch`, `shorten`, and `shorten_batch` to catch and retry any leaked Hanzi/Kana/Hangul characters (respecting `"keep original"` glossary terms), deferred Groq compact-prompt pronouns to the user's `Brief`, and tightened spoken-dubbing conciseness guidance.
- **Stage 6 Streaming Mixer & Explorer Reveal:**
  - Replaced `make_silence` + `pydub.AudioSegment.overlay` in `src/dubtool/audio_utils.py` and `src/dubtool/stages/s6_mix.py` with a 30-second streaming `soundfile` + `numpy` block mixer (~20 MB RAM, eliminates `_silence.wav`, auto-switches to RF64 above 3.8 GB, cancels cleanly).
  - Replaced `explorer /select,...` with 64-bit Win32 `SHOpenFolderAndSelectItems` in `src/dubtool/app/ui/main_window.py` to fix paths containing spaces opening `OneDrive\Documents`.
- **Stage 2 Cancel, S1/S2 Reuse & Settings UI Fixes:**
  - Wired `cancel` into `s2_transcribe.run()` / `_collect()` so Cancel stops Whisper immediately after the active segment; removed `provider:model` from `api._stable_job_id()` so changing translation models reuses S1/S2.
  - Fixed 3 `SettingsDialog` UI issues: deduplicated `.env` rows already stored in Credential Manager, introduced `_WrapLabel` (`heightForWidth` on `resizeEvent`) inside a `QGridLayout` to prevent vertical clipping at narrow/high-DPI widths, and aligned both job-retention `QSpinBox` controls to identical width and right edges.

---

## 4. Verification Summary

- **Real-world 2.6-hour multilingual E2E run (`test_china04.mp4`, 2,815 segments / 1,543 sentence units):**
  - **S3 (`auto-rotate`, 4 Gemini + 2 Groq keys):** ~240–250 segments/min, 100% translated, 0 errors.
  - **S4 (VieNeu-TTS v3 Turbo INT8 48 kHz offline CPU):** 1,543 clips synthesized in ~24 min, 0 failures.
  - **S5 (Fit duration):** **14 s** — `1,495 ok (96.9%)` / `36 stretched (2.3%)` / `12 shortened (0.8%)` / **`0 overflow`**.
  - **S6 (Streaming mixer + FFmpeg mux):** **~2 min 17 s** (down from **35.6 min** — ~15× speedup, no 1.84 GB `_silence.wav`).
  - **S7 (Export video + subtitles):** **31 s**, clean output.
- **Offline test suite (`pytest tests -q --timeout=120 -rs`):** **635+ passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — documented in `tests/SKIPS.md`), **0 failed**, including 38 new tests across `test_s3_optimizations.py`, `test_s6_and_explorer_fixes.py`, `test_groq_provider.py`, `test_auto_rotate.py`, and `test_hotfix_regressions.py`.
- **Packaged `DubTool.exe` checks:** `--version`, `--selftest`, `--layout-check` (0 violations), `--gui-smoke` (10/10 PASS), and `--screenshots` verified from clean commit via `scripts/release.py 19`.
- **Baseline reference:** builds directly on Update 17/18's verified offline VieNeu-TTS v3 E2E pipeline (`test9.mp4` 0% overflow, 0 TTS failures) and extends it with the full 2.6-hour Chinese→Vietnamese E2E run (`test_china04.mp4`, 0% overflow, 0 TTS failures, ~15× faster streaming S6 mix).
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 19` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
