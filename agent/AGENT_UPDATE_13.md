# AGENT_UPDATE_13.md — One "Tạo" button with a model dropdown (no Fast/Accurate modes), OpenAI (ChatGPT) and Anthropic (Claude) support with per-provider API keys, and the real-run evidence that Update 12 left out

Thirteenth update. `AGENT.md` and `AGENT_UPDATE.md` … `AGENT_UPDATE_12.md` still apply: ground rules, data contracts, resumability, secret safety, the review zip rule, the reporting format, the version rule `1.0.<update number>` (this update is **1.0.13**; set `packaging/update_number.txt` accordingly), and the **Definition of done** in `AGENT_UPDATE_7.md` section 1 rule 5 (real-widget tests, real-SDK contract tests, rebuilt exe, fresh screenshots, rewritten `REVIEW_NOTES.md`, one aggregate test run without `--ignore`, cleanup report, and the GitHub line written in the report itself with the three `git` command outputs). This file overrides them only where it says so.

## The user's requests

1. **Remove the "Tạo nhanh" and "Tạo chính xác" buttons.** Keep **one "Tạo" button** and a **model dropdown** next to it. Remove the Fast/Accurate model settings from Settings as well. **The default model is `gemini-3.5-flash-lite`.**
2. **Support API keys for other AI providers: ChatGPT (OpenAI) and Claude (Anthropic).** Research how to call both APIs, and rework the API-key settings so that they fit several kinds of keys. **The user has put `GPT_API_KEY` in the project's `.env` file.**
3. Everything the assistant asked for after the Update 12 review (section 5): real runs and real evidence instead of unit tests only, a clean report, clean tags.

The repository is **public**. A key must never be committed, printed, logged, or shown in a screenshot or report. `.env` is ignored by Git; keep it that way.

---

## 0. Evidence

### 0.1 The Update 12 package (reviewed)
- Done and verified in code: the retry policy (503 never splits; backoff; breaker; checkpoints; fallback), sentence-level dubbing, one voice per video, the voice limiter, real-platform screenshots, history scan, push evidence, 535 passed / 6 skipped.
- **Left out:** no real run of the demo video in sentence mode versus segment mode (the comparison of `AGENT_UPDATE_12.md` 3.7), no `balanced` versus `faithful` measurement, the five fault-injection runs on the exe and the interleaved TTS experiment were "left for the user", README screenshots came from a source run labeled 1.0.11 (and `running.png` shows two stages running at once), tag lists contradict each other (the notes say `v1.0.9, v1.0.11, v1.0.12`, the report says `update-6, update-7, update-9, v1.0.9` … `v1.0.12`), and the report contains several garbled sentences.

### 0.2 The current code (verified)
- `src/dubtool/llm/` has providers `gemini.py`, `ollama.py`, and a **minimal** `anthropic.py` (no JSON schema, `max_tokens` capped at 8000, 429 detection by `"rate" in str(exc)`, no overload mapping, no `retry-after`, `Anthropic(timeout=120.0)` fixed). **There is no OpenAI provider.**
- `config.load_env()` only exports `GEMINI_API_KEY` and `ANTHROPIC_API_KEY`; `GPT_API_KEY` is not read anywhere.
- The Fast/Accurate modes are used in 13 source files and 4 test files (`cli.py`, `config.py`, `api.py`, `compare_translate.py`, `s3_translate.py`, `s5_fit_duration.py`, `app/modes.py`, `app/settings.py`, `app/paths.py`, `app/main.py`, `app/ui/main_window.py`, `app/ui/settings_dialog.py`, `app/i18n.py`, `packaging/modes.yaml`, `scripts/latency_probe.py`, and the tests). `modes.yaml` maps `fast` → `gemini-3.5-flash-lite` (concurrency 1) and `accurate` → `gemini-3.8-flash` (concurrency 3). The job id is derived from the mode.
- Keys are stored per provider in no way: `app/secrets.py` handles Gemini only (keyring entry `gemini_api_keys`).

