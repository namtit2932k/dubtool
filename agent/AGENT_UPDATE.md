# AGENT_UPDATE.md — Add a pluggable LLM provider (free alternatives to Anthropic)

This file is an **update** to `AGENT.md`. Read it only after you have finished the work described in `AGENT.md` and reported on it. Everything in `AGENT.md` still applies (ground rules, data contracts, stages, acceptance checks, reporting format). This file changes only what is listed below. If something here conflicts with `AGENT.md`, this file wins for the LLM/translation parts only.

---

## 1. Why this change

The user wants to run translation without paying for the Anthropic API. The translation step must work with several providers, chosen by config, with no code change in the pipeline stages.

Providers to support:

| Provider id | What it is | Cost | Notes |
|---|---|---|---|
| `gemini` | Google Gemini API via a free API key from Google AI Studio | Free tier (rate-limited) | **New default.** Content sent may be used by Google to improve its products on the free tier. Warn the user in the README. |
| `ollama` | Local model served by Ollama at `http://localhost:11434` | Free, runs locally | Private. Quality and speed depend on the model and hardware. |
| `anthropic` | Existing implementation | Paid | Keep working, no regression. |

---

## 2. What changes (and what must not)

**Change**
- Replace the direct Anthropic calls in `llm.py` with a provider interface.
- Stage 3 (translate) and the shorten-sentence call in stage 5 use the interface only. They must not import any provider SDK directly.
- Config, `.env.example`, README, and tests.

**Must not change**
- Data contracts in `AGENT.md` section 4 (`transcript.json`, `translated.json`, `state.json`).
- Stage order, CLI commands, skip/force/resume behavior.
- The rule that reviewed segments are never overwritten.
- The prompts' requirements (natural spoken Vietnamese, `max_chars` limit, glossary, JSON-only output, exact ids). Move the prompt text into a shared prompts module so every provider uses the same text.

---

## 3. Design

### 3.1 Interface

Create `src/dubtool/llm/` (turn the current `llm.py` into a package):

```
src/dubtool/llm/
  __init__.py        # get_provider(config) factory
  base.py            # LLMProvider protocol + shared errors
  prompts.py         # translation prompt, shorten prompt (provider-independent)
  parsing.py         # strip fences, extract JSON, validate ids
  gemini.py
  ollama.py
  anthropic.py       # existing logic moved here
```

```python
# base.py
class LLMProvider(Protocol):
    name: str
    def complete(self, system: str, user: str, *, json_mode: bool = True,
                 max_tokens: int = 8000) -> LLMResult: ...

@dataclass
class LLMResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
```

Shared behavior lives **outside** the providers, in one wrapper used by the stages:
- Retries (max 3) for transient errors, with exponential backoff.
- On HTTP 429 / rate-limit errors, wait and retry; respect any retry-after information the provider gives. After the retries, fail the stage with a clear message that names the provider and says the rate limit was hit.
- Parse and validate the output with `parsing.py` (valid JSON, exactly the requested ids, non-empty strings). On validation failure, retry up to 3 times with the validation error appended to the prompt.
- Log provider name, model, and token counts (when available) to `pipeline.log`.

### 3.2 Provider specifics

**Gemini** (`gemini.py`)
- Use the official Google Gen AI Python SDK (`google-genai`). Read the key from env `GEMINI_API_KEY`.
- Request JSON output using the SDK's structured/JSON response option when available; still run the shared parser.
- **Do not hardcode a model name from memory.** Model names and which models are free change over time. Before choosing the default, check the official Gemini API pricing page and models page for a currently free Flash or Flash-Lite model, put that in `config.yaml`, and tell the user which one you picked and why.
- Map SDK rate-limit errors (HTTP 429, RESOURCE_EXHAUSTED) to the shared rate-limit error type.

**Ollama** (`ollama.py`)
- Call the local HTTP API (`POST /api/chat`) with `stream: false` and the JSON format option.
- Config: `ollama_host` (default `http://localhost:11434`) and `ollama_model`.
- On connection refused, fail with: "Ollama is not running. Start it and pull the model: `ollama pull <model>`."
- Set a generous timeout (local models are slow); make it configurable.
- Do not choose a model for the user silently. Put a sensible default in `config.yaml` and explain that Vietnamese quality varies by model.

