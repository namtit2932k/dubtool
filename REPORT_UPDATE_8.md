# REPORT_UPDATE_8.md

Scope: AGENT_UPDATE_8.md (TTS/LLM resilience, punctuation/segmentation,
fillers/idioms, completeness guards + fidelity preset, model dropdown,
close-out of the Update-6/7 review items, eval harness). Rule 1.3 applies:
**translation quality is not claimed** — structural numbers and side-by-side
text only; the user judges the Vietnamese.

---

## 1. Files added and changed; commands run; audit counts

**Added:** `src/dubtool/{punctuate.py, fillers.py, faults.py, netcheck.py,
av_check.py, eval_translation.py, tts/fallback.py}`, `src/dubtool/llm/
{budget.py, quota_state.py, keypool.py (quota-aware retry)}` updates,
`src/dubtool/stages/` punctuate + resegment wiring (s2), completeness
re-translate pass (s3), priority preset plumbing (s3/s5), suggested-fillers
dialog (`src/dubtool/app/ui/fillers_dialog.py`), `src/dubtool/app/
{asr_stats.py, ui/{overload_dialog within main_window}}`,
`packaging/fillers.yaml` (shipped defaults), `models/piper/`
(voice binaries, not bundled into the exe), tests `test_fillers*`,
`test_punctuate.py`, `test_tts_resilience.py`, `test_llm_faults.py`,
`test_av_check.py`, `test_eval_translation.py`, `tests/fixtures/` (short
text excerpts only from the quoted rows — no media).

**Changed (the load-bearing ones):** `stages/s4_tts.py` (failed segment no
longer aborts; deferred rounds + circuit breaker + adaptive pacing),
`llm/client.py`+`gemini.py` (nested-retry fix, server-error backoff,
split-on-timeout, overload policy), `stages/s3_translate.py` (glossary
filtering to chunk, completeness flags, budget enforcement),
`app/{main,settings,errors,i18n,diagnostics,cleanup,selftest}` and
`app/ui/{main_window,settings_dialog}.py`, `cli.py` (`clean`, `doctor`,
`--fault`, `--selftest --network`), `pyproject.toml` (`av>=11,<19`).
Full list: `git status` in the review zip.

**Aggregate offline suite (final, after every code change this update):**
`py -m pytest tests -q --ignore=tests/test_screenshots.py` →
**412 passed, 2 skipped** (both symptom-privilege symlink skips: Windows
treats non-admin symlink creation as restricted — `test_cleanup.py:219`,
`test_housekeeping.py:84`). The screenshots set (`test_screenshots.py`, not
in the aggregate ignore-list run) is 3 passed via the build gate.

---

## 2. TTS resilience (finding 1)

**What changed:** a failed segment records `tts_failures.json` and skips on;
after the pass, deferred retry rounds run with cooldowns `[60, 180, 300]`
(config `tts.retry_rounds/round_cooldown_s`); a circuit breaker stops
burning requests after `tts.breaker_consecutive: 3` consecutive failures;
adaptive pacing grows per-request delay after failures (cap
`tts.max_delay_s: 2`); provider cooldown ceiling raised 30 s →
`tts.max_cooldown_s` (120). `tts_unavailable` error code + `Tiếp tục/
Mở cài đặt` dialog; if everything fails the finished clips are kept so
Resume continues from where it stopped.

**Fault-injected packaged-exe proof** (`DubTool.exe --headless-run
<30-s clip> --fault tts:fail-first=6 --mode fast`, sanitized home, job
`fb5347df60e9`, output `build/out_ft2/ag8_clip30_vi.mp4`, **result: code=ok,
all s1–s7 done**):

| Piece of evidence | Measured |
| --- | --- |
| Injections absorbed | 6 (`fail-first` 1/6…6/6 across segments id 0,1,2, each logged with exception kind `TTSFailure`, voice `vi-VN-HoaiMyNeural`, `rate=+0%`, `edge-tts=7.3.1`, chars + first-80 text) |
| Circuit breaker | tripped after 3 consecutive failures in pass 1; tripped *again* inside round 1 → skipped straight to the next cooldown |
| Rounds | pass → cooldown 60 s → round 1 → cooldown → rounds continued; s4 total **528.4 s** for a 30-s clip (worst-case service-down profile, no job abort) |
| Real failures during the same run | **14 × `No audio was received`** from genuine edge-tts calls at 18:15–18:20 (round retries at 5-attempt depth) — and the job still completed `code=ok`, i.e. the recovery path absorbed *real* service flakiness, not just the injected fault |
| Finished-clip preservation / later resume | offline suite covers the exhausted case (`test_tts_resilience.py`);lado this fault run ended ok so no Resume needed |
| Counting out the log | no API key / no keyring value anywhere in the failure rows or the exported diagnostics |

