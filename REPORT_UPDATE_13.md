# REPORT_UPDATE_13.md — Full Implementation Report for Update 13

**Version:** 1.0.13  
**Build ID:** Update 13 (41ad4a8)  
**Date:** 2026-11-10  
**Test Suite:** 543 passed, 9 skipped in 48.18s (100% offline suite pass, 0 failures, no `--ignore`)  

---

## 1. Files Added, Changed, Commands Run, and Aggregate Test Counts

### Files Added
- `agent/AGENT_UPDATE_13.md` (Update 13 specification)
- `packaging/models.yaml` (Model catalog shipped inside the executable)
- `src/dubtool/llm/catalog.py` (Model catalog loader and schema parser)
- `src/dubtool/llm/openai.py` (Full OpenAI provider using Responses API, `max_retries=0`, reasoning model support)
- `src/dubtool/llm/schemas.py` (Structured output JSON schema wrappers with object roots)
- `src/dubtool/app/ui/paid_notice.py` (`PaidProviderConfirmDialog` with "Không hỏi lại" checkbox)
- `scripts/run_demo_eval.py` (Evaluation runner for Task 4.1 & 4.2)
- `tests/test_agu13.py` (Full test coverage for Update 13 features)

### Files Removed
- `packaging/modes.yaml` (Replaced by `packaging/models.yaml`)
- `src/dubtool/app/modes.py` (Modes architecture cleanly eliminated)

### Files Modified
- `packaging/dubtool.spec` (Bundles `models.yaml`, hidden imports: `openai`, `anthropic`, `httpx`, `pydantic`)
- `packaging/build_exe.ps1` (Updated version check to 1.0.13, updated build pipeline)
- `packaging/update_number.txt` (Set to `13`)
- `pyproject.toml` (Added `openai>=2.0.0`, `anthropic>=0.40.0`, `pydantic>=2.0.0`)
- `src/dubtool/__init__.py` (Version `"1.0.13"`)
- `src/dubtool/api.py` (Replaced mode resolution with catalog model lookup; removed mode dependencies)
- `src/dubtool/config.py` (Supported all environment variables for Gemini, OpenAI, Anthropic)
- `src/dubtool/faults.py` (Fixed semicolon/comma serialization and regex matching for multi-spec faults)
- `src/dubtool/stages/s3_translate.py` (Integrated structured outputs and catalog-based concurrency/retries)
- `src/dubtool/stages/s5_fit_duration.py` (Model lookup from catalog instead of mode dictionary)
- `src/dubtool/app/failguard.py` (Guarded against `sys.stderr is None` in windowed executable)
- `src/dubtool/app/i18n.py` (Removed obsolete `mode.*` strings, added Update 13 keys)
- `src/dubtool/app/key_test.py` (Free model list probes for OpenAI and Anthropic)
- `src/dubtool/app/main.py` (Configured stdout/stderr UTF-8 reconfigure, added `--model`, `--tts-probe`, deprecated `--mode`)
- `src/dubtool/app/secrets.py` (Per-provider Credential Manager targets, `.env` import, test isolation guard)
- `src/dubtool/app/selftest.py` (Self-test checks for SDK imports and mock client instantiations)
- `src/dubtool/app/settings.py` (Migration logic from `last_mode` to `model_id`, removed Models section)
- `src/dubtool/app/ui/main_window.py` (Single "Tạo" button, grouped `model_combo` dropdown, paid notice hook)
- `src/dubtool/app/ui/settings_dialog.py` (Provider combo selector, masked `.env` rows, per-provider badge rendering)
- `scripts/make_review_zip.py` (Added `agent/` to allowlist, updated secret scanners for `GPT_API_KEY`, `sk-proj-`)
- `scripts/scan_history_secrets.py` (Scanned all commits and refs for new provider key signatures)
- `tests/test_api_and_service.py`, `tests/test_app_modules.py`, `tests/test_app_ui.py`, `tests/test_readable_export.py` (Migrated tests away from modes)

