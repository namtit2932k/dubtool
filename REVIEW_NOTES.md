# REVIEW_NOTES.md

## 1. Session summary
- **Date:** 2026-10-27. **Scope:** AGENT_UPDATE_5.md (tasks 1–6): model dropdowns,
  multi-key storage/UI/rotation, hint removal, layout fix + automated check,
  review-accepted fixes, then packaging per AGENT_UPDATE_4 §10–11 with AGU5 §8
  additions. **Status:** finished except the accurate-mode headless run, which is
  blocked by Google's per-day free-tier quota (resumable; see 4).
- Disclaimer (per AGU4/AGU5): the audio/visual quality of the dub is not judged
  by me — listen to the delivered outputs yourself.

## 2. What changed since the previous review zip (files added / edited)
- Added: [packaging/dubtool.spec](packaging/dubtool.spec), [packaging/build_exe.ps1](packaging/build_exe.ps1),
  [packaging/version.txt](packaging/version.txt), [packaging/THIRD_PARTY.md](packaging/THIRD_PARTY.md),
  [README_BUILD.md](README_BUILD.md).
- Added/extended: [src/dubtool/app/layout_check.py](src/dubtool/app/layout_check.py) (AGU5 task 4
  checker; pytest helper + `--layout-check [--scale S]`), keypool
  [src/dubtool/llm/keypool.py](src/dubtool/llm/keypool.py), rotation tests, no-leak test.
- Edited: [src/dubtool/app/selftest.py](src/dubtool/app/selftest.py), [src/dubtool/app/api.py](src/dubtool/api.py) (see 3),
  [src/dubtool/audio_utils.py](src/dubtool/audio_utils.py), [src/dubtool/app/main.py](src/dubtool/app/main.py),
  [src/dubtool/app/ui/main_window.py](src/dubtool/app/ui/main_window.py) (wrap labels get constant min
  height; object names for checker clarity), [src/dubtool/app/ui/settings_dialog.py](src/dubtool/app/ui/settings_dialog.py)
  (tabs, key list, unclamped width), [src/dubtool/app/ui/widgets.py](src/dubtool/app/ui/widgets.py) (LogView toggle),
  [scripts/make_review_zip.py](scripts/make_review_zip.py) (allowlist += AGENT_UPDATE_5.md, README_BUILD.md).
- Deleted: none. Media/binaries stay out of the zip.

## 3. Real bugs found and fixed this session
1. **Packaged app needed the user's PATH for ffmpeg/ffprobe** — sanitized-env
   selftest exposed it. `audio_utils.ffmpeg_bin()/ffprobe_bin()` now prefer the
   bundled `vendor/*.exe` and fall back to PATH.
2. **`_LogBridge.emit` called `logging.Formatter.formatTime` unbound** —
   `'LogRecord' object has no attribute 'converter'` on the first INFO record.
   The fast run never hit it; accurate mode did (793 s wasted run). Fixed
   (formatter instance) + regression test `test_log_bridge_formats_records_without_crashing`.
3. **`--headless-run` imported `secrets` from the wrong module** (top-level vs
   `dubtool.app.secrets`) — ImportError on the packaged exe only.
4. **Selftest font render broke on any absolute window path** — ffmpeg filter
   args can't carry a Windows drive colon (escaped or not). Now: the bundled
   font is copied next to the sample ASS in the temp cwd and `fontsdir=.`.
   Same pattern already used by s7_subtitles (the production path was safe).
