# THIRD_PARTY.md — bundled software (DubTool for Windows)

Everything below is bundled or installed by the build. Licenses are the
authoritative texts in each project's repository and in the `licenses/`
folder shipped at the root of the DubTool package (`DubTool/licenses/`).

## Application license status

- **Trạng thái giấy phép ứng dụng / Application license status:** **MIT License** (`Copyright (c) 2026 sickyturtlez`, toàn văn trong tệp `LICENSE`) áp dụng cho mã nguồn của ứng dụng DubTool; các thành phần bên thứ ba đi kèm giữ nguyên giấy phép riêng của từng dự án bên dưới.
- Lưu ý: Tài liệu này ghi nhận thông tin giấy phép phần mềm bên thứ ba để đóng khoảng trống tài liệu kỹ thuật, không phải ý kiến tư vấn pháp lý (*documentation gap closed, not a legal opinion*).

## FFmpeg (`_internal/vendor/ffmpeg.exe`)

- **Exact `ffmpeg -version` string:**
  ```text
  ffmpeg version 8.1.2-essentials_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
  built with gcc 16.1.0 (Rev2, Built by MSYS2 project)
  ```
- **Exact `--enable-gpl` configuration line:**
  ```text
  configuration: --enable-gpl --enable-version3 --enable-static --disable-w32threads --disable-autodetect --enable-cairo --enable-fontconfig --enable-iconv --enable-gnutls --enable-libxml2 --enable-gmp --enable-bzlib --enable-lzma --enable-zlib --enable-libsrt --enable-libssh --enable-libzmq --enable-avisynth --enable-sdl2 --enable-libwebp --enable-libx264 --enable-libx265 --enable-libxvid --enable-libaom --enable-libopenjpeg --enable-libvpx --enable-mediafoundation --enable-libass --enable-libfreetype --enable-libfribidi --enable-libharfbuzz --enable-libvidstab --enable-libvmaf --enable-libzimg --enable-amf --enable-cuda-llvm --enable-cuvid --enable-dxva2 --enable-d3d11va --enable-d3d12va --enable-ffnvcodec --enable-libvpl --enable-nvdec --enable-nvenc --enable-vaapi --enable-openal --enable-libgme --enable-libopenmpt --enable-libopencore-amrwb --enable-libmp3lame --enable-libtheora --enable-libvo-amrwbenc --enable-libgsm --enable-libopencore-amrnb --enable-libopus --enable-libspeex --enable-libvorbis --enable-librubberband
  ```
- **Downloaded FFmpeg build URL:** `https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/ffmpeg-8.1.2-essentials_build.zip` (ZIP SHA-256: `db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`)
- **Bundled `ffmpeg.exe` SHA-256:** `1326dde4c84ff1f96fe6b8916c5bed29e163e9b5dccf995f6f3db069d143ec5e` (pinned in `packaging/vendor_manifest.json`)
- **FFmpeg upstream source URL:** `https://github.com/FFmpeg/FFmpeg/commit/38b88335f9` (`https://ffmpeg.org/download.html#repositories`)
- **License:** GNU General Public License v3.0 (`--enable-gpl --enable-version3`). Full license text is included in `licenses/GPL-3.0.txt`.

## Noto Sans (bundled subtitle font)

- **File:** `_internal/vendor/fonts/NotoSans.ttf` (variable font, includes Vietnamese).
- **Source:** `https://raw.githubusercontent.com/google/fonts/2984c575fdce412ee02b2baaba67672b9a9434d8/ofl/notosans/NotoSans%5Bwdth,wght%5D.ttf` (pinned commit from `google/fonts` `main`)
- **SHA-256:** `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d`
- **License:** SIL Open Font License 1.1 (`licenses/OFL-NotoSans.txt` and `_internal/vendor/fonts/OFL.txt`, SHA-256 `cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a`).

## Python runtime and packages

The one-dir bundle embeds these libraries; each keeps its own license:

| Package | License | License text in zip | Notes |
| --- | --- | --- | --- |
| PySide6-Essentials | LGPL-3.0 | `licenses/LGPL-3.0.txt` | Qt for Python; bundled as dynamic libraries |
| faster-whisper | MIT | `licenses/MIT-faster-whisper.txt` | Includes Silero VAD assets |
| ctranslate2 | MIT | `licenses/MIT-faster-whisper.txt` | Inference runtime for Whisper |
| onnxruntime | MIT | `licenses/MIT-faster-whisper.txt` | Used for Silero VAD & VieNeu-TTS v3 ONNX inference |
| PyAV (`av`) | BSD-3 / LGPL / GPL | `licenses/BSD-3-PyAV.txt` | FFmpeg bindings |
| tokenizers | Apache-2.0 | `licenses/Apache-2.0.txt` | |
| huggingface_hub | Apache-2.0 | `licenses/Apache-2.0.txt` | |
| certifi | MPL-2.0 | `licenses/THIRD_PARTY_NOTICES.txt` | TLS CA bundle |
| VieNeu-TTS (`vieneu`, `vieneu_utils`) | Apache-2.0 | `licenses/Apache-2.0.txt` | Offline Vietnamese TTS engine (v3 Turbo INT8 & v3 Nano; ONNX weights downloaded on demand from Hugging Face, including `OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX`, Apache-2.0) |
| sea-g2p | Apache-2.0 | `licenses/Apache-2.0.txt` | Vietnamese grapheme-to-phoneme frontend for VieNeu-TTS |
| soundfile (+ `libsndfile`) | BSD-3 / LGPL-2.1+ | `licenses/BSD-3-PyAV.txt`, `licenses/LGPL-3.0.txt` | PCM WAV I/O for local TTS synthesis |
| google-genai | Apache-2.0 | `licenses/Apache-2.0.txt` | Gemini SDK |
| openai | Apache-2.0 | `licenses/Apache-2.0.txt` | OpenAI Responses API SDK |
| anthropic | MIT | `licenses/MIT-faster-whisper.txt` | Anthropic Claude SDK |
| keyring | MIT | `licenses/MIT-faster-whisper.txt` | Windows Credential Manager backend |
| python-docx (+ lxml) | MIT / BSD-3 | `licenses/MIT-faster-whisper.txt` | Readable translation `.docx` export |
| pydub / pydantic / pyyaml / python-dotenv / psutil / tqdm / colorama | MIT / BSD-3 | `licenses/MIT-faster-whisper.txt` | |
| PyInstaller (build only) | GPL-2.0+ with Bootloader Exception | Not redistributed as source | Build tool |

## Files in `licenses/` inside the release zip

- `licenses/GPL-3.0.txt`
- `licenses/LGPL-3.0.txt`
- `licenses/OFL-NotoSans.txt`
- `licenses/Apache-2.0.txt`
- `licenses/MIT-faster-whisper.txt`
- `licenses/BSD-3-PyAV.txt`
- `licenses/THIRD_PARTY_NOTICES.txt`