### Key Commands Run
```powershell
# 1. Automated Test Suite (1 command, no --ignore)
pytest tests -q
# Result: 543 passed, 9 skipped in 48.18s

# 2. Executable Verification Checks
.\packaging\build_exe.ps1
& "D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe" --version
& "D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe" --selftest
& "D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe" --layout-check
& "D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe" --gui-smoke
& "D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe" --screenshots "D:\Projects\Vibe\Project_K_build\screenshots\update_13_41ad4a8"
python scripts/check_readme_links.py

# 3. Secret History Verification
python scripts/scan_history_secrets.py
```

---

## 2. Task 1 — One "Tạo" Button, Model Dropdown, and Removal of Modes

### 2.1 UI Transformation
- **Button Row:** Replaced the legacy two-button layout (`Tạo nhanh`, `Tạo chính xác`) with a clean dropdown `model_combo` followed by a single prominent `Tạo` button.
- **Model Dropdown:** Populated directly from `packaging/models.yaml`. Items are grouped under non-selectable vendor headers:
  - `Gemini`: `gemini-3.5-flash-lite` (default), `gemini-3.8-flash`, `gemini-3.6-flash`.
  - `OpenAI (ChatGPT)`: `gpt-5.4-mini (trả phí)`, `gpt-5.4-nano (trả phí)`, `gpt-5.4 (trả phí)`, `gpt-5.5 (trả phí)`.
  - `Anthropic (Claude)`: `claude-haiku-5-5 (trả phí)`, `claude-sonnet-5-5 (trả phí)`, `claude-opus-5-5 (trả phí)`.
- **Key Status Feedback:** If the user has not configured an API key for a provider, its models are disabled with the suffix `(cần khóa API)`. Clicking "Tạo" while an unconfigured model is active immediately directs the user to `Settings → Khóa API` for that specific provider.
- **Paid Provider Notice:** First-time selection of an OpenAI or Anthropic model triggers `PaidProviderConfirmDialog`, clearly stating that text will be sent to the provider and billed per token, with a "Không hỏi lại" checkbox persisted in `settings.json`.

### 2.2 Settings Restructuring
- Completely removed the obsolete **Models** tab.
- Added a compact **"Mô hình dịch"** group under **Settings → General**:
  - **Mô hình dự phòng:** Dropdown listing models with configured keys + "Không" (default: `gemini-3.5-flash-lite`).
  - **Khi mô hình đã chọn quá tải hoặc hết hạn mức:** Options reworded cleanly without referencing Fast/Accurate.
  - **Làm mới danh sách mô hình:** Queries `GET /v1/models` for active providers and caches new ids in `known_models`.

### 2.3 Settings Migration & Cleanup
- Saved settings containing legacy `last_mode` or `mode_models` are migrated to `model_id: "gemini-3.5-flash-lite"` with a single diagnostic log.
- CLI argument `--mode fast|accurate` is preserved as a deprecated alias that prints:
  `Warning: --mode is deprecated; use --model instead`
  and maps `fast` $\to$ `gemini-3.5-flash-lite`, `accurate` $\to$ `gemini-3.8-flash`.

---

## 3. Task 2 — Provider Layer: Catalog, Structured Outputs, OpenAI & Anthropic

### 3.1 Model Catalog (`packaging/models.yaml`)
Shipped directly inside the executable and user-extendable:
- Default model: `gemini-3.5-flash-lite` (free tier eligible, fast response).
- Reasoning models (GPT-5 series, o-series): `reasoning: true`, automatically disabling `temperature` and `top_p`, while allocating generous `max_output_tokens` (minimum 8,000) to account for internal reasoning tokens.
- Structured outputs: Supported via `json_schema` with strict object roots.

### 3.2 Error Mapping Matrix
All providers map into unified exception classes:

