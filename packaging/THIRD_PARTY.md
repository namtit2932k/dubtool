# THIRD_PARTY.md — bundled software (DubTool for Windows)

Everything below is bundled or installed by the build. Licenses are the
authoritative texts in each project's repository and in the `licenses/`
folder shipped at the root of the DubTool package (`DubTool/licenses/`).

## Application license status

- **Trạng thái giấy phép ứng dụng / Application license status:** Chưa chọn giấy phép — bảo lưu mọi quyền (*No license selected yet — all rights reserved*), chờ quyết định từ chủ sở hữu dự án.
- Lưu ý: Tài liệu này ghi nhận thông tin giấy phép phần mềm bên thứ ba để đóng khoảng trống tài liệu kỹ thuật, không phải ý kiến tư vấn pháp lý (*documentation gap closed, not a legal opinion*).

## FFmpeg (`_internal/vendor/ffmpeg.exe`)

- **Exact `ffmpeg -version` string:**
  ```text
  ffmpeg version N-118281-gb76053d8bf-20250111 Copyright (c) 2000-2025 the FFmpeg developers
  built with gcc 14.2.0 (crosstool-NG 1.26.0.120_4d36f27)
  ```
- **Exact `--enable-gpl` configuration line:**
  ```text
  configuration: --prefix=/ffbuild/prefix --pkg-config-flags=--static --pkg-config=pkg-config --cross-prefix=x86_64-w64-mingw32- --arch=x86_64 --target-os=mingw32 --enable-gpl --enable-version3 --disable-debug --disable-w32threads --enable-pthreads --enable-iconv --enable-zlib --enable-libfreetype --enable-libfribidi --enable-gmp --enable-libxml2 --enable-lzma --enable-fontconfig --enable-libharfbuzz --enable-libvorbis --enable-opencl --disable-libpulse --enable-libvmaf --disable-libxcb --disable-xlib --enable-amf --enable-libaom --enable-libaribb24 --enable-avisynth --enable-chromaprint --enable-libdav1d --enable-libdavs2 --enable-libdvdread --enable-libdvdnav --disable-libfdk-aac --enable-ffnvcodec --enable-cuda-llvm --enable-frei0r --enable-libgme --enable-libkvazaar --enable-libaribcaption --enable-libass --enable-libbluray --enable-libjxl --enable-libmp3lame --enable-libopus --enable-librist --enable-libssh --enable-libtheora --enable-libvpx --enable-libwebp --enable-libzmq --enable-lv2 --enable-libvpl --enable-openal --enable-libopencore-amrnb --enable-libopencore-amrwb --enable-libopenh264 --enable-libopenjpeg --enable-libopenmpt --enable-librav1e --enable-librubberband --enable-schannel --enable-sdl2 --enable-libsoxr --enable-libsrt --enable-libsvtav1 --enable-libtwolame --enable-libuavs3d --disable-libdrm --enable-vaapi --enable-libvidstab --enable-vulkan --enable-libshaderc --enable-libplacebo --disable-libvvenc --enable-libx264 --enable-libx265 --enable-libxavs2 --enable-libxvid --enable-libzimg --enable-libzvbi --extra-cflags=-DLIBTWOLAME_STATIC --extra-cxxflags= --extra-libs=-lgomp --extra-ldflags=-pthread --extra-ldexeflags= --cc=x86_64-w64-mingw32-gcc --cxx=x86_64-w64-mingw32-g++ --ar=x86_64-w64-mingw32-gcc-ar --ranlib=x86_64-w64-mingw32-gcc-ranlib --nm=x86_64-w64-mingw32-gcc-nm --extra-version=20250111
  ```
- **Downloaded FFmpeg build URL:** `https://github.com/BtbN/FFmpeg-Builds/releases` (`ffmpeg-N-118281-gb76053d8bf-win64-gpl.zip`)
- **FFmpeg upstream source URL:** `https://github.com/FFmpeg/FFmpeg/tree/b76053d8bf` (`https://ffmpeg.org/download.html#repositories`)
- **License:** GNU General Public License v3.0 (`--enable-gpl --enable-version3`). Full license text is included in `licenses/GPL-3.0.txt`.

## Noto Sans (bundled subtitle font)

- **File:** `_internal/vendor/fonts/NotoSans.ttf` (variable font, includes Vietnamese).
- **Source:** `https://raw.githubusercontent.com/google/fonts/main/ofl/notosans/NotoSans[wdth,wght].ttf`
- **SHA-256:** `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d`
- **License:** SIL Open Font License 1.1 (`licenses/OFL-NotoSans.txt` and `_internal/vendor/fonts/OFL.txt`).

## Python runtime and packages

The one-dir bundle embeds these libraries; each keeps its own license:

| Package | License | License text in zip | Notes |
| --- | --- | --- | --- |
| PySide6-Essentials | LGPL-3.0 | `licenses/LGPL-3.0.txt` | Qt for Python; bundled as dynamic libraries |
| faster-whisper | MIT | `licenses/MIT-faster-whisper.txt` | Includes Silero VAD assets |
| ctranslate2 | MIT | `licenses/MIT-faster-whisper.txt` | Inference runtime for Whisper |
| onnxruntime | MIT | `licenses/MIT-faster-whisper.txt` | Used for Silero VAD |
| PyAV (`av`) | BSD-3 / LGPL / GPL | `licenses/BSD-3-PyAV.txt` | FFmpeg bindings |
| tokenizers | Apache-2.0 | `licenses/Apache-2.0.txt` | |
| huggingface_hub | Apache-2.0 | `licenses/Apache-2.0.txt` | |
| certifi | MPL-2.0 | `licenses/THIRD_PARTY_NOTICES.txt` | TLS CA bundle |
| edge-tts | LGPL-3.0 / MIT | `licenses/LGPL-3.0.txt` | Unofficial interface to Microsoft Read Aloud service |
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
