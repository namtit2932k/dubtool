# AGENT.md — English → Vietnamese Video Dubbing Tool

You are a coding agent building a tool that takes an English-speaking video and produces the same video dubbed in Vietnamese. The user is a front-end engineer (React/TypeScript) making YouTube content. Videos are 1–20 minutes long, one video processed at a time.

Read this whole file before writing code. Work phase by phase. Do not start a phase until the previous one passes its acceptance checks.

---

## 1. Ground rules

1. **This is a fixed pipeline, not an autonomous agent.** Every video goes through the same stages. Do not add agent loops, planners, or multi-agent frameworks. The only LLM-driven loop allowed is the duration-fit retry in stage 5.
2. **Each stage is a separate module with a file-based contract** (input file → output file, defined below). Stages must be runnable alone from the CLI.
3. **Idempotent and resumable.** If a stage's output already exists and `--force` is not passed, skip it. A crash mid-run must be recoverable by re-running the same command.
4. **Per-sentence granularity for expensive work.** TTS and retranslation operate per segment and cache results, so fixing one sentence never reruns the whole video.
5. **No secrets in code.** Read keys from environment variables or `.env` (and add `.env` to `.gitignore`). Never log keys.
6. **Keep it small.** Prefer the standard library and a few well-known packages. Ask before adding a dependency not listed here.
7. **Verify, don't assume.** After each phase, run the acceptance checks and report real results (commands run, output observed). If something fails, say so plainly; do not claim success.
8. **Stay in scope.** Do not implement anything listed under "Out of scope". If you think something out of scope is needed, stop and ask.
9. **Language of code and comments: English.** Translation target language: Vietnamese (`vi`).

---

