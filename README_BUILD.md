# Building DubTool for Windows

From the repository root:

```powershell
powershell -File packaging/build_exe.ps1
```

This creates `venv-build` (under `DUBTOOL_BUILD_ROOT`), installs the exact
locked dependencies from `packaging/requirements-lock.txt` plus `vieneu==3.8.5`
(`--no-deps`), runs the offline test suite (skip with `-SkipTests`), builds
with PyInstaller using `packaging/dubtool.spec`, checks the bundle structure,
and writes:

- `dist/DubTool/` — the application (one-directory build)
- `dist/DubTool-<version>-win64.zip` — the release zip
- `dist/DubTool/THIRD_PARTY.md` and `dist/DubTool/README_BUILD.md`

### Reproducible dependency lockfile (Windows x64, Python 3.13)

Both Windows Python 3.13 CI and `packaging/build_exe.ps1` (invoked by
`scripts/release.py`) install from the same resolved lockfile:

```powershell
pip install -r packaging/requirements-lock.txt
pip install --no-deps vieneu==3.8.5
pip install -e . --no-deps
```

`tests/test_dependencies_sync.py` enforces that `pyproject.toml`,
`packaging/build_exe.ps1`, `.github/workflows/ci.yml`, and
`packaging/requirements-lock.txt` stay in sync (including the exact `vieneu`
version).

## Version rule (AGENT_UPDATE_9.md 5.2)

The version is `1.0.<N>` where **N is the number of the update being
delivered**. `packaging/update_number.txt` contains that single number;
the build script reads it, derives the full version, and prints the version
and build id at the end. The one true source is `src/dubtool/__init__.py:
__version__` (the exe's version resource is generated from it by the spec);
the About box, `--version`, `BUILD_INFO.txt`, the zip name and the
diagnostics all show the same value. When delivering update N+1: set
`__version__ = "1.0.<N+1>"` in `src/dubtool/__init__.py` and write `<N+1>`
into `packaging/update_number.txt` — nothing else needs changing.

## What the build bundles

Everything goes through the spec (`packaging/dubtool.spec`):

- `vendor/ffmpeg.exe` and `vendor/fonts/NotoSans.ttf`
  (plus its OFL license) are placed into `_internal/vendor/` inside the
  bundle (`ffprobe.exe` is intentionally omitted because probing runs
  in-process via PyAV); `dubtool.app.paths.bundle_root()` resolves them at runtime.
- `packaging/modes.yaml` (shipped mode/model list) into `_internal/packaging/`.
- `faster_whisper`, `ctranslate2`, `onnxruntime`, `av` binaries and data
  (Silero VAD assets, model tokenizers), `vieneu` / `vieneu_utils` / `sea_g2p` /
  `soundfile` (`libsndfile`) for offline VieNeu-TTS v3 ONNX, `certifi` CA bundle,
  `keyring` backends (`keyring.backends.Windows` explicitly), `psutil`
  (the `--profile` CPU sampler, AGENT_UPDATE_6.md 4.1), and PySide6
  plugins via PyInstaller's hooks.

> The Gemini free-tier daily quota is **per project and per model** (see
> `README.md` → Setup), so several keys from one Google project share a
> single quota; only keys from different projects add capacity. Check
> Google's terms before creating extra projects or accounts.

## Non-obvious fixes found while building

1. `keyring.backends.Windows` is only found via an explicit hidden import;
   the collect_submodules call for `keyring.backends` covers the rest.
2. `ctranslate2`/`onnxruntime`/`av` ship wide dynamic libraries that hooks
   sometimes miss on Windows — collected with `collect_dynamic_libs`.
3. `faster_whisper`'s bundled VAD assets are data files (`collect_data_files`);
   without them, Silero-VAD voice filtering throws at ASR time.
4. The windowed build has no console: runtime errors and the job logs go to
   `%LOCALAPPDATA%\DubTool\logs\`; a `--selftest` failure printed to stdout
   is only visible in a run of `DubTool.exe --selftest` from `cmd.exe` (the
   console window-less exe still writes stdout output when launched from an
   existing console).
5. A version resource generated from `src/dubtool/__init__.py:
   __version__` (see the rule above) and a generated icon
   (`packaging/icon.png`) are embedded into `DubTool.exe`.

## FFmpeg & vendor asset verification

External vendor binaries (`ffmpeg.exe`, `fonts/NotoSans.ttf`, `fonts/OFL.txt`)
are pinned by URL and SHA-256 in `packaging/vendor_manifest.json`. Populate or
verify `<DUBTOOL_BUILD_ROOT>/vendor/` on a clean machine with:

```powershell
python scripts/fetch_vendor.py
```

Both `packaging/build_exe.ps1` and GitHub Actions CI verify the bundled SHA-256
of every vendor asset against `packaging/vendor_manifest.json`. To upgrade
FFmpeg deliberately, update `packaging/vendor_manifest.json`,
`packaging/THIRD_PARTY.md`, and `packaging/licenses/THIRD_PARTY_NOTICES.txt`
together, re-run `python scripts/fetch_vendor.py`, and verify with
`DubTool.exe --selftest` (checks filters `subtitles/amix/alimiter/sidechaincompress`,
encoder `libx264`, and bundled Vietnamese font rendering).

## SmartScreen

`DubTool.exe` is unsigned. Windows SmartScreen or an antivirus may warn on
first run; choose "More info → Run anyway" if you built it yourself. Do not
disable security software.

## Packaged-exe checks (AGENT_UPDATE_5.md section 8)

Run from a different folder, with a sanitized environment (PATH limited to
the Windows system folders, temporary `APPDATA`/`LOCALAPPDATA`):

```cmd
DubTool.exe --version
DubTool.exe --selftest --json out.json
DubTool.exe --layout-check
DubTool.exe --screenshots shots
DubTool.exe --headless-run sample\input\test1.mp4 out --mode fast
DubTool.exe --headless-run sample\input\test1.mp4 out --mode accurate
```

`--layout-check` and `--selftest` need no key; `--headless-run` uses the
keyring or the `GEMINI_API_KEY` environment variable.

## Release guard limits (`pre-push` vs GitHub API)

The repository installs a `pre-push` hook (`scripts/git_push_guard.py`) via `scripts/install_git_hooks.py`. Its exact scope is:

- **What it blocks:**
  - Pushing a `v*` tag unless `.git/dubtool-release-ok-<N>` exists and matches `HEAD` (no bypass environment variable).
  - Pushing a version bump on `main` before the release gate stamp is written.
  - Pushing commits or tracked files that fail the secret scanner (`scripts/preflight_gates.py`).
- **What it cannot block:**
  - Server-side tag creation via `gh release create` or the GitHub web UI, because those talk directly to the GitHub API and never invoke client-side Git hooks.
  - Local edits or unpushed commits until a `git push` occurs.
- **Compensating controls:**
  - Always publish through `scripts/release.py <N>` (builds from clean `git archive HEAD`, runs full tests, preflight/secret gates, clean-env exe verification, DoD checks, then pushes, tags, creates the release, and writes `RELEASE_EVIDENCE.txt`).
  - Run `scripts/verify_release.py --audit` (invoked by `scripts/definition_of_done.py`), which queries GitHub releases (`gh release list`) and flags any post-Update-15 release lacking verified local evidence.
