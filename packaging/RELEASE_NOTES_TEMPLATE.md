# DubTool v{version} (Update {update})

## Tiếng Việt

- **Phiên bản:** `{version}` (Update `{update}`)
- **Commit (`HEAD`):** `{commit}`
- **Tệp phát hành:** `{zip_name}`
- **SHA-256 (`{zip_name}`):** `{zip_sha256}`
- **Chữ ký số:** Chưa có chữ ký số (Windows SmartScreen có thể cảnh báo khi chạy lần đầu — bấm *More info* → *Run anyway*).
- **Trạng thái giấy phép ứng dụng:** **MIT License** (`Copyright (c) 2026 sickyturtlez`) cho mã nguồn của ứng dụng DubTool; các thành phần bên thứ ba giữ nguyên giấy phép riêng (xem `THIRD_PARTY.md` và thư mục `licenses/`).
- **Lưu ý dịch vụ & quyền riêng tư:**
  - Âm thanh, video, nhận diện tiếng nói (Whisper) và tạo giọng đọc tiếng Việt (**VieNeu-TTS v3 ONNX**) đều chạy **100% cục bộ trên máy của bạn** (không còn phụ thuộc `edge-tts` hay dịch vụ đọc trực tuyến bên thứ ba). Lần chạy đầu tiên cần Internet để tải mô hình Whisper và VieNeu-TTS (~244–269 MB) từ Hugging Face.
  - Khi bạn chọn mô hình dịch (bao gồm chế độ xoay tua tự động miễn phí Gemini + Groq, hoặc các nhà cung cấp trả phí như OpenAI / Anthropic), **văn bản lời thoại nguồn cùng ngữ cảnh tùy chọn bạn điền (Brief, Glossary) sẽ được gửi tới nhà cung cấp đó** để dịch sang tiếng Việt.

### Phần mềm bên thứ ba & Giấy phép (Third-Party Notices)

Bản đóng gói kèm theo các thành phần bên thứ ba sau đây (toàn văn giấy phép nằm trong thư mục `licenses/` và tệp `THIRD_PARTY.md` bên trong gói `.zip`):

1. **FFmpeg (`_internal/vendor/ffmpeg.exe`) — Giấy phép GNU GPL v3 (`licenses/GPL-3.0.txt`):**
   - Chuỗi phiên bản (`ffmpeg -version`):
     ```text
     ffmpeg version N-118281-gb76053d8bf-20250111 Copyright (c) 2000-2025 the FFmpeg developers
     built with gcc 14.2.0 (crosstool-NG 1.26.0.120_4d36f27)
     ```
   - Dòng cấu hình `--enable-gpl`:
     ```text
     configuration: --prefix=/ffbuild/prefix --pkg-config-flags=--static --pkg-config=pkg-config --cross-prefix=x86_64-w64-mingw32- --arch=x86_64 --target-os=mingw32 --enable-gpl --enable-version3 --disable-debug --disable-w32threads --enable-pthreads --enable-iconv --enable-zlib --enable-libfreetype --enable-libfribidi --enable-gmp --enable-libxml2 --enable-lzma --enable-fontconfig --enable-libharfbuzz --enable-libvorbis --enable-opencl --disable-libpulse --enable-libvmaf --disable-libxcb --disable-xlib --enable-amf --enable-libaom --enable-libaribb24 --enable-avisynth --enable-chromaprint --enable-libdav1d --enable-libdavs2 --enable-libdvdread --enable-libdvdnav --disable-libfdk-aac --enable-ffnvcodec --enable-cuda-llvm --enable-frei0r --enable-libgme --enable-libkvazaar --enable-libaribcaption --enable-libass --enable-libbluray --enable-libjxl --enable-libmp3lame --enable-libopus --enable-librist --enable-libssh --enable-libtheora --enable-libvpx --enable-libwebp --enable-libzmq --enable-lv2 --enable-libvpl --enable-openal --enable-libopencore-amrnb --enable-libopencore-amrwb --enable-libopenh264 --enable-libopenjpeg --enable-libopenmpt --enable-librav1e --enable-librubberband --enable-schannel --enable-sdl2 --enable-libsoxr --enable-libsrt --enable-libsvtav1 --enable-libtwolame --enable-libuavs3d --disable-libdrm --enable-vaapi --enable-libvidstab --enable-vulkan --enable-libshaderc --enable-libplacebo --disable-libvvenc --enable-libx264 --enable-libx265 --enable-libxavs2 --enable-libxvid --enable-libzimg --enable-libzvbi --extra-cflags=-DLIBTWOLAME_STATIC --extra-cxxflags= --extra-libs=-lgomp --extra-ldflags=-pthread --extra-ldexeflags= --cc=x86_64-w64-mingw32-gcc --cxx=x86_64-w64-mingw32-g++ --ar=x86_64-w64-mingw32-gcc-ar --ranlib=x86_64-w64-mingw32-gcc-ranlib --nm=x86_64-w64-mingw32-gcc-nm --extra-version=20250111
     ```
   - URL bản dựng FFmpeg đã tải: `https://github.com/BtbN/FFmpeg-Builds/releases` (`ffmpeg-N-118281-gb76053d8bf-win64-gpl.zip`)
   - URL mã nguồn FFmpeg tương ứng: `https://github.com/FFmpeg/FFmpeg/tree/b76053d8bf` (`https://ffmpeg.org/download.html#repositories`)