**Anthropic** (`anthropic.py`)
- Move the existing implementation here unchanged in behavior. Keep reading `ANTHROPIC_API_KEY`.

### 3.3 Smaller models need smaller chunks

Free or local models follow length limits worse than large models. Add config, with these defaults:
- `chunk_size: 25` (was 40) for `gemini` and `ollama`; keep 40 for `anthropic`. Make it per-provider in config.
- In stage 3, after parsing, **enforce `max_chars` in code**: any segment whose `vi` exceeds `max_chars * 1.1` is collected and re-requested once in a single batch ("shorten these"), before moving on. Segments still over the limit afterward are left as is; stage 5 handles them.

---

## 4. Config changes

`config.yaml`:

```yaml
llm_provider: gemini          # gemini | ollama | anthropic
llm:
  gemini:
    model: <fill after checking official docs>
    chunk_size: 25
  ollama:
    host: http://localhost:11434
    model: <fill, e.g. a multilingual model the user has pulled>
    chunk_size: 20
    timeout_sec: 300
  anthropic:
    model: claude-sonnet-5-5
    chunk_size: 40
```

CLI: add `--llm gemini|ollama|anthropic` to `dubtool run` and `dubtool stage`. CLI overrides config.

`.env.example`:

```
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
```

At startup, only require the key for the **selected** provider. Do not demand `ANTHROPIC_API_KEY` if the user selected `gemini` or `ollama`. If the key is missing, tell the user exactly where to get it (Google AI Studio for Gemini) and how to set it.

### 4.1 The `.env` file provided by the user

The user has created a `.env` file and placed it in the **project root**, next to `AGENT.md` and `AGENT_UPDATE.md`. It contains the Gemini key in this exact style (spaces around `=` and double quotes around the value):

```dotenv
GEMINI_API_KEY = "<the user's real key>"
```

Requirements:
- **Load it with `python-dotenv`** (`load_dotenv()` at startup, add the dependency if missing). It handles spaces around `=` and the quotes correctly. Do not write your own parser, and do not rely on `source .env` in a shell (the spaces break it).
- Real environment variables take precedence over `.env` values (the `python-dotenv` default). Keep that behavior.
- After loading, strip whitespace and any surrounding quotes from the value defensively.
- **Placeholder detection**: if `GEMINI_API_KEY` is empty or still looks like a placeholder (for example it contains spaces, or equals text like `key cua toi`), stop with a clear one-line error: "GEMINI_API_KEY in .env is empty or still a placeholder. Get a key at Google AI Studio and put it in .env."
- **Never reveal the key**:
  - Do not `cat`, print, echo, or otherwise display the contents of `.env`.
  - Do not log the key, include it in exceptions, `pipeline.log`, `state.json`, test output, or your reports. To confirm it loaded, say only "GEMINI_API_KEY is set" (optionally its length), never the value or any part of it.
  - Do not copy it into any other file. `.env.example` must keep an empty value.
  - Do not paste it into commands, URLs that get logged, or code.
- **Keep it out of Git**: make sure `.gitignore` contains `.env`. If the project is a Git repository, verify with `git check-ignore .env` and never `git add` it. If `.env` is already tracked or staged, stop and tell the user instead of continuing.
- If the user also adds `ANTHROPIC_API_KEY` to `.env` later, load it the same way. It is only required when `anthropic` is the selected provider.

---

## 5. Tests

Mocks only; tests must pass offline with no keys and no Ollama running.

- Factory returns the right provider for each config value, and errors clearly for an unknown one.
- Parsing: fenced JSON, JSON with extra prose around it, missing ids, duplicated ids, empty strings.
- Rate limit: a fake provider that returns 429 twice and then succeeds results in success; one that always fails produces the clear rate-limit error.
- Stage 3 with a fake provider: `max_chars` enforcement triggers the one-time shorten batch.
- Existing tests still pass unchanged.

---

## 6. README changes