5. **Layout invariant failures** (task 4): wrap-dependent height-for-width hints
   went stale after state changes (Qt cached them); fixed with constant minimum
   heights for the wrap labels + checker honoring explicit per-axis minimums
   (LogView toggle's deliberate 28 px width) + unclamped Settings width.
6. **layout_check probe file** — `Path(__file__)` doesn't exist in the frozen
   PYZ; the exe path is used as the fake video when frozen.

## 4. Commands run / results
- Full offline suite: **217 passed** (209 earlier + keypool/key/tabs/log-bridge
  additions, run per-file during the session; `tests/test_api_and_service.py`
  8/8 at the end). See section 5 for scope.
- Layout sweep (source CLI, offscreen + windows platform at scales
  100/125/150/175%): **0 violations across 25 size × state combos**; packaged
  exe sanitized run at 100%: same.
- Packaged exe (fresh `dist/DubTool`, copied to a probe folder, PATH = system
  folders, temp APPDATA/LOCALAPPDATA):
  - `--selftest --json`: **8/8 PASS** incl. keys count 3 (number only), pool
    import, tab list (Khóa API, Mô hình, Chung, Nâng cao); `overall: PASS`.
  - `--layout-check`: **0 violations** (25 combos).
  - `--screenshots --scale 1.0 / 1.5`: 22 PNGs each (5 states × 3 sizes +
    first-run + error dialogs + 4 Settings tabs).
  - `--headless-run test1.mp4 --mode fast`: **code=ok, exit 0**; stages s1 1.7 s,
    s2 33.5 s, s3 10.2 s (provider=gemini model=gemini-3.5-flash-lite,
    units 20, over-max 39→23, tokens in 6026/out 3301), s4 172.7 s,
    s5 102.7 s, s6 23.1 s, s7 52.2 s. Output 110.1 MB.
  - `--headless-run --mode accurate`: s1/s2 skipped (resume), s3 done 965 s
    (model=gemini-3.8-flash, over-max 24→24, tokens in 4537/out 632), s4 done
    175 s, s5 blocked by **429 RESOURCE_EXHAUSTED per-day free-tier quota**
    (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 20/day for the
    model; retryDelay ~7 h). Real rotation fired: `key #1 hit a per-day quota —
    unavailable for this run` → `switched to the next available key`. Stopped
    the process; the job is resumable (s1–s4 done) by re-running the same
    command later — today's quota budget cannot finish it.
- No-leak: quiet in-memory scan of every key value from keyring + env across
  APPDATA/LOCALAPPDATA DubTool dirs, probe profile, dist/, jobs/, review/ →
  **0 matches**; `AIza…`-pattern grep of dist and probe data → none. Keys never
  passed as command-line arguments anywhere.
- ffprobe (bundled vendor ffprobe) on the fast output: 1 h264 video + 1 AAC
  audio stream; duration 216.867 s vs input 216.921 s → **Δ 0.05 s**. Output
  AAC stream duration 216.851 s. Intermediates were auto-cleaned
  (keep_intermediate=False), so the vi.wav cross-check is not possible; stream
  durations stand in. I cannot judge how the dub sounds.

## 5. Test scope / honesty notes
- The earlier full-suite run was 209 passed; since then tests were added and
  each touched file's suite re-ran green, but not one final single full-suite
  run with the last three fixes — the do-not-overclaim count is: files'
  suites green individually, final aggregate not re-run.
- Rotation vs real API: one real 429 (per-day quota) observed and handled
  (switch to next key). A "wait when all keys limited" path was not observed
  in real runs (only key #1 was exhausted; others had quota). Proven offline
  by fake-provider/fake-clock tests.
- Not verified: GUI look/feel on the user's display, clean-machine/SmartScreen
  behavior, sound + translation quality, clean-install behavior without
  Visual C++ redist.

## 6. Known issues / shortcuts
- The accurate run left tables `over_max_chars_before/after` equal (24/24):
  the shorten pass output was consumed but its count is unchanged this run —
  same numbers as s3 summary; worth a reviewer's eye on s5 counts semantics.
- Console mojibake in selftest tab names is cp-style console encoding, not app.
- `dist/`, `build/`, `shots*/`, probe dirs are not in git; binary assets
  (ffmpeg, font) live in vendored `vendor/` (ffmpeg N-118281-gb76053d8bf,
  2025-01-11 gpl build; font NotoSans.ttf OFL).

## 7. Questions for the reviewer — ANSWERED (AGU6 task 6.3)
1. Rendering stale-hint workaround (constant min height on wrap labels) is a
   pragmatic fix — is there a cleaner invariant (e.g. forcing full invalidate
   on wrap labels) that Qt honors deterministically in this version?
   **Decision (AGU6 7.3): keep the constant minimum height.** A comment in
   `main_window.py` explains why the pinned height is the invariant that Qt
   honors deterministically; do not spend time on another mechanism.
2. Should the per-day exhausted key stay unavailable until tomorrow (current:
   yes) or reset on app restart? Currently the pool keeps the flag only for
   the process lifetime.
   **Decision (AGU6 7.3, task 5.3): persist per key *and model* until its
   `until` time** in `%LOCALAPPDATA%/DubTool/quota_state.json`; it is not
   reset on restart, and the file carries no key material (fingerprints
   only).
3. The `s5` accurate `fit_status` overflow ids are per-job; the excerpts show
   the nuanced status counts rather than the raw list — is that enough?
   **Decision (AGU6 7.3): the counts are enough for the UI; the overflow ids
   are additionally written to the job log** (`pipeline.log`) so a ring
   debug is possible without bloating the excerpts.