2. **Phông chữ Noto Sans (`_internal/vendor/fonts/NotoSans.ttf`):** SIL Open Font License 1.1 (`licenses/OFL-NotoSans.txt`).
3. **PySide6-Essentials (Qt for Python) & `libsndfile` (`soundfile`):** GNU LGPL v3 / LGPL-2.1+ / BSD-3 (`licenses/LGPL-3.0.txt`, `licenses/BSD-3-PyAV.txt`).
4. **faster-whisper, ctranslate2, onnxruntime, anthropic, keyring, python-docx, pydub, pydantic, pyyaml, python-dotenv, psutil, tqdm:** MIT / BSD-3 (`licenses/MIT-faster-whisper.txt`, `licenses/BSD-3-PyAV.txt`).
5. **VieNeu-TTS (`vieneu`), `sea-g2p`, MOSS-Audio-Tokenizer-Nano-ONNX, google-genai, openai, huggingface_hub, tokenizers:** Apache License 2.0 (`licenses/Apache-2.0.txt`).
6. **Danh sách tệp trong `licenses/` của gói zip:**
   - `licenses/GPL-3.0.txt`
   - `licenses/LGPL-3.0.txt`
   - `licenses/OFL-NotoSans.txt`
   - `licenses/Apache-2.0.txt`
   - `licenses/MIT-faster-whisper.txt`
   - `licenses/BSD-3-PyAV.txt`
   - `licenses/THIRD_PARTY_NOTICES.txt`

---

## English

- **Version:** `{version}` (Update `{update}`)
- **Commit (`HEAD`):** `{commit}`
- **Release Asset:** `{zip_name}`
- **SHA-256 (`{zip_name}`):** `{zip_sha256}`
- **Code Signing:** Unsigned binary (Windows SmartScreen may show a warning on first launch — click *More info* → *Run anyway*).
- **Application License Status:** **MIT License** (`Copyright (c) 2026 sickyturtlez`) for DubTool's own application source code; bundled third-party components keep their own licenses (see `THIRD_PARTY.md` and `licenses/`).
- **Service & Privacy Notes:**
  - Audio extraction, speech recognition (Whisper), and Vietnamese voice synthesis (**VieNeu-TTS v3 ONNX**) all run **100% locally on your machine** (no `edge-tts` or online TTS service dependency). An internet connection is needed on the first run to download local Whisper and VieNeu-TTS ONNX weights (~244–269 MB) from Hugging Face.
  - When translating (including with the free Auto-Rotate Gemini + Groq pool, or paid providers such as OpenAI or Anthropic), **recognized source-language transcript text and optional user-provided context (video briefs and glossary entries) are sent to the selected provider**.

### Third-Party Components & Licenses

Bundled third-party components and their license locations inside the `.zip` archive (`licenses/` and `THIRD_PARTY.md`):

1. **FFmpeg (`_internal/vendor/ffmpeg.exe`) — GNU GPL v3 (`licenses/GPL-3.0.txt`):**
   - Exact `ffmpeg -version` string: `ffmpeg version N-118281-gb76053d8bf-20250111 Copyright (c) 2000-2025 the FFmpeg developers` (`built with gcc 14.2.0 (crosstool-NG 1.26.0.120_4d36f27)`), built with `--enable-gpl --enable-version3` (full configuration line above).
   - Downloaded build URL: `https://github.com/BtbN/FFmpeg-Builds/releases` (`ffmpeg-N-118281-gb76053d8bf-win64-gpl.zip`)
   - Upstream source URL: `https://github.com/FFmpeg/FFmpeg/tree/b76053d8bf` (`https://ffmpeg.org/download.html#repositories`)
2. **Noto Sans Font (`_internal/vendor/fonts/NotoSans.ttf`):** SIL Open Font License 1.1 (`licenses/OFL-NotoSans.txt`).
3. **PySide6-Essentials & `libsndfile` (`soundfile`):** GNU LGPL v3 / LGPL-2.1+ / BSD-3 (`licenses/LGPL-3.0.txt`, `licenses/BSD-3-PyAV.txt`).
4. **VieNeu-TTS (`vieneu`), `sea-g2p`, MOSS-Audio-Tokenizer-Nano-ONNX, faster-whisper, ctranslate2, onnxruntime, PyAV, google-genai, openai, anthropic, keyring, python-docx, certifi:** Apache-2.0 / MIT / BSD-3 / MPL-2.0 (`licenses/Apache-2.0.txt`, `licenses/MIT-faster-whisper.txt`, `licenses/BSD-3-PyAV.txt`, `licenses/THIRD_PARTY_NOTICES.txt`).