### 0.3 Research on the provider APIs (collected 2026-11-05; **verify every item against the official documentation while implementing, because model ids and parameters change**)

**OpenAI (ChatGPT API).**
- Keys come from the OpenAI Platform (billed per token, prepaid credits). A ChatGPT subscription does not include an API key. Project keys look like `sk-proj-…`; older keys look like `sk-…`. The Python SDK is `openai` (2.x); the client reads `OPENAI_API_KEY` by default.
- Two endpoints: **Responses API** (`POST https://api.openai.com/v1/responses`, OpenAI recommends it for new projects) and Chat Completions (`POST /v1/chat/completions`, still supported). Header `Authorization: Bearer <key>`.
- **Structured Outputs** (guarantee a reply that matches a JSON Schema): Responses API `text={"format": {"type": "json_schema", "name": "...", "strict": true, "schema": {...}}}`; Chat Completions `response_format={"type": "json_schema", "json_schema": {...}}`. The root of a strict schema must be an **object**, so an array has to be wrapped (`{"items": [...]}`). Output text is `response.output_text`; usage is `input_tokens`, `output_tokens`, and reasoning tokens.
- Model ids listed in the official reference: `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.2`, `gpt-5.1`, `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-4.1` (and `-mini`, `-nano`), `o3`, `o4-mini`. Which of them the user's key can use is decided by **`GET /v1/models`** (free to call).
- **GPT-5-series models are reasoning models:** `temperature` and `top_p` are reported to be rejected (use `reasoning={"effort": ...}` and `text={"verbosity": ...}` instead), and `max_output_tokens` **includes reasoning tokens**, so a small cap can leave no room for the answer. Low or no reasoning effort is faster and cheaper.
- **Errors** (SDK exceptions `openai.RateLimitError`, `openai.InternalServerError`, with `.code`): `429 rate_limit_exceeded` (RPM or TPM) → wait for `Retry-After`; `429 slow_down` → lower the rate; **`429` with `insufficient_quota` / `credit_balance_exhausted` / spend or usage limit → do not retry (billing)**; `503 server_is_overloaded` → retry with longer delays. Headers: `Retry-After` (seconds), `x-ratelimit-*` (reset values are durations such as `1s` or `6m0s`).
- `OpenAI(max_retries=N)` makes the SDK retry by itself; **set it to 0** and let the app's own policy decide (the app must not retry twice).

**Anthropic (Claude API).**
- Keys come from the Anthropic Console (billed per token, separate from a Claude subscription), format `sk-ant-…`. SDK `anthropic`. Endpoint `POST https://api.anthropic.com/v1/messages` with headers `x-api-key: <key>`, `anthropic-version: 2023-06-01`, `content-type: application/json`. Body: `model`, `max_tokens` (required), `system`, `messages`. Reply text is in `content[]` blocks of type `text`; usage is `usage.input_tokens` / `usage.output_tokens`; `stop_reason == "max_tokens"` means the reply was cut.
- **Structured outputs:** `output_config={"format": {"type": "json_schema", "schema": {...}}}` (an earlier beta used the header `structured-outputs-2025-11-13` and `output_format`; confirm which one is current). The schema root must be an object. **Do not prefill assistant messages**: the API reports that prefilling is not supported for some models and points to structured outputs.
- **Errors:** `400 invalid_request_error`, `401 authentication_error`, `403 permission_error`, `404 not_found_error`, `413 request_too_large`, **`429 rate_limit_error`** (with a trustworthy `retry-after` header in seconds; limits are RPM, input tokens per minute, and output tokens per minute), `500 api_error`, **`529 overloaded_error`** (capacity; no trustworthy `retry-after`; retry with backoff or fail over to another provider). Errors can also arrive mid-stream after a 200 when streaming (not used here).
- Model ids given to the assistant as current API model strings: `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5` (and `claude-fable-5-1`, which has extra safety measures and may not be available to every account). Use `GET /v1/models` (Models API) with the user's key to see what is available.

