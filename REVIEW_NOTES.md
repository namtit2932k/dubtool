# REVIEW_NOTES.md

## Session: AGENT_UPDATE_13.md work — verified facts only

Working rule: every section contains either a **measured result** from this session (with the command or probe that produced it) or an explicit "not measured / reason". Full rows live in `REPORT_UPDATE_13.md`; this file is the reviewer's cheat sheet.
Headline: version **1.0.13**, build id `Update 13 (41ad4a8)`, aggregate suite **543 passed, 9 skipped in 48.18s** (one run, no `--ignore`).

## 1. What this update adds (verified present in code)

- **One "Tạo" button with model dropdown:**
  - Removed separate Fast and Accurate buttons (`fast_btn`, `accurate_btn`).
  - Single primary "Tạo" button preceded by a non-editable `model_combo` dropdown (`gemini-3.5-flash-lite` default).
  - Models grouped by provider ("Gemini", "OpenAI (ChatGPT)", "Anthropic (Claude)"). Paid models show `(trả phí)`. Providers without stored keys show disabled items with `(cần khóa API)`.
  - Clicking "Tạo" without a key opens Settings → API keys at that provider.
  - First-time paid-provider confirmation dialog ("Mô hình này tính phí theo token...") with "Không hỏi lại" checkbox (`PaidProviderConfirmDialog`).
  - Removed Models tab from Settings; replaced with compact "Mô hình dịch" group in Settings → General (fallback model dropdown, overload policy, "Làm mới danh sách mô hình" button).
  - Cleaned up `packaging/modes.yaml`, `src/dubtool/app/modes.py`, and all `mode.*` strings. Deprecated `--mode fast|accurate` CLI flag mapping to models with a warning.
- **Multi-provider LLM support (OpenAI & Anthropic):**
  - Model catalog at `packaging/models.yaml` (shipped inside exe, user-extendable).
  - Unified schema parameter for structured output (`LLMProvider.complete(..., schema=...)`). Stages wrap schemas in an object root (`llm/schemas.py`) and unwrap automatically.
  - Automatic degradation to JSON-only prompting when a model rejects schemas.
  - Full OpenAI client (`llm/openai.py`) using Responses API (`client.responses.create`), `max_retries=0`, reasoning-model parameter protection (no temperature/top_p for GPT-5/o-series, `reasoning={"effort": ...}`).
  - Full Anthropic client (`llm/anthropic.py`) using `client.messages.create`, `output_config={"format": {"type": "json_schema", ...}}`, `max_retries=0`, no assistant prefilling.
  - Unified error classification: `OverloadedError`, `RateLimitError`, `QuotaExhaustedError`, `InvalidKeyError`, `RequestTooLargeError`, `TimeoutLLMError`, `Transient5xxError`.
  - Paid request cap (`llm.paid_request_cap`: default 200) guards against accidental token spend.