## 2. Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| Audio/video | FFmpeg (system binary), `pydub` for simple audio assembly |
| ASR | `faster-whisper` (model configurable, default `small`; `large-v3` if GPU present) |
| Translation | Anthropic API, model `claude-sonnet-5-5` (configurable) |
| TTS | Pluggable provider interface. Default for prototype: `edge-tts` (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`). It is unofficial, so keep it behind the interface so it can be swapped. |
| Music separation (Phase 3) | Demucs |
| State | JSON files in the job directory (no database in the MVP) |
| API/UI (Phase 4) | FastAPI backend, React + TypeScript frontend |

Check prerequisites at startup and fail with a clear message if `ffmpeg` is missing or `ANTHROPIC_API_KEY` is unset.

---

## 3. Project layout

```
dub-tool/
  AGENT.md
  README.md
  pyproject.toml
  .env.example
  .gitignore
  config.yaml                 # defaults, overridable by CLI flags
  glossary.example.json       # per-channel terminology
  src/dubtool/
    __init__.py
    cli.py                    # entry point: `dubtool run|stage|status`
    config.py                 # load config.yaml + env + CLI
    job.py                    # job dir, state file, skip/force logic
    stages/
      s1_extract_audio.py
      s2_transcribe.py
      s3_translate.py
      s4_tts.py
      s5_fit_duration.py
      s6_mix.py
      s7_export_subs.py
    tts/
      base.py                 # TTSProvider interface
      edge.py
    llm.py                    # Anthropic client wrapper, retries, JSON parsing
    audio_utils.py            # duration, time-stretch, silence helpers
  tests/
    test_contracts.py
    test_fit_duration.py
    fixtures/                 # tiny sample JSON, no large media in git
  jobs/                       # runtime output, gitignored
    <job_id>/
      input.mp4
      state.json
      audio.wav
      transcript.json
      translated.json
      tts/<id>.mp3
      fitted/<id>.wav
      vi.wav
      output.mp4
      output.vi.srt
```

---

## 4. Data contracts

All timestamps are seconds as floats. `id` is a zero-based integer, stable across all files for the same job.

**`transcript.json`** (stage 2 output)
```json
[
  {"id": 0, "start": 0.52, "end": 4.10, "text": "Welcome back to the channel.", "speaker": null}
]
```

**`translated.json`** (stage 3 output; stage 5 may update `vi`)
```json
[
  {
    "id": 0, "start": 0.52, "end": 4.10,
    "text": "Welcome back to the channel.",
    "vi": "Chào mừng bạn quay lại kênh.",
    "max_chars": 50,
    "reviewed": false,
    "tts_duration": null,
    "fit_status": "pending"
  }
]
```
`fit_status` is one of `pending | ok | stretched | shortened | overflow`.
`reviewed: true` means a human edited or approved this line; stages must never overwrite `vi` for reviewed segments.

**`state.json`**
```json
{
  "job_id": "...",
  "source": "input.mp4",
  "stages": {"s1": "done", "s2": "done", "s3": "pending"},
  "created_at": "ISO-8601"
}
```

Write all JSON with `ensure_ascii=False`, UTF-8, indent 2. Write atomically (write to temp file, then rename).

---

## 5. Pipeline stages

### Stage 1 — Extract audio
- Input: `input.mp4`. Output: `audio.wav` (mono, 16 kHz).
- Command: `ffmpeg -y -i input.mp4 -vn -ac 1 -ar 16000 audio.wav`
- Fail clearly if the file has no audio stream.

### Stage 2 — Transcribe
- Use `faster-whisper`, `language="en"`, VAD filter on.
- Output `transcript.json` as in section 4.
- Merge segments shorter than ~0.8 s into neighbors; split segments longer than ~12 s at sentence punctuation where possible. Keep `id`s sequential after this cleanup.
- Config: model size, device (`cpu`/`cuda`), compute type.

### Stage 3 — Translate
- Translate with the LLM in **chunks of ~40 segments** (about 30–60 s of speech) while including the previous chunk's last 3 segments as read-only context, so tone and terminology stay consistent across chunks.
- Compute `max_chars = int((end - start) * CHARS_PER_SEC)`, `CHARS_PER_SEC` default 14, configurable.
- Prompt requirements (put the real prompt in `llm.py` or a prompts file, not inline everywhere):
  - Natural spoken Vietnamese suitable for YouTube narration, not literal.
  - Never exceed `max_chars` per segment; shorten the meaning rather than overflow.
  - Apply `glossary.json` terms exactly (a map of English term → required Vietnamese term, or "keep original").
  - Return only a JSON array `[{"id": int, "vi": str}, ...]` with exactly the ids provided.
- Validate the response: valid JSON, same ids, non-empty strings. On failure retry up to 3 times with the error message appended; then fail the stage with a clear error.
- Strip code fences before parsing.
- Skip segments with `reviewed: true` if a translated file already exists.

### Stage 4 — Text-to-speech
- Use the `TTSProvider` interface:
  ```python
  class TTSProvider(Protocol):
      def synthesize(self, text: str, voice: str, out_path: Path) -> None: ...
  ```
- Generate `tts/<id>.mp3` for each segment, cache by `(text, voice)` hash so unchanged lines are not regenerated.
- Measure each file's real duration (`ffprobe` or pydub) and store it as `tts_duration` in `translated.json`.
- Rate-limit and retry on transient errors (3 attempts, exponential backoff).
- Voice is configurable globally; per-speaker voice mapping is out of scope until diarization exists.

### Stage 5 — Fit duration
Goal: each Vietnamese clip should fit its original time slot `[start, end]` (plus allowed slack up to the next segment's start).

Algorithm per segment, in order:
1. `slot = next_start - start` if a next segment exists, else `end - start + 1.0`.
2. If `tts_duration <= slot`: `fit_status = "ok"`.
3. Else if `tts_duration <= slot * 1.15`: time-stretch with FFmpeg `atempo` (ratio `tts_duration / slot`, max 1.15). `fit_status = "stretched"`. Output to `fitted/<id>.wav`.
4. Else (too long): ask the LLM to shorten that one sentence to fit (give it the original English, the current `vi`, and the new `max_chars` reduced by ~15%). Regenerate TTS for that segment, re-measure, and repeat from step 2. **Maximum 3 attempts per segment.** If a segment is `reviewed: true`, do not rewrite it; use stretch only.
5. After 3 failed attempts: apply the max stretch (1.15), set `fit_status = "overflow"`, and let the clip run slightly long. Never speed up beyond 1.15.
- Copy `ok` clips to `fitted/<id>.wav` too, so stage 6 reads from one folder.
- Print a summary at the end: counts per `fit_status`, and list the overflow ids.

### Stage 6 — Mix and mux
- Build a silent track the length of the video, overlay each `fitted/<id>.wav` at `start` (ms), export `vi.wav`.
- Phase 1–2: replace the original audio entirely with `vi.wav`.
- Phase 3: use Demucs to separate vocals/background; mix `vi.wav` over the background track, lowering the background by ~8–10 dB while Vietnamese speech is playing (ducking), full volume otherwise.
- Mux: `ffmpeg -y -i input.mp4 -i vi.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest output.mp4`
- Never re-encode the video stream (`-c:v copy`).

### Stage 7 — Export subtitles
- Write `output.vi.srt` from `translated.json` using each segment's `start`/`end` and `vi`. Break lines at ~42 characters, max 2 lines.

---

## 6. CLI

```
dubtool run <video.mp4> [--voice vi-VN-HoaiMyNeural] [--whisper-model small] [--force]
dubtool stage <job_id> <s1|s2|...|s7> [--force]
dubtool status <job_id>
dubtool retts <job_id> --ids 3,7,12      # regenerate TTS for specific segments after manual edits
```

- `run` creates `jobs/<job_id>/`, copies the input, executes all stages in order, prints progress per stage with elapsed time.
- Exit code non-zero on any stage failure; the error message must name the stage and the cause.

`config.yaml` defaults:
```yaml
whisper_model: small
device: cpu
llm_model: claude-sonnet-5-5
chars_per_sec: 14
chunk_size: 40
max_stretch: 1.15
max_fit_attempts: 3
tts_voice: vi-VN-HoaiMyNeural
```

---

## 7. Phases and acceptance checks

### Phase 1 — Working end-to-end on a short clip
Build stages 1–6 with the CLI. Test on a 2–3 minute English video.
Acceptance:
- [ ] `dubtool run sample.mp4` produces `output.mp4` with Vietnamese audio and the original video, playable.
- [ ] Re-running the same command skips completed stages and finishes in seconds.
- [ ] Killing the process during stage 4 and re-running resumes without regenerating finished clips.
- [ ] `transcript.json` and `translated.json` match the contracts in section 4.
- [ ] Missing `ffmpeg` or API key gives a clear one-line error, not a stack trace.

### Phase 2 — Duration fitting and subtitles
Add stage 5 properly and stage 7.
Acceptance:
- [ ] On the sample, report the `fit_status` counts before and after stage 5.
- [ ] No segment is stretched above 1.15×.
- [ ] Reviewed segments are never rewritten (unit test with a fixture).
- [ ] `output.vi.srt` loads correctly in a video player alongside the video.
- [ ] Unit tests for the fit algorithm cover: fits, needs stretch, needs shortening, overflow.

### Phase 3 — Background music preserved
Add Demucs separation and ducking in stage 6, behind a `--keep-music` flag (default on if Demucs is installed).
Acceptance:
- [ ] Output keeps original music/effects at a lower volume under Vietnamese speech.
- [ ] Works on a 20-minute video without running out of memory (process in chunks if needed; report peak RAM observed).

### Phase 4 — Review UI (only after Phases 1–3 are accepted)
FastAPI + React/TypeScript.
- Endpoints: upload video, create job, job status (per stage), get/update `translated.json` segments, regenerate TTS for selected ids, rebuild final video, download outputs.
- UI: upload and progress view; a review table with the English line on the left and an editable Vietnamese line on the right; a play button per segment for its TTS clip; "Regenerate" per row; "Rebuild video" button. Editing a line sets `reviewed: true`.
- Run long work as background tasks and expose progress via polling.
Acceptance:
- [ ] A user can upload a video, edit three lines, regenerate those clips only, and download the rebuilt video.

---

## 8. Error handling and logging

- Use Python `logging`; write a `pipeline.log` in the job directory with stage start/end, durations, and retries.
- External calls (LLM, TTS) must have timeouts and retries; never retry forever.
- Never swallow exceptions silently. Fail the stage and record the error in `state.json`.
- Log token usage per LLM call so the user can see cost per video.

## 9. Testing

- Unit tests with `pytest` for: JSON contract validation, LLM response parsing (including fenced/invalid JSON), fit-duration algorithm, SRT formatting, and skip/force logic.
- Mock the LLM and TTS in tests; tests must pass offline with no API keys.
- Keep media out of git; use tiny generated fixtures (for example a 5-second synthetic tone) when media is needed.

## 10. Out of scope (do not build unless asked)

- Voice cloning, or any voice imitating a specific real person
- Speaker diarization and per-speaker voices
- Multiple target languages
- User accounts, authentication, payments
- Cloud deployment, Docker, queues (Celery, etc.), databases
- Automatic YouTube upload
- Lip-sync

## 11. Legal and safety notes

- Only process video the user owns or has permission to dub. Include a one-line reminder in the README.
- Do not implement voice cloning without an explicit request and a consent check.

## 12. How to report progress

At the end of each phase, reply with:
1. What was built (files created or changed).
2. The acceptance checks, each marked pass or fail with the evidence (command and observed output).
3. Known issues and anything you were unsure about.
4. Your proposed next step, and wait for confirmation before starting the next phase.

If anything in this file is ambiguous or conflicts with what you observe, ask the user one concise question instead of guessing.
