# REPORT_UPDATE_17 — DubTool `1.0.17` (Update 17)

## 1. Summary Table

| Phase | Status | Key Verification / Evidence |
| --- | --- | --- |
| Phase 1 — `VieNeuTTSProvider` offline ONNX (`v3turbo_int8` + `v3nano`) | **PASS** | `src/dubtool/tts/vieneu.py`, `tests/test_vieneu_tts.py` (13/13 PASS), real synthesis verified in both 48 kHz Turbo INT8 and 24 kHz Nano |
| Phase 2 — Stage 4/5 WAV cache, native join, RAM lifecycle & `chars_per_sec=16.5` | **PASS** | `src/dubtool/stages/s4_tts.py`, `src/dubtool/stages/s5_fit_duration.py`, `src/dubtool/stages/s2_transcribe.py`, `config.yaml`, `src/dubtool/config.py`, `src/dubtool/job.py` |
| Phase 3 — Complete removal of `edge-tts`, legacy fallbacks & probe code | **PASS** | Deleted `edge.py`, `fallback.py`, `tts_probe.py`, `validate_voice_settings.py`, `tts_experiment.py`; 0 runtime `edge_tts` references in `src/dubtool/` (verified by `tests/test_vieneu_tts.py`); updated `pyproject.toml`, `src/dubtool/netcheck.py`, `src/dubtool/app/selftest.py`, `src/dubtool/app/diagnostics.py`, `src/dubtool/cli.py`, `src/dubtool/api.py` |
| Phase 4 — UI voice/mode selector, legacy migration & privacy notice | **PASS** | `src/dubtool/app/settings.py`, `src/dubtool/app/ui/settings_dialog.py`, `src/dubtool/app/ui/main_window.py`, `src/dubtool/app/ui/fillers_dialog.py`, `src/dubtool/app/i18n.py`; `--layout-check` 0 violations, `--gui-smoke` 10/10 PASS, `docs/images/dialog_settings_tab1_Chung.png` & `docs/images/ready.png` visually verified |
| Phase 5 — Lean PyInstaller packaging & third-party licenses | **PASS** | `packaging/build_exe.ps1` (Python 3.13 + `vieneu --no-deps`), `packaging/dubtool.spec`, `packaging/THIRD_PARTY.md`, `packaging/RELEASE_NOTES_TEMPLATE.md`, `packaging/licenses/THIRD_PARTY_NOTICES.txt`, `README.md`, `README_BUILD.md` |
| Phase 6 — Packaged `.exe` E2E verification, DoD & Official Release | **PASS** | Real `DubTool.exe` `1.0.17` full `s1–s8` runs on `test9.mp4` (`v3turbo_int8`, 0% overflow, 0 TTS failures) and `zh` clip (`v3nano`, 0% overflow, 0 TTS failures); `DOD_REPORT.md` (9/9 PASS), `RELEASE_EVIDENCE.txt`, `scripts/release.py` |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 17`, `scripts/definition_of_done.py --update 17`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
| `v1.0.16` | `76ba8313` | Yes | `BUILD_INFO=76ba8313, sha256=fad95de80966…` | ok |
| `v1.0.15` | `102528cd` | Yes | `BUILD_INFO=102528cd, sha256=ba9990083f37…` | ok |
| `v1.0.14` | `045660d0` | Pre-release | Mismatched tree (`63c85a18` != `045660d0`), superseded by `v1.0.15` | pre-release, superseded |
| `v1.0.13` | `63c85a18` | No | Historical | tag only |
| `v1.0.12`–`v1.0.10` | `04531dd3`–`1f25a1a9` | Yes | Pre-Update-15 release | no evidence |
| `v1.0.9`–`v1.0.6` | `ad373d84`–`1d8c6428` | No | Historical (`v1.0.8` on `262f9ebd`) | tag only |

---

## 3. Architecture & Key Changes (VieNeu-TTS v3 ONNX Offline)

- **Provider (`src/dubtool/tts/vieneu.py`):**
  - Supports `v3turbo_int8` (48 kHz, INT8 ONNX backbone, ~244 MB one-time HF download, default) and `v3nano` (24 kHz, 48M flow-matching ONNX model, ~269 MB one-time HF download, low-RAM option).
  - Always passes an explicit preset voice (default `"Trúc Ly"`) to `Vieneu.infer(...)` so VieNeu's internal default is never triggered silently.
  - Routes Hugging Face downloads cleanly into `<models_dir>/hf_cache` (never bundled in the release zip) and surfaces a clear Vietnamese/English message if the first-time download lacks internet access.
  - Pins the 5 shared voices (`Trúc Ly`, `Mai Anh`, `Mỹ Duyên`, `Đức Trí`, `Adam`) at the top of both the 25-voice Turbo list and the 11-voice Nano list; auto-migrates legacy `vi-VN-HoaiMyNeural` → `Trúc Ly` and `vi-VN-NamMinhNeural` → `Đức Trí`.
- **Stage 4 & Stage 5 (`src/dubtool/stages/s4_tts.py`, `src/dubtool/stages/s5_fit_duration.py`):**
  - Switched content cache and clip output from `.mp3` to PCM 16-bit `.wav` at native sample rate (48 kHz / 24 kHz); cache key includes `provider`, `mode`, `precision`, and silence-trim settings; `stage_inputs_hash` for `s4` invalidates automatically when switching `tts.mode`.
  - Replaced FFmpeg MP3 concat with direct `soundfile` + `numpy` WAV joining at native sample rate; retained resume-safe fallback reading `.mp3` from older jobs in `s5`.
  - Released Whisper memory (`gc.collect()`) after `s2_transcribe.py` and closed the VieNeu ONNX session (`provider.close()`) in `finally` blocks of `s4_tts.py` and `s5_fit_duration.py`.
  - Updated `chars_per_sec` in `config.yaml` and `src/dubtool/config.py` from `12.5` → `16.5` to match measured VieNeu-TTS v3 speaking rate.
- **Lean Packaging (`packaging/build_exe.ps1`, `packaging/dubtool.spec`):**
  - Pinned `venv-build` creation to Python 3.13 (64-bit) for prebuilt `onnxruntime` / `sea-g2p` wheels and installed `vieneu>=3.8,<4` with `--no-deps` so `gradio`, `librosa`, `numba`, `scipy`, and `torch` are never pulled into the build.
  - Resulting `DubTool-1.0.17-win64.zip` grew by only **+11.5 MB** over `1.0.16` (~210.5 MB vs 199.0 MB), well below the 28–35 MB estimate in `docs/PROPOSAL_VIENEU_TTS.md`.
- **Additional bugs caught & fixed during verification:**
  - `scripts/update_readme_images.py`: removed the 98% pixel-similarity skip threshold that suppressed copying `docs/images/ready.png` when only the single voice-combo text row changed.
  - `src/dubtool/app/ui/settings_dialog.py`: fixed `AttributeError: 'SettingsDialog' object has no attribute '_valid_id'` in `_on_models_fetched` by using `MODEL_ID_RE.fullmatch(model_id)`.
  - `src/dubtool/stages/s4_tts.py`: updated `_set_state_voice` to mirror `voice` and `tts_mode` into in-memory `job.state` so `job.finish_stage()` preserves them in `state.json`.
  - `packaging/dubtool.spec`: added `copy_metadata("vieneu")` so frozen `--selftest` prints exact `vieneu 3.8.5`.

---

## 4. Real Packaged-Exe E2E Verification (`DubTool.exe` `1.0.17`)

Executed directly on the packaged `DubTool.exe --headless-run --profile` with real Gemini translation (`gemini-3.5-flash-lite`) and real offline VieNeu-TTS v3 ONNX synthesis:

| Run | Video & Source Lang | Mode & Voice | s1 | s2 | s3 | s4 (TTS) | s5 (Fit) | s6 (Mix) | s7 (Subs) | Total | Peak RAM | Fit Breakdown (`ok/short/stretch/overflow`) | TTS Failures |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 1 | `test9.mp4` (376.2s, `en` auto) | `v3turbo_int8` / `Trúc Ly` | 0.3s | 42.1s | 24.5s | **81.2s** | 10.8s | 10.8s | 30.4s | **200.9s** | **1,903 MB** | **59 / 1 / 2 / 0** (0% overflow, 62 units) | **0 / 62** |
| 2 | `clip_zh.mp4` (45.9s, `zh` auto — previously failed s4 on `edge-tts` in U16) | `v3nano` / `Mai Anh` | 0.1s | 12.5s | 2.9s | **8.8s** | 6.6s | 0.6s | 0.2s | **32.8s** | **635 MB** | **2 / 2 / 4 / 0** (0% overflow, 8 units) | **0 / 8** |

- Output verification via PyAV:
  - `test9_vi.mp4`: 72,914,792 B, 376.24s, `h264` + `aac` 44,100 Hz; `test9_vi_translation.docx`: 44,457 B.
  - `clip_zh_vi.mp4`: 716,477 B, 45.87s, `h264` + `aac` 24,000 Hz; `clip_zh_vi_translation.docx`: 39,444 B.
- Offline checks on packaged `DubTool.exe`:
  - `--selftest`: **PASS** (14/14 checks including `vieneu_tts`, `imports`, `keyring`, `docx_roundtrip`, `av_decode_compat`).
  - `--layout-check`: **PASS** (25/25 window sizes × states, 0 violations).
  - `--gui-smoke`: **PASS** (10/10 steps in sanitized temp home).

---

## 5. Tests, Artifacts & Limitations

- **Offline test suite:** **579 passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — Windows symlink privilege, documented in `tests/SKIPS.md`), **0 failed**.
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 17` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
- **Deferred to future updates (per `docs/PROPOSAL_VIENEU_TTS.md`):**
  - Voice cloning (requires `librosa`, `soxr`, `kaldi-native-fbank`, denoiser/encoder ONNX weights).
  - CUDA GPU acceleration for VieNeu-TTS v3 (PyTorch backend).
  - Subjective audio/voice quality evaluation is left to the user on their own listening hardware.