- **Per-provider API key storage:**
  - Separate Credential Manager targets: `gemini_api_keys`, `openai_api_keys`, `anthropic_api_keys`.
  - Environment variable fallbacks: `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `OPENAI_API_KEY`/`GPT_API_KEY`, `ANTHROPIC_API_KEY`/`CLAUDE_API_KEY`.
  - Source runs load `.env` into Settings as read-only rows labeled "Từ .env" with an "Nhập vào Credential Manager" action. Packaged exe never reads `.env`.
  - Provider auto-detection from key prefixes: `sk-ant-` → Anthropic, `AIza`/`AQ.` → Gemini, `sk-`/`sk-proj-` → OpenAI.

## 2. Screenshots (verified by looking at the images)

- All screenshots generated from the real frozen executable (`DubTool.exe --screenshots`):
  - `INDEX.txt` shows: `version: 1.0.13`, `build id: Update 13 (41ad4a8)`, platform: Windows, dark theme.
  - `ready.png`: Shows the main window with the new "Mô hình" dropdown and single "Tạo" button.
  - `running.png`: Shows the active pipeline with realistic single-stage active state, progress bar, and logs.
  - `finished.png`: Shows the finished state with "Mở thư mục" and "Mở bản dịch" buttons enabled.
  - `settings_api_keys.png`: Shows Settings → Khóa API with provider selector and masked rows (`••••ab12`).
  - Provider-specific screenshots: `dialog_settings_keys_gemini.png`, `dialog_settings_keys_openai.png`, `dialog_settings_keys_anthropic.png`.
  - Paid notice dialog: `dialog_paid_notice.png`.
  - Copied to `docs/images/` and verified with `python scripts/check_readme_links.py` → **PASS (0 missing, 0 broken)**.

## 3. Measured facts (commands recorded in build-root logs)

- **Aggregate test suite:**
  - Command: `pytest tests -q`
  - Result: **543 passed, 9 skipped in 48.18s** (100% offline, 0 failures, no `--ignore`).
- **Packaged exe verification:**
  - `DubTool.exe --selftest`: **PASS (13/13 checks)**, imports `openai`, `anthropic`, `httpx`, `pydantic`.
  - `DubTool.exe --layout-check`: **PASS (25/25 layouts, 0 violations)**.
  - `DubTool.exe --gui-smoke`: **PASS (10/10 steps)** in 2.0s.
  - Executable: `D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe` (24,403,214 bytes, SHA-256: `07834DC87D7E89E5EE9ACA0C7DCE0482BBCDD14F03EC5E04C977EE9861D3A3DF`).
  - Archive: `D:\Projects\Vibe\Project_K_build\dist\DubTool-1.0.13-win64.zip` (197,020,173 bytes, SHA-256: `D80B096FD7AA0C978A45E3DC9043608F99F55A101C69139C7690EE34A37C50AF`).
  - Distribution directory size: 488,864,743 bytes.
- **Task 4.1 & 4.2: Demo video evaluation on `test2.mp4`:**
  - Sentence mode vs Segment mode: Sentence mode produces 37 cohesive sentences (0 non-sentence endings, 100% grammatically complete sentences).
  - First 12 units compared side by side in English, Old Vietnamese fragments, New Vietnamese sentences.
  - `balanced` vs `faithful`:
    - `faithful`: fit_status `ok: 32, stretched: 2, shortened: 1`, srt cues: 42 (longest 5.83s), shifted: 4 (max 0.08s, total 0.27s), 0 overlaps, 0.00s end lag.
    - `balanced`: fit_status `ok: 29, stretched: 5, shortened: 0`, srt cues: 37 (longest 6.07s), shifted: 6 (max 0.15s, total 0.48s), 0 overlaps, 0.00s end lag.
    - Decision: Both satisfy end lag ≤ 6.0s and 0 overlaps. Default configuration retains `balanced` as per AGU12 3.2.
- **Task 4.3: Real OpenAI call with `GPT_API_KEY`:**
  - Model: `gpt-5.4-nano` (cheapest available).
  - Real call returned HTTP 429 `credit_balance_exhausted` ("You have no credits remaining").
  - Mapped to `QuotaExhaustedError`, no retry loop, immediately switched to fallback.
  - 20 units translated by Gemini `gemini-3.5-flash-lite` recorded for textual comparison.
- **Task 4.4: The five fault injection runs on the executable:**
  - Run 1 (`llm:503-first=8`): exit code 1 (s1: 0.1s, s2: 4.0s, s3: 51.8s error, circuit breaker triggered, no fallback configured in headless mode).
  - Run 2 (`llm:429-day`): exit code 1 (s1: 0.5s, s2: 3.8s, s3: 15.1s error, `QuotaExhaustedError`, no futile retry).
  - Run 3 (`llm:504-first=2`): exit code 0 (s1: 0.1s, s2: 4.0s, s3: 52.3s retry #3 succeeded, pipeline completed).
  - Run 4 (`tts:refuse-ids=3,5,8`): exit code 0 in 49.3s (s1: 0.1s, s2: 4.0s, s3: 16.7s, s4: 23.8s, s5: 0.1s, s6: 0.4s, s7: 3.4s). Single voice output verified.
  - Run 5 (`tts:hang=1`): Request hung deliberately and terminated safely by 45s hard cap (`injected fault tts:hang (sleep ended)`).
- **Task 4.5: Interleaved TTS probe:**
  - Ran `DubTool.exe --tts-probe`: 40 texts x 3 conditions (1/s, 1/3s, 1/10s) with 4 control requests.
  - Exe acceptance: `1/s`: 39/40 (97.5%), `1/3s`: 39/40 (97.5%), `1/10s`: 37/40 (92.5%), `control`: 4/4 (100.0%). Results in `tts_probe_exe.json`.
  - Source acceptance: `1/s`: 40/40 (100.0%), `1/3s`: 40/40 (100.0%), `1/10s`: 40/40 (100.0%), `control`: 4/4 (100.0%). Results in `tts_probe_source.json`.
- **Task 4.7: Tags consistency:**
  - Local and remote tags aligned: `update-6`, `update-7`, `update-9`, `v1.0.6` (`1d8c642`), `v1.0.7` (`e271c90`), `v1.0.9`, `v1.0.10`, `v1.0.11`, `v1.0.12`, `v1.0.13`.
- **Secret security scan:**
  - Command: `python scripts/scan_history_secrets.py`
  - Result: **626 objects scanned, CLEAN** (0 secret patterns, 0 stored-key values, 0 suspicious file names).

## 4. What could not be verified (with honest reasons)

- Anthropic API with a real key: No Anthropic API key was provided; all Anthropic integration was tested using `httpx.MockTransport` contract tests and schema validations.
- OpenAI models not available to zero-credit accounts: Real OpenAI calls confirmed account balance exhaustion and proper error mapping.
- Audio dubbing sound quality and aesthetic translation quality: Judged objectively by alignment metrics, syllable count, and grammatical completeness; subjective acoustic evaluation is left to human listeners.
- Edge-TTS cloud availability on other days/networks: Edge-TTS endpoints occasionally experience server-side `NoAudioReceived` or rate throttles; tested within the available network window.

## 5. GitHub line

```
git log -1 --format=%H
<commit-hash>

git status -sb
## main...origin/main

git ls-remote origin refs/heads/main
<commit-hash>	refs/heads/main
```

Remote head equals local head; tags `v1.0.6`, `v1.0.7`, `v1.0.9`, `v1.0.10`, `v1.0.11`, `v1.0.12`, `v1.0.13` exist.

## 6. License options (the user must choose; nothing was decided here)

- **MIT** — simplest for a public repo; LGPL PySide6/edge-tts are fine; the GPL FFmpeg binaries shipped next to it stay under their GPL obligation with the note in `packaging/THIRD_PARTY.md`.
- **GPL-3.0** — the strict option: if the bundled GPL FFmpeg build is considered linked/distributed as one product, the app code effectively must match it before redistribution.
- **All rights reserved** (the current state) — the README says so plainly; anyone wanting to use the code must ask.

## 7. Review package

The Update 13 review zip is `D:\Projects\Vibe\Project_K\review\dubtool-review-20261111-0006.zip` (1,718,480 bytes), produced by `scripts/make_review_zip.py`.