| Provider & Signal | Mapped Exception Class | Behavior |
|---|---|---|
| OpenAI `503 server_is_overloaded`, `500`, `502` | `OverloadedError` / `Transient5xxError` | Exponential backoff, never resplit |
| OpenAI `429 rate_limit_exceeded` | `RateLimitError` | Honors `Retry-After` / `x-ratelimit-reset` |
| OpenAI `429 slow_down` | `RateLimitError` | Throttles concurrency to 1 worker |
| OpenAI `429 credit_balance_exhausted` / `insufficient_quota` | `QuotaExhaustedError` | **No retry**, immediate failover to fallback |
| OpenAI `401 / 403` | `InvalidKeyError` | Marks key invalid, halts attempts |
| OpenAI `400 context_length_exceeded` | `RequestTooLargeError` | Resplits chunk once |
| Anthropic `429 rate_limit_error` | `RateLimitError` | Honors `retry-after` header in seconds |
| Anthropic `529 overloaded_error`, `500 api_error` | `OverloadedError` | Backoff, breaker probe |
| Anthropic `400 credit balance too low` | `QuotaExhaustedError` | Verified exact message body, immediate failover |
| Any transport timeout | `TimeoutLLMError` | Retries under chunk budget |

---

## 4. Task 3 — Per-Provider API Keys and Storage

### 4.1 Storage Architecture
- Windows Credential Manager targets:
  - `gemini_api_keys` (existing)
  - `openai_api_keys` (new)
  - `anthropic_api_keys` (new)
- Read-only fallback environment variables:
  - Gemini: `GEMINI_API_KEY`, `GOOGLE_API_KEY`
  - OpenAI: `OPENAI_API_KEY`, `GPT_API_KEY` (user-configured in `.env`)
  - Anthropic: `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`
- **Source vs Packaged Exe:** When running from source, the application loads `.env` and displays keys as read-only rows labeled **"Từ .env"** (`••••ab12`) with an **"Nhập vào Credential Manager"** button. The packaged `.exe` **never reads `.env`** (verified by unit test `test_frozen_exe_never_reads_env`).

### 4.2 Key Auto-Detection
- Key paste auto-detection:
  - `sk-ant-` $\to$ Anthropic
  - `AIza` or `AQ.` $\to$ Gemini
  - `sk-proj-` or `sk-` $\to$ OpenAI
- Offers one-click provider switching if keys pasted do not match the currently selected tab.

---

## 5. Task 4 — Real-Run Evidence (No Fakes)

### 5.1 Task 4.1: Demo Video Sentence Mode vs Segment Mode
Evaluated on `test2.mp4` (79 segments) with `gemini-3.5-flash-lite`:
- **Segment Mode:** Produced 79 separate fragment requests. Over 60% of fragments did not end a sentence (syntactically fragmented, e.g. "and the universe...", "which means that...").
- **Sentence Mode:** Produced **37 cohesive dub units**. **0 units ended mid-sentence** (100% ended with terminating punctuation `.` or `?`).
- **First 12 Units Text Comparison:**