**Connectivity table (fresh exe, `--selftest --network`, sanitized env):**
`generativelanguage.googleapis.com` gemini api ok/ok; `huggingface.co`
whisper models ok/ok **clock offset +54.36 s** (worth watching: a wrongly-set
system clock can produce TLS/token oddities); `speech.platform.bing.com`
edge-tts ok/ok.

**What is still unknown about the user's `No audio was received`:** the
cause was never established (their exe produced TTS fine on other days).
Today's sanitized-env run *reproduced* 14 real occurrences and recovered —
consistent with service-side or network-path flakiness (IP throttling,
edge/CDN), still not provable from logs alone.

**Backup voices (2.3):**
- `windows_sapi`: no Vietnamese SAPI voice installed on this machine →
  **not implemented** (per the update's own rule).
- `piper`: implemented behind `PiperTTSProvider` (opt-in `tts.piper_voice`),
  nothing bundled for the exe until the user picks a voice. Samples for the
  user to listen to: `sample/backup_voice/3_piper_vais1000_medium.wav`
  (681,516 B) and `sample/backup_voice/3_piper_vivos_x_low.wav` (701,996 B),
  against the primary `1_primary_hoaimy_edge_tts.mp3` (same Vietnamese text).
  Voice-model licensing must be verified by the user before any model is
  shipped — not done → not bundled.

---

## 3. Gemini retry/overload work (finding 2, 3)

- `complete_json`'s validation loop no longer catches transport errors →
  no more 3×3=9 multiplications (fake-503 test asserts exactly `MAX_RETRIES`
  transport attempts).
- 503/504 get exponential backoff **with jitter** on the server-error
  schedule `[10, 30, 90]`; **no key rotation** on server errors (only on
  rate limits / invalid keys).
- After a 504 or client timeout the chunk is **split in two halves**
  (`llm.split_on_timeout: true`, min 4 units) instead of resending.
- Overload policy after `llm.overload_after: 2` consecutive 503/504:
  GUI `ask` dialog with the three Vietnamese buttons; CLI/headless
  `fallback_fast`; `wait` supported. Chunk→model usage records land in the
  run summary.
- Per-mode timeouts (`request_timeout_s`: Fast 90 / Accurate 180) and every
  request log line carries model + kind + size + elapsed + status.

**Packaged-exe proof** (`--fault llm:503-first=4 --mode fast`, sanitized
home, output `build/out_fl2/ag8_clip30_vi.mp4`, **result code=ok**,
job`.freebuff/san8/home2/…/pipeline.log`):

| Evidence | Measured |
| --- | --- |
| Injections | 4×503s split across a punctuate window and the translate chunk |
| Backoff schedule honoured | observed real backoffs **12 s → 27 s → 74 s → 11 s** (exponential × jitter, never instant) |
| No key rotation on 503 | every server-error line logs “model-wide: no key rotation” |
| Log lines carry the model | `llm gemini/gemini-3.5-flash-lite server error 503 (attempt n/3, translate chunk 1/1 (4 units, 470 chars))…` — model, kind, size, attempt, status |
| Validation-vs-transport split | punctuate window rejected after optimum transport attempts: `punctuate: window 0-7 rejected (giving up after 3 attempts …) — keeping original text` (original kept, nothing multiplied) |
| Job outcome | all s1–s7 done, `code=ok`; total injected-request count stayed at the designed number (not 9+) |

---

## 4. Punctuation and sentence boundaries (finding 8, 11)

### 4.1 Measured variants (no default changed) — test2 full audio, 182.0 s, CPU int8

| Variant | Segs | Ends with sentence-final punct | Longest word run w/o final | Units | Longest unit |
| --- | --- | --- | --- | --- | --- |
| current (small, cond=false, no prompt) | 39 | 41.0% | 166 w | 21 | 20.94 s |
| cond=true (small) | 36 | **11.1%** | 170 w | 13 | 20.94 s |
| initial_prompt (small) | 40 | **52.5%** | **79 w** | 25 | 17.66 s |
| medium (cond=false, no prompt) | 57 | 49.1% | 75 w | 33 | 17.82 s |

Elapsed/RTC: current 45.0 s (RTC 0.25), cond=true 59.9 s (0.33), prompt
41.3 s (0.23), medium 137.6 s (0.76). The hypothesis that
`condition_on_previous_text` helps punctuation is **refuted on this audio**
(11.1% vs 41.0%); an initial_prompt measurably helps (52.5%); medium also
helps but costs ~3× stage-2 time. **No default was changed** — for the
user to decide (the shipped defaults stay `cond=false`, small).

Side-by-side first-60-s text of each variant (for the user to read):
`review/ag8_asr_variants_first60.txt`.

### 4.2–4.3 What shipped (all behind config, old behaviour reachable)

`asr.word_timestamps` (words → separate `words.json`), `asr.sentence_resegment`
(re-segment to sentence/c-clause boundaries using word times, keeping the
0.8 s–12 s caps, sequential ids, dedup), `punctuate.enabled: auto|on|off`
(default `auto`, Fast model, ~40-segment windows, strict no-word-change
validation — a mismatched window is discarded and the original kept and
counted in the quality report; counts against the request budget),
`units.pause_split_s` (0.5 s default pause-aware closing of units).

Row-1 result on the fixture (fake-LLM flow): at least 3 sentences split,
no unit keeps an interior sentence-final mark (see tests).

---

## 5. Fillers/vocatives/idioms (finding 9, 12)

**Shipped:** `packaging/fillers.yaml` + user override, actions
`drop | keep | translate_as`, protected phrases (`girl's girl`, `girl
power`, `trans girl(s)`, `the girls` …), position/context rules in
`fillers.tag_fillers` (vocative only at clause edge with comma/pause and
never after a determiner/possessive; discourse markers comma/pause-gated
so `I like pizza` survives), suggestion → per-video dialog
(`suggested_fillers.json`, “Phát hiện từ đệm…” with Bỏ/Giữ/Dịch thành…),
prompt policy blocks render per-chunk (and `a girl's girl` →
`người luôn đứng về phía phụ nữ` in `glossary.example.json`).

**What the detector measured on the user's real test2 audio** (fresh
word-timestamped transcription, 182 s, 40 segments; suggestion threshold
≥ max(5, 3% of sentences)):
**no token crossed the threshold — zero suggestions**, including `girl`.
So on this video Whisper either did not transcribe the spoken tic or the
speaker does not use it here; the row-1 idiom evidence sits with
`girl's girl` (protected, never a filler). Unit tests keep the two cases
distinct (vocative `Girl,` → drop; `girl's girl`/`trans girls`/`the
girls`/`girl power`/`I like pizza` → untouched).

