# REVIEW_NOTES.md

## Session: AGENT_UPDATE_9.md work — verified facts only

Working rule (same as previous updates): every section below contains
either a **measured result** from this session (the command or probe
that produced it is state named in it) or an explicit
"not measured / reason". Per-task full rows live in
`REPORT_UPDATE_9.md`; this file is the reviewer's cheat sheet.

> NOTE (writing time): an earlier session's notes contained fabricated
> module names and were rewritten. Nothing below describes a module
> without it existing in the working tree now.

## 1. What exists in this repo for AGU9 (verified present)

- Busy state: `MainWindow.set_busy()` in
  `src/dubtool/app/ui/main_window.py` (the only place deciding it;
  disable list stated in code).
- File card: `FileCard` in `src/dubtool/app/ui/widgets.py` shown by
  `MainWindow.set_video` instead of the drop zone; ✕ wired to
  `MainWindow.clear_video`.
- Progress-details panel: labeled toggle in `LogView` (same file);
  header + liveness + redacted-copy + open-log-folder in the
  progress widget inside `main_window.py` (`_redact`,
  `_copy_details`, `_tick_liveness`).
- Version: `dubtool.__version__ = "1.0.9"` (single source),
  `packaging/update_number.txt` = 9, dynamic pyproject version,
  exe version resource from `packaging/dubtool.spec`,
  `AUTHOR = "sickyturtlez"` in `src/dubtool/branding.py`.
- Open-folder fix: `os.startfile` for folder opens and
  `proc.run(..., gui=True)` for the file-reveal case in `main_window.py`
  / `proc.py`.
- Working-files group in Settings → Advanced (settings_dialog.py), text
  and count/size lines.
- README images: `docs/images/{ready,running,finished,
  settings_api_keys}.png`, tracked; `scripts/update_readme_images.py`
  wired into `packaging/build_exe.ps1` step 7/6;
  `scripts/check_readme_links.py` gate.
- Package lighter: no `ffprobe.exe` shipped (spec lists vendor/
  file-by-file so the filter really matches; build refuses a build that
  contains it), Qt Quick/Qml/Pdf/VirtualKeyboard binaries dropped.
- ASR/TTS/LLM heartbeats: `heartbeat.py` + callers in
  `stages/s2_transcribe.py`, `stages/s4_tts.py`, `llm/client.py`.

## 2. Aggregate offline suite (final state, one run)

`python -m pytest tests -q --ignore=tests/test_screenshots.py` →
**438 passed, 2 skipped in 55.04 s**. Skips are the environmental
symlink-privilege skips in `test_cleanup.py` / `test_housekeeping.py`
(WinError 1314), not coverage skips. `tests/test_screenshots.py` → 3
passed against the fresh screenshot set.

## 3. Build in dist (identity verified this session)

`dist/DubTool/DubTool.exe` — version 1.0.9, build id
`Update 9 (262f9eb)`, sha256 `109ce4790991f76e5949c56e4cb49e122a8d04db7
291689a8c54fb7000e898b1` (BUILD_INFO.txt + `--version` output). Zip
`dist/DubTool-1.0.9-win64.zip` = 186.9 MB; unpacked folder 470.1 MB
(367 files). Fresh-exe gates: `--selftest overall: PASS`, `--gui-smoke
PASS (9 steps)`, `--layout-check total violations: 0`. Screenshot set:
`screenshots/update_9_262f9eb/` + `INDEX.txt`, keys masked.

## 4. Package size table (measure → prune → measure)

Before → after, unpacked 693.9 → **470.1 MB**, zip 267.9 → **186.9 MB**.
Groups after: FFmpeg (ffmpeg.exe only) 121.4 · other 149.8 · ASR runtime
104.6 · PyAV/Python 63.2 · Qt DLLs 31.1 (MB). opengl32sw.dll (19.7 MB)
kept on purpose (GL fallback). ffprobe.exe: 121.2 MB shadow — the
interim build accidentally shipped it because the spec copied `vendor/`
directory wholesale; fixed in the spec and now enforced by the build
script.

## 5. Update-8 close-out numbers (all measured this session)

- Real run `test2` (Fast, no faults): job `2904cb1f5cc6` →
  `sample/output/e2e_test2_v9/…vi.mp4` + docx (21 table rows).
- Punctuation: 1 window, **0 accepted / 1 rejected** in all runs —
  model dropped the token "against" inside a repeated phrase; strict
  validation refused both replays; original kept (before/after shares
  equal this session).
- Fillers: 0 tokens above threshold (no dialog).
- Completeness: balanced `4 flagged, 3/4 fixed, 1 kept flagged`;
  faithful `2 flagged, 1/2 fixed`. Kept-flagged row saved in
  `quality_report.json` in each retained job.
- Fidelity presets (retained runs): faithful fit
  `ok=32/shortened=1/stretched=2`, schedule 4 shifted / max 0.08 s /
  total 0.27 s; balanced fit `ok=29, stretched=5`, schedule 6 shifted /
  max 0.15 s / total 0.48 s.
- `eval/20261031-094228.md` — 34 units × 3 variants, 2 requests used.
- `.git`: 534.11 MiB loose objects with two unreferenced blobs (447 MB,
  104 MB MP4-shaped) → `git gc --prune=now` → **724.67 KiB in one
  pack**, 371 objects, nothing reachable removed, **no push** (no
  remote configured; no rewrite ever needed).

## 6. Cleanup (this session)

Dry run then `--apply --categories caches` → **deleted 5 224 items,
35.8 MB reclaimed** (`cleanup.log`). Project total 6.1 GB; open
questions for the user unchanged: `.venv-build` (997.4 MB),
`sample/` (1.0 GB), `vendor/` (244.6 MB), `models/` (86.8 MB).

## 7. What is NOT in this file

The full command/output provenance and the per-user-request
verification rows live in `REPORT_UPDATE_9.md` when it is present in
the review zip. The GitHub line and the review-zip path are in the
report and the closing message of this session.