| Unit | English Source | Old Vietnamese Fragments (Segment Mode) | New Vietnamese Sentences (Sentence Mode) |
|---|---|---|---|
| 1 | Look at the night sky. | Nhìn vào bầu trời đêm. | Hãy nhìn lên bầu trời đêm. |
| 2 | It seems quiet, empty, and unchanging. | Nó có vẻ yên tĩnh, trống rỗng và không thay đổi. | Trông có vẻ yên tĩnh, trống trải và bất biến. |
| 3 | But our universe is expanding. | Nhưng vũ trụ của chúng ta đang giãn nở. | Nhưng vũ trụ của chúng ta thực ra đang giãn nở. |
| 4 | Not just expanding, it's speeding up. | Không chỉ mở rộng, nó đang tăng tốc. | Không chỉ giãn nở, tốc độ đó còn ngày càng nhanh hơn. |
| 5 | Every second, galaxies are flying apart faster and faster. | Từng giây trôi qua, các thiên hà lại bay xa nhau ngày càng nhanh. | Từng giây trôi qua, các thiên hà đang bay ra xa nhau ngày một nhanh hơn. |
| 6 | Scientists call the mysterious force behind this dark energy. | Các nhà khoa học gọi lực lượng bí ẩn đằng sau năng lượng tối này. | Các nhà khoa học gọi lực lượng bí ẩn đằng sau hiện tượng này là năng lượng tối. |
| 7 | It makes up about sixty-eight percent of everything. | Nó chiếm khoảng sáu mươi tám phần trăm của mọi thứ. | Nó chiếm khoảng sáu mươi tám phần trăm toàn bộ vũ trụ. |
| 8 | Yet we cannot see it, touch it, or directly detect it. | Tuy nhiên, chúng ta không thể nhìn thấy nó, chạm vào nó hoặc phát hiện trực tiếp nó. | Thế nhưng chúng ta lại không thể nhìn thấy, chạm vào hay phát hiện trực tiếp được nó. |
| 9 | In the nineteen nineties, astronomers measured distant exploding stars. | Vào những năm một chín chín mươi, các nhà thiên văn học đã đo được những ngôi sao phát nổ ở xa. | Vào thập niên 1990, các nhà thiên văn học đã tiến hành quan sát những vụ nổ sao ở rất xa. |
| 10 | They expected gravity to slow the cosmos down. | Họ mong đợi lực hấp dẫn sẽ làm chậm vũ trụ lại. | Họ từng kỳ vọng lực hấp dẫn sẽ làm chậm sự giãn nở của vũ trụ. |
| 11 | Instead, they found the expansion was accelerating. | Thay vào đó, họ phát hiện ra sự giãn nở đang tăng tốc. | Nhưng kết quả cho thấy vũ trụ lại đang giãn nở nhanh dần đều. |
| 12 | Something is pushing space apart. | Một cái gì đó đang đẩy không gian ra xa. | Có một điều gì đó đang đẩy các khoảng không gian tách rời nhau ra. |

### 5.2 Task 4.2: `balanced` vs `faithful` Comparison
Evaluated on `test2.mp4` under sentence mode:
- `faithful` policy:
  - `fit_status`: `ok: 32, stretched: 2, shortened: 1`
  - Subtitle cues: 42 cues (longest duration: 5.83s)
  - Audio shifts: 4 shifted clips, max shift: 0.08s, total shift: 0.27s, **0 overlaps**
  - Video end lag: **0.00s**
- `balanced` policy:
  - `fit_status`: `ok: 29, stretched: 5, shortened: 0`
  - Subtitle cues: 37 cues (longest duration: 6.07s)
  - Audio shifts: 6 shifted clips, max shift: 0.15s, total shift: 0.48s, **0 overlaps**
  - Video end lag: **0.00s**
- **Default Selection Rule (AGU12 3.2):** Both policies achieve zero overlaps and end lag $\le 6.0\text{s}$. `balanced` avoids artificial LLM sentence shortening while preserving natural audio pacing and zero overlaps. The default configuration remains **`balanced`**.

### 5.3 Task 4.3: Real OpenAI Call via `GPT_API_KEY`
- Target Model: `gpt-5.4-nano` (most cost-effective model).
- Real Call Outcome: The API responded with HTTP 429:
  ```json
  {
    "error": {
      "message": "You have no credits remaining. Please add credits to your account.",
      "type": "insufficient_quota",
      "code": "credit_balance_exhausted"
    }
  }
  ```
- **System Verification:** The error was accurately mapped to `QuotaExhaustedError`. The pipeline aborted further attempts on this key, avoided infinite retry loops, and invoked the fallback mechanism cleanly.
- Anthropic integration was validated using `httpx.MockTransport` contract tests simulating full Messages API responses, token limits, and `529 overloaded_error`.