---

## 6. Completeness guards and the fidelity choice (finding 9, 10)

- Prompt rules: no literary `ta` outside a requested brief; function words
  (`trong, của, để, với…`) preserved; every unit must stay a complete
  clause with the source's final punctuation.
- Truncation/shrink detector flags: EN-final but VI-unfinal; VI ending in
  the connector list (`luôn, và, mà, nhưng, vì, nếu, rằng, là, của, để,
  một, những, các, thì, với, cho, khi, như, hay, hoặc, bởi`); or
  `len(vi) < 0.5 × len(en)` over 40 chars. Flagged units get **one** batch
  re-translate (Fast model) with +30% allowance; still-flagged rows are
  listed in `quality_report.json` and named in the run summary; the
  customer-readable file marks them only when `readable.mark_uncertain:
  true` (default false).
- Fidelity preset `translate.priority: faithful | balanced | timing`
  (default **balanced**, unchanged): faithful ×1.5 char budget +
  fillers-only shortening + `max_shift_s` 4; timing ×0.9; preset numbers in
  `inputs_hash`.

**On test2 (real end-to-end, compiled to the fresh code):**

| Metric | test1 (c20e0b64c551) | test2 (a904c47d5fca) |
| --- | --- | --- |
| Stage s (s2/s3/s4/s5/s6/s7) | 24.2/14.2/120.1/1.1/4.7/33.0 | 31.0/20.3/130.8/34.9/6.0/36.9 |
| Readable docx rows | 17 | 21 |
| Flagged units | no re-translate pass ran in these runs (they predate the pass landing in this exact form); the offline tests carry the flagged→re-translate behaviour; the version minute of the last run (18:22–18:27) does exercise the punctuate rejection path with the original kept |

(The second run's fault injection exercised the punctuate validation, not
the completeness re-translate; the completeness path is asserted by
`test_s3_*`/`quality_report` tests. The in-CLI `eval-translation`
report is available for the user's judgement:
`dubtool eval-translation <job> --variants current,punctuated,faithful`.)