**Gemini (already supported):** keep the existing mapping (`503` model-wide, `504` deadline, `429` per-minute versus per-day quota).

**Implication:** all three providers have the same failure classes. The Update 12 policy table (`AGENT_UPDATE_12.md` 2.1) applies to every provider; this update only adds the provider-specific mapping (task 2.3).

---

## 1. Rules for this update

1. **Reproduce first, verify for real** (Update 11 rule 1, Update 12 rule 2). Unit tests with fakes are necessary and not sufficient; each task below names the real check.
2. **Never print, log, or write an API key**, from any provider. Never read `.env` into output. Keys appear in the UI only as position plus the last 4 characters. This includes screenshots, README images, diagnostics, review zips, and reports.
3. **Cost guard for real calls with paid providers.** The user has provided an OpenAI key for verification. All real OpenAI calls in this update together must stay under **US$0.50**: use the cheapest suitable model (`gpt-5.4-nano` or `gpt-5.4-mini`, whichever the key lists), run at most **20 units** of the demo video, and report the token counts. Do not run a full video through a paid provider unless the user asks. No Anthropic key has been provided: test Anthropic with mock transports only, and say so.
4. Do not claim translations improved. Show text side by side; the user judges.
5. Every number and file claim in your reports is backed by the command and its output. Write the report in complete sentences, re-read it, and fix garbled lines.
6. Log and diagnostic messages must not mention specification identifiers (`AGU`, `AGENT_UPDATE_*`, task numbers).
7. Never write logs or temporary files into the project root; never `git add -A`; no `git gc`, no history rewrite, no `--force`.

---

## 2. Task 1 — One "Tạo" button and a model dropdown; remove the modes

### 2.1 Main window
- Remove `fast_btn` and `accurate_btn`. Add **one primary button "Tạo"** and, immediately before it, a **dropdown "Mô hình"** (non-editable `QComboBox`).
- **Dropdown content** comes from the catalog (task 2.1) and is grouped by provider with non-selectable headers: "Gemini", "OpenAI (ChatGPT)", "Anthropic (Claude)". Each item shows the label and, for paid providers, the suffix "(trả phí)". Items of a provider **with no key** are shown disabled with the suffix "(cần khóa API)" and a tooltip that says where to add it. The **default selection is `gemini-3.5-flash-lite`**. The last choice is remembered (`settings.json`: `model_id`).
- Clicking "Tạo" when the selected model's provider has no key opens Settings → API keys at that provider and does not start the run.
- The first time a paid provider is used, show one confirmation: "Mô hình này tính phí theo token trên tài khoản {provider} của bạn. Văn bản tiếng Anh của video sẽ được gửi tới {provider}." with a "Không hỏi lại" checkbox. Update the first-run privacy notice so that it names the provider that will receive the text, and still says that audio and video stay on the computer. Do not state provider data-retention terms; say "xem điều khoản của nhà cung cấp".
- The dropdown and the button are disabled while a run is in progress (`set_busy`). Tooltip of the dropdown: provider, model id, and "trả phí" if applicable.
- The layout check, the GUI smoke, and the screenshots cover the new row at all sizes and scale factors.

### 2.2 Settings
- Delete the **Models** tab or section (the two Fast/Accurate dropdowns, "Reset", the custom-id fields tied to modes). Settings → General keeps: language, voice, backup voice, Whisper model, CPU level, encode speed, priority, fillers, working files.
- Add to Settings → General, in a small group **"Mô hình dịch"**: **"Mô hình dự phòng"** (dropdown of the models that have a key, plus "Không"; default `gemini-3.5-flash-lite` when the selected model is a different one, otherwise "Không") and the existing **"Khi mô hình đã chọn quá tải hoặc hết hạn mức"** policy dropdown (*Tự động chuyển sang mô hình dự phòng* (default) / *Hỏi tôi* / *Chờ*), with the texts reworded so they no longer say "Chính xác" or "Nhanh". A **"Làm mới danh sách mô hình"** button asks every provider that has a key for its model list (task 3.4) and adds the ids it returns to the dropdown (cached in `settings.json` as `known_models`, ids only). All of this is disabled while a run is in progress.
- Do not add any hint text to the Settings dialog.