Add a short "Choosing an LLM" section covering:
1. Gemini free tier: how to get a key in Google AI Studio, no card needed, limits are per project and visible in AI Studio, and the privacy note that free-tier content may be used to improve Google products (do not use it for unpublished or sensitive videos).
2. Ollama: install, `ollama pull <model>`, set `llm_provider: ollama`.
3. Anthropic: optional, paid.
4. A note that translation quality differs between providers and the user should read the first translated output and compare.

---

## 7. Acceptance checks

- [ ] `dubtool run sample.mp4 --llm gemini` completes end to end using only `GEMINI_API_KEY` (no Anthropic key set in the environment).
- [ ] `dubtool run sample.mp4 --llm ollama` completes end to end with Ollama running locally, or fails with the clear "Ollama is not running" message if it is not.
- [ ] `dubtool run sample.mp4 --llm anthropic` still works exactly as before (if a key is available to test; otherwise state that it was not tested).
- [ ] `translated.json` is schema-identical regardless of provider.
- [ ] `grep -r "import anthropic\|google.genai\|ollama" src/dubtool/stages` finds nothing (stages never import provider SDKs).
- [ ] The final summary of stage 3 prints: provider, model, number of segments over `max_chars` before and after the enforcement pass, and token counts when available.
- [ ] All tests pass offline.
- [ ] The key is read from `.env` (format `GEMINI_API_KEY = "..."` with spaces and quotes works) via `python-dotenv`.
- [ ] A test with a temporary `.env` containing a placeholder value produces the clear placeholder error; a test with spaces and quotes around a fake value loads it correctly (use a fake value, never the real key).
- [ ] `git check-ignore .env` confirms `.env` is ignored (or state that the folder is not a Git repo).
- [ ] The key does not appear anywhere in `pipeline.log`, `state.json`, test output, or your report (confirm with a search that does not print the key itself, for example by checking the file for the key's presence with a quiet exit-code-only grep).

---

## 7.1 Sample run on the user's test video

The user has placed a test video here (relative to the project root):

```
sample/
  input/
    test1.mp4        # provided by the user, do not modify, move, or delete
  output/            # create if missing
```

After the provider work and its acceptance checks are done, run the full pipeline on this file and deliver the dubbed result:

1. Run: `dubtool run sample/input/test1.mp4 --out sample/output/test1.mp4` using the default provider from `config.yaml` (Gemini), with no other provider unless the user asks.
2. Add the CLI option `--out <path>` to `dubtool run`: when the job finishes, copy the final dubbed video from the job directory (`jobs/<job_id>/output.mp4`) to that path. Create parent folders if needed. If the target already exists, overwrite it only when `--force` is passed; otherwise stop and tell the user.
3. The deliverable is **`sample/output/test1.mp4`**: the same video with Vietnamese dubbing (video stream copied, audio replaced by the Vietnamese track). Use the same file name as the input. Do not put any other files in `sample/output/` (subtitles, logs, and intermediates stay in the job directory).
4. If `sample/input/test1.mp4` does not exist, stop and tell the user; do not substitute another video or generate a fake one.
5. Do not commit media. Add `sample/` and `jobs/` to `.gitignore` if they are not there already.

Verify the output with real measurements, not assumptions (use `ffprobe`):
- [ ] `sample/output/test1.mp4` exists and `ffprobe` reads it without errors.
- [ ] It has one video stream and one audio stream (AAC).
- [ ] Its duration differs from the input by no more than 1 second.
- [ ] The audio is the Vietnamese track (not the original English). Confirm by checking that the file's audio matches `jobs/<job_id>/vi.wav` in duration and that the transcript/translation files exist for the job. State that you cannot judge how the dubbing sounds, and that the user should listen to it.
- [ ] Re-running the same command without `--force` skips all finished stages and does not overwrite `sample/output/test1.mp4`.

If the run fails partway (for example a rate limit from the free tier), do not report success. Because the pipeline is resumable, wait and re-run the same command to continue, and tell the user what happened and how long it took. If it still fails, report the stage, the error (without any key), and what you tried.

## 7.2 Review package: zip the source after every work session

The user has the code reviewed by another assistant, who can only see what is uploaded. So **at the end of every work session** (after finishing this update, and after every later phase or task, even if the work is unfinished), produce a review zip and tell the user where it is. This is a standing rule from now on.

### What to build

Create `scripts/make_review_zip.py` (standard library only) and run it at the end of each session. Also expose it as `dubtool review-zip [--tag phase2]`.

Output: `review/dubtool-review-<YYYYMMDD-HHMM>[-<tag>].zip` (create `review/` if missing; add `review/` to `.gitignore`). Keep only the 3 most recent zips in `review/`; delete older ones.

### Use an allowlist, not "zip everything"

Include only these, if they exist:
- `src/`, `tests/`, `scripts/`
- `config.yaml`, `glossary.example.json`, `pyproject.toml`, `README.md`, `.gitignore`, `.env.example`
- `AGENT.md`, `AGENT_UPDATE.md`
- `REVIEW_NOTES.md` (generated, see below)
- `excerpts/` (generated, see below)

Never include, even by accident: `.env` (any `.env*` file other than `.env.example`), `.venv/`, `venv/`, `.git/`, `jobs/`, `sample/`, `review/`, `__pycache__/`, `*.pyc`, `node_modules/`, model files and caches, and any media file (`*.mp4`, `*.mov`, `*.mkv`, `*.wav`, `*.mp3`, `*.m4a`, `*.zip`).

Fail with a clear error if the resulting zip is larger than 10 MB, and list the largest files so the cause can be found.

### Generated content inside the zip

**`REVIEW_NOTES.md`** (write it fresh each time, short and factual):
1. Date/time, the task or phase just worked on, and whether it is finished.
2. What changed since the previous review zip (files added, changed, deleted).
3. Commands you ran and their real results (tests passed/failed with counts, `ffprobe` results, sample run summary). Do not claim anything you did not actually run.
4. Known issues, shortcuts taken, and anything you are unsure about.
5. Questions for the reviewer, if any.
6. The list of files in the zip.

**`excerpts/`** (small samples so the reviewer can judge real behavior):
- First 10 entries of the latest job's `transcript.json` and `translated.json`.
- The last 200 lines of the latest job's `pipeline.log`, after redaction.
- The `fit_status` counts and ids of `overflow` segments from stage 5.
- Never include audio, video, or full transcripts of the user's video.

### Secret safety (mandatory, checked before the zip is written)

1. Collect the secret values from the environment and from `.env` (for example `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) **in memory only**. Do not print them.
2. Scan every file that would go into the zip (including generated excerpts) for those exact values and for common key patterns (for example strings starting with `AIza` or `sk-ant-`).
3. In excerpts and log lines, replace any match with `[REDACTED]`.
4. If a match is found in a source file (not just a log), **abort without writing the zip**, tell the user the file path and line number only (never the value), and tell them the key should be revoked and replaced.
5. Add a test with a fake key that proves the scan catches it and the redaction works.

### Acceptance checks for this rule

- [ ] `dubtool review-zip` produces a zip in `review/` that opens correctly.
- [ ] `unzip -l` shows no `.env`, no media, no `jobs/`, no `sample/`, no `.venv/`.
- [ ] A test with a fake key planted in a source file makes the script abort without producing a zip, and the error message does not contain the fake key.
- [ ] `REVIEW_NOTES.md` and `excerpts/` are present and contain no secrets.

## 8. Report back

Follow the reporting format in `AGENT.md` section 12, and also tell the user:
1. Which Gemini model you selected as default, the source you checked, and the date.
2. A side-by-side of 5 translated lines from each provider you could actually run (original English, Vietnamese, character count vs `max_chars`).
3. The sample run on `sample/input/test1.mp4`: the exact command used, total time, the provider and model, the `fit_status` counts from stage 5 (including the ids that ended as `overflow`), the `ffprobe` results for `sample/output/test1.mp4`, and the path of the job directory.
4. Anything you could not test, stated plainly.
5. The path of the review zip you just produced (section 7.2) and its size. Remind the user to upload that zip for review. Do the same at the end of every later session.

Then stop and wait for the user's confirmation before doing anything else.