---

## 7. Whisper model choice (finding 8, 11)

Settings → General whisper dropdown: `base | small | default | medium |
large-v3` with per-entry descriptions and a warning line that shows
before the first download, chosen in the dialog. Real-time factors
measured on this machine (`ag8_asr_variants.json`, 60-s clip, CPU int8):
**small RTC 0.37** (22.4 s for 60 s incl. load 2.1 s), **medium RTC 0.79**
(47.5 s/60 s incl. load 5.3 s). On the full 182 s: small 0.25, medium 0.76
(with download-load 7.1 s). All of it is a measured table, not hardcoded.

---

## 8. Close-out of the review items (task 7)

1. **`av` pin:** `av>=11,<19` in `pyproject.toml`; startup check warns
   (`pip install "av>=11,<19"`) when running from source with a newer av;
   `av_decode_compat` PASS in the exe selftest (`av 18.1.0`).
2. **Diagnostics rewrite:** language from settings, `chars_per_sec` from
   the effective config, TTS failure summary, per-stage times, model names,
   quota summary (positions only), connectivity, last-3 log tails, key
   redaction; no-key-value test in the suite.
3. **Single key:** `default_output` is migrated to `old-setting` `default_output_dir` on load; main window + dialog migrated; old key no longer written.
4. **Report shipping:** review zip now includes `REPORT_UPDATE_8.md` +
   rewritten `REVIEW_NOTES.md`; the zip payload is rebuilt for this update
   (path/size in §12).
5. **Real e2e runs:** `--headless-run` (Fast) on the user's videos:
   job `c20e0b64c551` → `sample/output/e2e_test1/test1_vi.mp4` +
   `test1_vi_translation.docx` (17 docx rows); job `a904c47d5fca` →
   `test2` counterpart (21 rows). Stage timings above; requests within the
   per-run budget (default 40).
6. **Cleanup:** dry run then `--apply --categories build,caches` →
   **deleted 5,635 items, reclaimed 42.4 MB** (log `cleanup.log`); project
   total ≈ **3.0 GB**; Top keeps (`REVIEW_NOTES.md` §3 has the full table
   and the questions for the user before anything else is deleted).
7. **GitHub line:** see the closing message of this session (pushed or not,
   commit/tag/gates…).
8. **docx appearance:** LibreOffice is not installed → the visual render
   check remains **not verified**; the docx round-trip test covers the
   structure only.

---

## 9. Build (packaged exe) — identity + screenshot set

| Item | Value |
| --- | --- |
| Build id | `Update 8 (a7b511a)` → see `dist/DubTool/BUILD_INFO.txt` |
| exe sha256 | see `BUILD_INFO.txt` / `--version` output (rebuild-stamped this session) |
| exe path | `dist/DubTool/DubTool.exe` |
| zip | `dist/DubTool-1.0.0-win64.zip` (281,049,170 bytes; `DubTool/` ≈ 694 MB unpacked) |
| Screenshot set | `screenshots/update_8_a7b511a/` (+ `INDEX.txt`), regenerated from the freshly built exe by the build script's step 7; older folders removed by the same step |
| Gates on the fresh exe | `--selftest` overall PASS (11/11); `--layout-check` 0 violations; `--gui-smoke` PASS 9 steps; `--selftest --network` PASS incl. the DNS/TLS table above |

---

## 10. What could not be verified in this update

- Whether the Vietnamese is actually better (the user judges; structure-only
  numbers are reported per rule 1.3).
- Whether the dub *sounds* right (no listening here).
- Why edge-tts returned `No audio was received` on the user's machine
  (reproduced 14× in today's sanitized fault run and recovered, but the
  root cause is service/network-side and logs do not capture it).
- Word document / window appearance on the user's screen + DPI.
- Antivirus/SmartScreen behaviour with the unsigned exe.

## 11. Noticed but not fixed

- The sanitized runs' clock offset on huggingface.co was +54.36 s — if the
  user's clock drifts further, HTTPS handshake drama shows up; the netcheck
  table now surfaces it.
- `sample/` (340.9 MB) and `.venv-build` (997.4 MB) stay only because the
  user has not yet answered the deletion questions (§8.6/REVIEW_NOTES §3).

## 12. Review zip

`review/dubtool-review-20261030-1838-update-8.zip` — **382,621 bytes**
(regenerated after the rebuild; contains the reports, review-notes,
diagnostics digest, logs and the side-by-side punctuation file, no media,
no keys).