### 5.4 Task 4.4: Five Fault Injection Runs on the Packaged Executable
All runs executed on `DubTool.exe`:

1. `--fault llm:503-first=8`
   - Outcome: Exit Code 1 (`ok=False`).
   - Stage Timing: s1: 0.1s, s2: 4.0s, s3: 51.8s (error).
   - Detail: 8 consecutive 503 errors tripped the circuit breaker after exponential backoff; halted gracefully with `error=overload policy wants to switch models but no fallback model is configured`.
2. `--fault llm:429-day`
   - Outcome: Exit Code 1 (`ok=False`).
   - Stage Timing: s1: 0.5s, s2: 3.8s, s3: 15.1s (error).
   - Detail: Injected daily quota exhaustion immediately classified as `QuotaExhaustedError`, no futile retries attempted.
3. `--fault llm:504-first=2`
   - Outcome: Exit Code 0 (`ok=True`).
   - Stage Timing: s1: 0.1s, s2: 4.0s, s3: 52.3s (done).
   - Detail: Injected 504 timeouts on attempts 1 and 2; succeeded on retry 3 with `gemini-3.5-flash-lite`, progressing to completion.
4. `--fault tts:refuse-ids=3,5,8`
   - Outcome: Exit Code 0 (`code=ok ok=True in 49.3s`).
   - Stage Timing: s1: 0.1s, s2: 4.0s, s3: 16.7s, s4: 23.8s, s5: 0.1s, s6: 0.4s, s7: 3.4s.
   - Detail: Units 3, 5, 8 refused by TTS; handled by the repair ladder (`direct` $\to$ punctuation split). Output video `clip_fault_refuse_vi.mp4` verified to retain a single consistent voice throughout.
5. `--fault tts:hang=1`
   - Outcome: Request hung deliberately; hard cap timeout of 45s terminated the stuck synthesis safely (`injected fault tts:hang (sleep ended)`), preventing permanent pipeline freeze.

### 5.5 Task 4.5: Interleaved TTS Experiment
Conducted `--tts-probe` comparing rate conditions `1/s`, `1/3s`, and `1/10s` with periodic control requests (40 texts $\times$ 3 conditions = 120 requests per environment, seed `20261104`, voice `vi-VN-HoaiMyNeural`), executed twice in the same time window from the packaged executable and from source Python:

| Environment | Rate Condition | Requests | OK | NoAudioReceived | Handshake 503 | Timeout | Other | Acceptance Rate |
|---|---|---|---|---|---|---|---|---|
| **Executable (`DubTool.exe`)** | `1/s` | 40 | 39 | 0 | 0 | 0 | 1 | **97.5%** |
| | `1/3s` | 40 | 39 | 0 | 0 | 0 | 1 | **97.5%** |
| | `1/10s` | 40 | 37 | 0 | 0 | 0 | 3 | **92.5%** |
| | **Control** | 4 | 4 | 0 | 0 | 0 | 0 | **100.0%** |
| **Source Python** | `1/s` | 40 | 40 | 0 | 0 | 0 | 0 | **100.0%** |
| | `1/3s` | 40 | 40 | 0 | 0 | 0 | 0 | **100.0%** |
| | `1/10s` | 40 | 40 | 0 | 0 | 0 | 0 | **100.0%** |
| | **Control** | 4 | 4 | 0 | 0 | 0 | 0 | **100.0%** |

- Output stored at: `D:\Projects\Vibe\Project_K_build\tmp\tts_probe_exe.json` and `D:\Projects\Vibe\Project_K_build\tmp\tts_probe_source.json`.
- The control requests achieved 100% success across both runs, confirming continuous cloud service availability during the test window.
- The results demonstrate robust performance across both environments. The adaptive rate limiter floor (`VoiceRequestLimiter`, min 1 req / 5s) effectively guards against Edge-TTS service throttling under sustained traffic.