### 2.3 Removal and migration
- Remove `app/modes.py`, `packaging/modes.yaml`, the `mode.*` i18n keys, every "Nhanh/Chính xác" string, and `modes_mod` use in `api.py`. Replace them by the catalog (task 2.1). The job id is derived from the **model id** (and the other output-affecting options).
- **CLI:** replace `--mode fast|accurate` by `--model <id>` (provider inferred from the catalog; `--provider` is optional and only needed when the id is ambiguous). Keep `--mode` as a **deprecated alias** that prints a one-line warning: `fast` → `gemini-3.5-flash-lite`, `accurate` → `gemini-3.8-flash`. Update `--headless-run`, `--fault`, `compare-translate` (`provider:model` entries accept `openai:` and `anthropic:`), `scripts/latency_probe.py`, the README, and the docs.
- **Settings migration:** a saved `last_mode` or `mode_models` is ignored; the model becomes the default `gemini-3.5-flash-lite` (log one line). Saved overload-policy values keep their meaning.
- Update the tests that mention modes (`test_api_and_service.py`, `test_app_modules.py`, `test_compare_translate.py`, `test_readable_export.py`, and the others found by `grep`).

### 2.4 Tests (real widgets)
The dropdown lists only the catalog's models, groups them, disables providers without a key, defaults to `gemini-3.5-flash-lite`, remembers the choice, is disabled while busy; clicking "Tạo" without a key for the provider opens Settings at that provider and starts nothing; the paid-provider confirmation appears once per provider and respects "Không hỏi lại"; the Settings dialog has no Models tab; the settings migration; `--mode fast` maps with a warning; no `mode.*` i18n keys remain.

---

## 3. Task 2 — Provider layer: catalog, structured output, OpenAI, Anthropic

