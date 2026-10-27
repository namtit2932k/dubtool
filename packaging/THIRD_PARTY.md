# THIRD_PARTY.md — bundled software (DubTool 1.0.0 for Windows)

Everything below is bundled or installed by the build. Licenses are the
authoritative texts in each project's repository; the table summarizes.

## FFmpeg / ffprobe

- Version: `N-118281-gb76053d8bf-20250111` (Windows 64-bit build).
- Source: bundled from `vendor/` at build time. Record the exact download URL
  and its publisher's checksum when you fetch a fresh build yourself.
- What it must include: `libass` (`subtitles` filter), `amix`, `alimiter`,
  `sidechaincompress` filters and the `libx264` encoder (verified during the
  build by the app's `--selftest`).
- License note: GPL-licensed FFmpeg builds carry GPL obligations if DubTool
  is ever redistributed. Combined works that call FFmpeg as a separate
  process are generally unaffected, but redistribution of this folder
  together with a GPL FFmpeg triggers the GPL.
- On this machine the build was packaged from a locally installed FFmpeg
  (`C:\ffmpeg`); its exact upstream origin could not be re-verified here —
  see README_BUILD.md ("FFmpeg verification").

## Noto Sans (bundled subtitle font)

- File: `vendor/fonts/NotoSans.ttf` (variable font, includes Vietnamese).
- Source: `https://raw.githubusercontent.com/google/fonts/main/ofl/notosans/NotoSans[wdth,wght].ttf`
  (fetched 2026-10-27).
- SHA-256: `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d`
- License: SIL Open Font License 1.1 (`vendor/fonts/OFL.txt`, bundled).

## Python runtime and packages (installed into the build, not shipped here)

The one-dir bundle embeds these libraries; each keeps its own license:

| Package | License | Notes |
| --- | --- | --- |
| PySide6 | LGPL-3.0 | Qt for Python; bundled as dynamic libraries |
| faster-whisper | MIT | includes Silero VAD assets |
| ctranslate2 | MIT | |
| onnxruntime | MIT | |
| PyAV (`av`) | LGPL/GPL | FFmpeg bindings |
| tokenizers | Apache-2.0 | |
| huggingface_hub | Apache-2.0 | |
| certifi | MPL-2.0 | TLS CA bundle used for Gemini/edge-tts/HF |
| edge-tts | GPL-3.0 | Piper-like TTS text endpoints; invoked as a library |
| google-genai | Apache-2.0 | Gemini SDK |
| anthropic | MIT | |
| keyring | MIT | Windows Credential Manager backend |
| python-docx (+ lxml) | MIT / BSD-3 | AGU7 task 4: the customer-readable translation document; its default template ships as bundled data |
| pydub / pydantic / pyyaml / python-dotenv | MIT / MIT / MIT / BSD-3 | |
| tqdm / colorama | MIT / BSD-3 | |
| PyInstaller (build only) | GPL with boot-loader exception | not redistributed |
| pytest / pytest-qt (build only) | MIT | not redistributed |

## Application code

DubTool itself is MIT. This machine-generated icon (`packaging/icon.png`)
is created from the DubTool repository and carries no third-party license.

## Reminder

Only process video you own or have permission to translate and dub; the app
README states this as well.