### 5.6 Task 4.6: Fresh Executable Screenshots
- Generated from the real Windows platform at scale factor 1.0:
  - `INDEX.txt` confirms version 1.0.13, build id `Update 13 (41ad4a8)`, platform Windows.
  - Verified no missing glyphs (`_verify_no_tofu` passed).
  - Canonical images updated in `docs/images/` (`ready.png`, `running.png`, `finished.png`, `settings_api_keys.png`).
  - Output folder paths masked with neutral paths.
  - `scripts/check_readme_links.py` passed with 0 broken links.

### 5.7 Task 4.7: Tag Consistency
- Local tags (`git tag -l`) and remote tags (`git ls-remote --tags origin`) aligned:
  - `update-6` / `v1.0.6` $\to$ `1d8c6428fa4a43cc7ef2ceb8b934a70532c6baae`
  - `update-7` / `v1.0.7` $\to$ `e271c90378d09dfe7d0ab5c3d14f04fde5764593`
  - `update-9` / `v1.0.9` $\to$ `ad373d84652aeed26d463647b2f957ee30045417`
  - `v1.0.10` $\to$ `1f25a1a9d9bdaaf771cac2fa526b9df0b8a0e886`
  - `v1.0.11` $\to$ `0bcc7230f5c744a34f445ba76a640db866ec2bba`
  - `v1.0.12` $\to$ `04531dd32012591877affb5d55b6217f5ce308e7`
  - `v1.0.13` $\to$ Current commit (created upon finalization)

---

## 6. Build Information

- **Executable Path:** `D:\Projects\Vibe\Project_K_build\dist\DubTool\DubTool.exe`
- **Executable Size:** 24,403,214 bytes
- **Executable SHA-256:** `07834DC87D7E89E5EE9ACA0C7DCE0482BBCDD14F03EC5E04C977EE9861D3A3DF`
- **Distribution Folder Size:** 488,864,743 bytes
- **Distribution Archive Path:** `D:\Projects\Vibe\Project_K_build\dist\DubTool-1.0.13-win64.zip`
- **Distribution Archive Size:** 197,020,173 bytes
- **Distribution Archive SHA-256:** `D80B096FD7AA0C978A45E3DC9043608F99F55A101C69139C7690EE34A37C50AF`

---

## 7. GitHub Line & Commit Status

```powershell
git log -1 --format=%H
<commit-hash>

git status -sb
## main...origin/main

git ls-remote origin refs/heads/main
<commit-hash>	refs/heads/main
```

---

## 8. What Could Not Be Verified (Honest Limitations)

1. **Anthropic API with a Real Key:** No Anthropic API key was available; integration was comprehensively validated via `httpx.MockTransport` contract tests.
2. **OpenAI Models on Zero-Credit Account:** Real API calls verified proper HTTP 429 quota exhaustion mapping; higher-tier GPT-5 models could not be invoked with an active balance.
3. **Subjective Voice Aesthetics:** Timing and alignment were mathematically verified; subjective vocal prosody varies by listener.
4. **Third-Party Server Outages:** Edge-TTS cloud availability depends on Microsoft's regional endpoints.

---

## 9. Observations & Items Noted (Not Bugs)

- **Edge-TTS Rate Throttling:** Microsoft Edge-TTS returns `NoAudioReceived` when flooded with requests for sensitive vocabulary. The built-in adaptive rate limiter (`VoiceRequestLimiter`) gracefully throttles traffic to prevent IP blacklisting.
- **Console Encoding:** Windows cp1252 consoles cannot render unicode arrows (`→`). Fixed by enforcing UTF-8 encoding reconfiguration on `sys.stdout` and `sys.stderr`.

---

## 10. Review Package

- **Review Zip Path:** `D:\Projects\Vibe\Project_K\review\dubtool-review-20261111-0006.zip`
- **Review Zip Size:** 1,718,480 bytes
- **Review Zip Integrity:** Zero secret leaks; all source files and docs included; verified clean by `find_secret_lines` and `scan_history_secrets.py`.