### 3.1 Model catalog (`packaging/models.yaml`, shipped in the exe, user-extendable)
One entry per model: `provider` (`gemini` | `openai` | `anthropic` | `ollama`), `id`, `label`, `paid` (bool), `structured` (`json_schema` | `json_object` | `none`), `reasoning` (bool; for OpenAI GPT-5 and o-series), `defaults` (translate concurrency, request timeout, `chunk_max_chars`, `max_output_tokens`, reasoning effort), `verified` (date and source). Seed it as follows and mark each entry's `verified` from the official documentation and, where a key exists, from the provider's model-list endpoint:
- Gemini: `gemini-3.5-flash-lite` (**default**), `gemini-3.8-flash`, `gemini-3.6-flash` (the ids already in `modes.yaml`).
- OpenAI: `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.4`, `gpt-5.5` (and others only if the user's `GET /v1/models` lists them and you verified them).
- Anthropic: `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5` (others only when listed by the Models API for a key).
Do not hardcode prices anywhere. Model-specific defaults (timeouts, concurrency) come from the catalog, not from code.

### 3.2 One interface with a schema parameter
Extend `LLMProvider.complete(system, user, *, schema=None, json_mode=True, max_tokens=...)`. `schema` is a JSON Schema whose **root is an object**; stages that need an array wrap it (`{"items": [...]}`) in `llm/schemas.py` (translate, shorten, punctuate, enforcement, completeness) and the shared parser unwraps it. Providers that support structured output use it (OpenAI `text.format`/`response_format`, Anthropic `output_config.format`, Gemini response schema or `response_mime_type`); if a provider or model rejects the schema (a 400 that names the schema or the format), **degrade once** to "JSON only" prompting, remember that for `(provider, model)` for the rest of the run, and log it. The existing validation of ids and word sequences stays in the shared parser, because a schema guarantees shape, not correctness.

### 3.3 The provider-specific error mapping
All providers raise the same exception classes used by the Update 12 policy (`OverloadedError`, `RateLimitError` with `retry_after`, `QuotaExhausted`, `InvalidKey`, `RequestTooLarge`, `Timeout`, `Transient5xx`). Map as follows, based on the **exception type, status, and error code, never on message text** (except where stated):

| Provider and signal | Class | Notes |
|---|---|---|
| OpenAI `503` (`server_is_overloaded`), `500`, `502` | `OverloadedError` / `Transient5xx` | backoff; never split |
| OpenAI `429 rate_limit_exceeded` (RPM/TPM) | `RateLimitError` | honor `Retry-After`; global pause for the model |
| OpenAI `429 slow_down` | `RateLimitError` | lower the rate (concurrency 1) |
| OpenAI `429` with `insufficient_quota`, `credit_balance_exhausted`, spend or usage limit | `QuotaExhausted` | **do not retry**; go to the fallback; say "tài khoản OpenAI hết tín dụng hoặc đạt giới hạn chi tiêu" |
| OpenAI `401/403` | `InvalidKey` | |
| OpenAI `400` for `context_length_exceeded` or too many output tokens | `RequestTooLarge` | split once |
| Anthropic `429 rate_limit_error` | `RateLimitError` | honor `retry-after` (seconds) |
| Anthropic `529 overloaded_error`, `500 api_error`, `503` | `OverloadedError` / `Transient5xx` | backoff; never split; consider failover |
| Anthropic `413 request_too_large`, `stop_reason == "max_tokens"` | `RequestTooLarge` | split once; raise `max_tokens` before splitting when the cut reply was small |
| Anthropic `401 authentication_error`, `403 permission_error` | `InvalidKey` | |
| Anthropic `400 invalid_request_error` whose body says the credit balance is too low | `QuotaExhausted` | the only message-based rule; verify the exact body and say so in the report |
| Any timeout | `Timeout` | |

### 3.4 OpenAI provider (`llm/openai.py`)
- Use the `openai` SDK with `OpenAI(api_key=..., timeout=..., max_retries=0)`. Use the **Responses API**: `client.responses.create(model=..., instructions=system, input=user, max_output_tokens=..., text={"format": {"type": "json_schema", "name": ..., "strict": True, "schema": ...}})`; read `response.output_text` and the usage fields. Fall back to Chat Completions (`response_format`) only if the Responses call is rejected for the model, and record it.
- **Parameters by model family** (from the catalog's `reasoning` flag): for reasoning models (GPT-5 series and o-series) **never send `temperature` or `top_p`**; send `reasoning={"effort": <config, default "low" or "none" if the model accepts it>}`; set `max_output_tokens` generously (the answer must fit **after** reasoning tokens: default `max(8000, 4 × the expected output)`); if the API still answers 400 because of an unsupported parameter, drop that parameter once, retry, and remember it. For non-reasoning models (for example `gpt-4.1`) send `temperature` low (default 0.2).
- `incomplete_details.reason == "max_output_tokens"` is a **truncated** reply: treat it as `RequestTooLarge` after one retry with a higher cap.
- Read `Retry-After` and the `x-ratelimit-reset-*` durations (`1s`, `6m0s`) into `retry_after`.
- The key test (task 3.5) and the model-list refresh use `GET /v1/models`.

### 3.5 Anthropic provider (`llm/anthropic.py`, rewrite)
- Use the `anthropic` SDK with `Anthropic(api_key=..., timeout=<catalog>, max_retries=0)`. Call `client.messages.create(model=..., max_tokens=<catalog>, system=system, messages=[{"role": "user", "content": user}], output_config={"format": {"type": "json_schema", "schema": ...}})` (confirm the current parameter form in the documentation; if only the beta form exists, use it with its header and say so). **No assistant prefill.**
- Concatenate the `text` blocks; read `usage.input_tokens`/`output_tokens`; handle `stop_reason == "max_tokens"` as in the table; read `retry-after` on 429.
- The key test and the model-list refresh use the Models API (`GET /v1/models`).
- Remove the `"rate" in str(exc)` heuristic and the fixed `120.0` timeout and `8000` cap.

### 3.6 Gemini and Ollama
Adapt Gemini to the `schema` parameter (keep its current behavior as the baseline). Ollama keeps working through the same interface.

### 3.7 Concurrency, limits, and the fallback
- Per-model defaults come from the catalog (start: Gemini lite 3, Gemini others 2, OpenAI 2, Anthropic 2); the Update 12 adaptive concurrency, token bucket, circuit breaker, chunk checkpoints, and fallback apply to **every** provider. The breaker and the quota state are keyed by `(provider, key position, model)`.
- Several keys per provider are allowed (rotation only for rate limits and invalid keys; **never for `QuotaExhausted`, because OpenAI and Anthropic limits belong to the organization or project, not to a key**: say this in `README_BUILD.md`).
- The fallback model may belong to another provider (for example Claude selected, Gemini lite as fallback); the per-unit `model` field and the run summary name the model used for every unit.
- **Cost guard:** `llm.paid_request_cap` (default 200 requests per run for OpenAI/Anthropic models). When it is reached, stop calling the paid model, use the fallback, and say so. The summary shows input and output tokens per model.

### 3.8 Tests (offline, real SDK classes)
- **Mock-transport contract tests:** build the real SDK clients (`OpenAI`, `Anthropic`) with an `httpx.MockTransport` and assert, for each provider: the endpoint path, the headers (`Authorization: Bearer …`; `x-api-key` and `anthropic-version`), the body (`text.format.json_schema` with `strict`; `output_config.format`; no `temperature` or `top_p` for reasoning models; `max_output_tokens` large enough; no prefill), that `max_retries=0`, and that the key never appears in a log line.
- **Error-mapping tests** from realistic error bodies and headers (including `Retry-After` seconds, `x-ratelimit-reset-requests: 6m0s`, `insufficient_quota`, `credit_balance_exhausted`, `server_is_overloaded`, `529 overloaded_error`, `413`, `stop_reason: max_tokens`, `incomplete_details: max_output_tokens`), asserting the class and that `QuotaExhausted` is never retried and never causes a key rotation.
- Schema degradation: a 400 that rejects the schema degrades once and is remembered.
- The catalog loads, the defaults apply, the fallback may cross providers, the paid-request cap switches to the fallback.
- The replay tests of Update 12 pass for each provider's error vocabulary.

---

## 4. Task 3 — API keys for several providers

### 4.1 Storage
`app/secrets.py` gets a provider parameter: `get_keys(provider)`, `add_keys(provider, keys)`, `remove_key(provider, index)`, `move_key(provider, index, delta)`, `has_key(provider)`, `key_source(provider)`. Credential Manager entries (service `DubTool`): `gemini_api_keys` (existing), `openai_api_keys`, `anthropic_api_keys`, each an ordered JSON list with the same limits (10 keys, size check) and the same migration rules. Keep `get_gemini_keys()` as a compatibility wrapper. Keys are never written to a file, `settings.json`, logs, or the process environment.

### 4.2 Environment variables and `.env`
- Names read as a read-only fallback, in this order, each with `_2`, `_3`, … suffixes:
  - Gemini: `GEMINI_API_KEY`, `GOOGLE_API_KEY`.
  - OpenAI: `OPENAI_API_KEY`, **`GPT_API_KEY`** (the name the user chose).
  - Anthropic: `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`.
- `config.load_env()` and the environment readers know all of these names (not only `GEMINI_API_KEY` and `ANTHROPIC_API_KEY`). Treat the placeholder values as missing.
- **When the app runs from source (not frozen), the GUI also loads `.env`** (python-dotenv, real environment variables keep precedence) so that the keys appear in Settings as **read-only rows labeled "Từ .env"** (masked, last 4 characters). Each such row has an **"Nhập vào Credential Manager"** action that copies the key into the keyring (the user can then remove it from `.env`). **The packaged exe never reads a `.env` file.**
- `.env.example` lists the variable names, with empty values. Add a test that `git check-ignore .env` is true and that no tracked file contains a key-like string (the existing scans).

### 4.3 Settings → "Khóa API"
- A provider selector at the top (a segmented control or a combo box): **Gemini | OpenAI (ChatGPT) | Anthropic (Claude)**. Below it the existing key-list editor, parametrized by provider: masked rows (`Khóa 1  ••••ab12`), status badges, **Thêm khóa…**, **Xóa**, **Lên**, **Xuống**, **Kiểm tra khóa đã chọn**, **Kiểm tra tất cả**, with rows from `.env` shown read-only as in 4.2. A short label under each provider name says only where keys come from in a neutral way ("khóa API riêng, tính phí theo token; gói thuê bao ChatGPT/Claude không kèm khóa API" is allowed here **only as the provider's field label, not as a hint paragraph**; if in doubt, leave it out, since the user removed hint text earlier).
- **Thêm khóa…** dialog: one key per line; detect the provider from the prefix and warn when it does not match the selected provider: `sk-ant-` → Anthropic; `sk-proj-` or other `sk-` → OpenAI; `AIza` → Gemini. (`sk-ant-` is checked before `sk-`, because Anthropic keys also start with `sk-`.) Offer "Chuyển sang {provider}" when the pasted keys belong to another provider. Keep the fixes of Update 7 (keys are read before the field is cleared; button states follow the selection).
- The status line never contains a key. All controls are disabled while a run is in progress; the dialog is read-only with the banner as in Update 9.

### 4.4 Key test and model list per provider
Outcomes `ok`, `invalid`, `network`, `quota`, `invalid_model`, `unknown`, as in Update 7, decided by status and error code, not by text. Test with a **free or very cheap call**: `GET /v1/models` for OpenAI and Anthropic (no tokens used), the existing minimal call for Gemini. The same calls feed **"Làm mới danh sách mô hình"**; a model id that the list does not contain is shown with a warning, never silently accepted.

### 4.5 Redaction and notices
Add the OpenAI and Anthropic patterns (`sk-proj-…`, long `sk-…`, `sk-ant-…`) and the **exact values of every stored key of every provider** to the diagnostics and log redaction, and to the pre-commit and pre-push secret scans. Extend the no-leak test to three providers (save fake keys for each, run a mocked failing run, export diagnostics, search the app data, logs, diagnostics zip, `settings.json`, the process environment, screenshots, and command lines).

### 4.6 Tests (real widgets)
Provider selector switches the list; add keys with auto-detection and the mismatch warning; the `sk-ant-` versus `sk-` order; read-only `.env` rows and the import action (fake keyring); masked rows; button states for all providers; the key test for each provider with mock transports; the no-leak test; the frozen exe never reads `.env` (a test that runs the packaged `--selftest` in a folder containing a `.env` with a fake key and shows that it is not found).

---

## 5. Task 4 — The evidence Update 12 left out (real runs, not fakes)

Do these on the packaged exe, from a sanitized environment with the shared caches, and report command outputs.

1. **The demo video** (the dark-energy video, 79 segments; if it is not in `sample/input/`, use `test2`): **sentence mode versus segment mode** with `gemini-3.5-flash-lite`, same settings. Report the number of TTS requests, the number of TTS texts that do not end a sentence (expected 0 in sentence mode), the `fit_status` counts, the shifted and overlapping clips, the maximum shift and the lag at the end, the subtitle cue counts and the longest cue, and **the first 12 units as text**: English, old Vietnamese fragments, new Vietnamese sentence. Do not say which is better.
2. **`balanced` versus `faithful`** in sentence mode on the same video: the lag, the overlaps, the `fit_status` counts; apply the default rule of `AGENT_UPDATE_12.md` 3.2 and say what it chose.
3. **A real run through the OpenAI key** (task 3 cost guard): the first 20 units of the demo video translated by the cheapest suitable GPT model, with the token counts, the request count, the latency per request, the structured-output status (accepted or degraded), and the 20 resulting sentences as text next to the Gemini result for the same units. Say plainly that Anthropic was tested with mocks only.
4. **The five fault runs on the exe** that Update 12 skipped: `--fault llm:503-first=8`, `--fault llm:429-day`, `--fault llm:504-first=2`, `--fault tts:refuse-ids=3,5,8`, `--fault tts:hang=1`: stage times, requests used, the model per unit, and that the result has one voice. Run each as its own command with its own timeout, and report the outcome even when it is a failure.
5. **The interleaved TTS experiment** of `AGENT_UPDATE_12.md` task 4 (`--tts-probe`), from source and from the exe, in the same time window: the table with acceptance per rate, the handshake 503 and `NoAudioReceived` counts, and the control requests.
6. **Screenshots from the new exe** (`DubTool.exe --screenshots`, real platform): the main window with the model dropdown open and closed, Settings → Khóa API for each of the three providers (with fake keys, one row marked "Từ .env"), Settings → General with the fallback model, the paid-provider confirmation, and the README images. **`INDEX.txt` must show version 1.0.13, the build id, and the exe path** (not a source run). The "running" image must show a **possible** state (one stage active at a time). Mask the output-folder path in the README images (use a neutral path such as `C:\Videos\Output`).
7. **Tags:** list `git tag -l` and `git ls-remote --tags origin`, make them consistent (one tag per update, `v1.0.N`; add the missing ones to the matching commits; no moved or deleted tags; no `--force`), and say which commit each points to.

---

## 6. Verification

Rebuild with version `1.0.13`, build id `Update 13`, and verify from a sanitized environment: `--selftest` (it must now also construct the OpenAI, Anthropic, and Gemini clients with a mock transport and import `openai`, `anthropic`, `httpx`, and `pydantic` from the frozen exe), `--layout-check`, `--screenshots`, `--gui-smoke` (extend it: pick each provider in the dropdown, add fake keys through the real dialog for each provider, run with a fake provider per vendor), plus task 4. Packaging: add the new SDKs and their dependencies to the spec (collect data and hidden imports as needed), and report the size change of `dist/DubTool` and the zip (before and after).

No key of any provider may be found in the build root, logs, screenshots, diagnostics, readable files, or the review zip (quiet search); the full-history secret scan is repeated; the aggregate offline suite passes in one run with the real counts.

---

## 7. Report back

Follow `AGENT.md` section 12, `AGENT_UPDATE.md` 7.2, and the Definition of done. Include:

1. Files added and changed; commands run; aggregate test counts.
2. **Task 1:** screenshots of the new button row and Settings; the migration; the list of removed mode references.
3. **Task 2:** the catalog (with its `verified` sources), what the OpenAI and Anthropic documentation said that differed from this file (if anything), the error-mapping table as implemented, and the contract-test results.
4. **Task 3:** the key-storage layout, the environment names read (including `GPT_API_KEY`), the `.env` behavior from source and from the exe, and the no-leak test result.
5. **Task 4:** items 1–7 with real output, including the failures.
6. The build: absolute path of the exe and the zip, build id, version, SHA-256, sizes.
7. The GitHub line with the three `git` command outputs and the tag list.
8. What you could not verify (Anthropic with a real key, the OpenAI models the user's key cannot use, the services' behavior on other days, the dub's sound, translation quality, how the windows look on the user's display).
9. Anything you noticed but did not fix.
10. The review zip path and size.

Then stop and wait for the user's feedback.
