# Building DubTool for Windows

From the repository root:

```powershell
powershell -File packaging/build_exe.ps1
```

This creates `.venv-build`, installs pinned dependencies, runs the offline
test suite (skip with `-SkipTests`), builds with PyInstaller using
`packaging/dubtool.spec`, checks the bundle structure, and writes:

- `dist/DubTool/` — the application (one-directory build)
- `dist/DubTool-<version>-win64.zip` — the release zip
- `dist/DubTool/THIRD_PARTY.md` and `dist/DubTool/README_BUILD.md`

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

- `vendor/ffmpeg.exe`, `vendor/ffprobe.exe` and `vendor/fonts/NotoSans.ttf`
  (plus its OFL license) are placed into `_internal/vendor/` inside the
  bundle; `dubtool.app.paths.bundle_root()` resolves them at runtime.
- `packaging/modes.yaml` (shipped mode/model list) into `_internal/packaging/`.
- `faster_whisper`, `ctranslate2`, `onnxruntime`, `av` binaries and data
  (Silero VAD assets, model tokenizers), `edge_tts` data, `certifi` CA bundle,
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

## FFmpeg verification

You can swap the bundled FFmpeg by replacing `vendor/ffmpeg.exe` and
`vendor/ffprobe.exe` before building. Verify after the build with
`DubTool.exe --selftest` (checks filters `subtitles/amix/alimiter/
sidechaincompress`, encoder `libx264`, and the bundled font rendering
Vietnamese). When you fetch a fresh FFmpeg build, record its download URL
and the checksum in `packaging/THIRD_PARTY.md` — the build that produced
1.0.0 used a locally installed build and its upstream origin was not
re-verified.

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
