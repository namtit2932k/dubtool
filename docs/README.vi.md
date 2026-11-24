<p align="center">
  <img src="images/logo.png" width="120" alt="DubTool" />
</p>

<h1 align="center">DubTool</h1>

<p align="center">
  <strong>Ứng dụng lồng tiếng video đa ngôn ngữ sang tiếng Việt cho Windows — nhận diện giọng nói Whisper cục bộ, xoay tua tự động toàn bộ API Key miễn phí (Gemini + Groq), giọng đọc VieNeu-TTS v3 offline, trộn âm thanh streaming và gắn phụ đề đồng bộ.</strong>
</p>

<p align="center">
  <a href="https://github.com/namtit2932k/dubtool/releases/latest"><img src="https://img.shields.io/github/v/release/namtit2932k/dubtool?label=Release&color=e05d44" alt="Phiên bản mới nhất" /></a>
  <a href="https://github.com/namtit2932k/dubtool/actions/workflows/ci.yml"><img src="https://github.com/namtit2932k/dubtool/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/License-MIT-2ea043" alt="Giấy phép MIT" /></a>
  <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-0969da" alt="Windows 10/11 x64" />
</p>

<p align="center">
  <a href="../README.md">English</a> · <strong>Tiếng Việt</strong>
</p>

<p align="center">
  <a href="https://github.com/namtit2932k/dubtool/releases/latest"><img src="https://img.shields.io/badge/%E2%86%93%20T%E1%BA%A2I%20V%E1%BB%80-WINDOWS%2010%2F11%20X64-2ea043?style=for-the-badge&labelColor=555555" alt="Tải về cho Windows 10/11 x64" /></a>
</p>

---

**DubTool** là ứng dụng desktop mã nguồn mở cho Windows (Python + PySide6, đóng gói sẵn thành bản `.exe` portable) giúp chuyển đổi bất kỳ video ngoại ngữ nào — từ clip ngắn đến phim bộ, bài giảng dài nhiều giờ — thành video lồng tiếng Việt hoàn chỉnh mà không cần canh phụ đề thủ công.

Ứng dụng tự động nhận diện ngôn ngữ nói của video (hoặc dùng ngôn ngữ bạn chọn tay), nhận diện lời thoại **100% cục bộ** bằng `faster-whisper`, dịch sang tiếng Việt tự nhiên qua chế độ **Tối ưu — Xoay tua toàn bộ API Key miễn phí (Gemini + Groq)** hoặc mô hình tùy chọn (**Gemini**, **Groq**, **OpenAI**, **Anthropic**, **Ollama** cục bộ), tạo giọng đọc tiếng Việt **100% offline** bằng **VieNeu-TTS v3 ONNX**, căn chỉnh khớp thời lượng từng câu, trộn streaming đè lên nền gốc đã giảm âm lượng kèm limiter chống rè, gắn phụ đề tiếng Việt chuẩn xác theo thời điểm phát thực tế và xuất kèm tệp bản dịch dễ đọc `.docx`/`.txt`.

Nếu video đầu vào đã là tiếng Việt, DubTool sẽ tự dừng trước bước dịch và đề xuất xuất phụ đề `.srt` + bản chép lời mà không gọi API dịch.

> ⚖️ Chỉ sử dụng cho video bạn sở hữu hoặc có quyền chỉnh sửa. Khi tải và sử dụng DubTool, bạn đồng ý với [Giấy phép MIT](../LICENSE) và các [thông báo bên thứ ba](../packaging/THIRD_PARTY.md).

<p align="center">
  <img src="images/ready.png" alt="Giao diện chính DubTool" />
</p>

---

## Tính năng nổi bật

- **Chế độ mặc định `Tối ưu — Xoay tua toàn bộ API Key (Miễn phí)` (`auto-rotate`):** Tự động xoay vòng đan xen **toàn bộ** API key miễn phí **Gemini Flash-Lite** và **Groq (`qwen/qwen3.8-27b`)** trên nhiều luồng song song (`2` luồng mặc định, tự nâng lên `3` luồng khi có `>= 4` khóa miễn phí), cooldown `503` thích ứng (`15s → 30s → 45s`) và chuyển khóa/hãng tức thì trong `0 ms`. Tuyệt đối **không** tự dùng khóa trả phí (OpenAI / Anthropic).
- **Đa nhà cung cấp dịch & kiểm soát chất lượng:** Hỗ trợ **Gemini**, **Groq Cloud** (miễn phí, tốc độ cao, nén prompt `-67%`, xoay nhiều khóa qua `KeyPool`), **OpenAI**, **Anthropic** và **Ollama** (cục bộ), kèm fallback chéo hãng khi quá tải, ô **Mô tả ngắn (Brief)** để định hướng bối cảnh/xưng hô (vd: *"bối cảnh cổ trang trung quốc, xưng hô cổ trang"*), từ điển thuật ngữ (`glossary`) và bộ lọc tự động phát hiện & dịch lại nếu sót ký tự Hán/Kana/Hangul.
- **Nhận diện & Lồng tiếng 100% cục bộ (Offline CPU):**
  - **Nhận diện tiếng nói (ASR):** `faster-whisper` có VAD, tự sửa lỗi trôi timestamp & tách khoảng lặng nội bộ (`>= 1.5s`), bấm **Hủy** dừng ngay giữa chừng và tự động **tái sử dụng kết quả S1/S2** khi bạn đổi mô hình dịch.
  - **Giọng đọc tiếng Việt (TTS):** **VieNeu-TTS v3 ONNX** chạy hoàn toàn trên CPU — `v3turbo_int8` (`48 kHz`, 25 giọng tự nhiên, mặc định *Trúc Ly*) hoặc `v3nano` (`24 kHz`, 11 giọng, chỉ ~480 MB RAM), kèm nút **Nghe thử giọng** ngay trong app. Chỉ tải mô hình 1 lần (~244–269 MB) và dùng offline vĩnh viễn.
- **Trộn âm thanh streaming cực nhanh & khớp thời lượng mượt:**
  - **S5 (Khớp thời lượng):** Căn khớp từng câu theo khoảng trống thực tế bằng giãn tốc nhẹ (`atempo`) hoặc tự rút gọn câu dài bằng LLM + đọc lại (đạt ~97% khớp tự nhiên `1.0×` và `0` tràn câu trên video thực tế dài 2.6 tiếng).
  - **S6 (Streaming Mixer):** Bộ trộn `soundfile` + `numpy` theo khối 30 giây (RAM cố định ~20 MB, không tạo file trung gian hàng GB, tự bật RF64 khi >3.8 GB, nhanh hơn **~15×** trên video nhiều giờ), hạ nền gốc (`−14 dB`) và áp dụng `alimiter` chống méo tiếng.
- **An toàn, tiếp tục tức thì & không ghi đè file gốc:**
  - Tuyệt đối không sửa hay ghi đè video gốc.
  - Lưu tiến độ từng bước **và từng cụm dịch** — hủy lúc nào cũng chạy tiếp (`Tạo`) đúng từ điểm dừng mà không mất công đoạn cũ.
  - Xuất kèm video `.mp4`, `.srt` (tùy chọn) và bản dịch song ngữ `.docx`/`.txt`, hỗ trợ mở và chọn thẳng tệp trong Windows Explorer.
- **Bảo mật khóa API:** Lưu trong **Windows Credential Manager** (hoặc `.env` khi dùng CLI), hiển thị che ký tự trên giao diện, tự lọc khỏi toàn bộ log/báo cáo lỗi và không bao giờ ghi ra `settings.json` hay thư mục job.

---

## Kiến trúc Pipeline (7 giai đoạn có thể tiếp tục)

| Giai đoạn | Nhiệm vụ |
| --- | --- |
| **s1** Tách âm thanh | Trích xuất WAV mono 16 kHz từ video đầu vào bằng FFmpeg đóng gói sẵn |
| **s2** Nhận diện giọng nói | `faster-whisper` cục bộ tự phát hiện ngôn ngữ, VAD, sửa timestamp từ và hỗ trợ hủy ngay → `transcript.json` |
| **s3** Dịch sang tiếng Việt | Dịch theo câu hoàn chỉnh kèm ngữ cảnh, Brief, glossary, đồng nhất xưng hô, chặn sót chữ Hán/CJK, checkpoint từng chunk và xoay tua Auto-Rotate |
| **s4** Tạo giọng đọc (TTS) | VieNeu-TTS v3 ONNX offline (`v3turbo_int8` 48 kHz / `v3nano` 24 kHz), tự cắt khoảng lặng thừa và cache WAV từng câu |
| **s5** Khớp thời lượng | Căn thời lượng từng đoạn vừa khung bằng `atempo` nhẹ hoặc rút gọn LLM + tạo lại giọng, không để câu đè nhau |
| **s6** Trộn & ghép âm | Trộn streaming 30 giây bằng `soundfile` + `numpy` (hỗ trợ RF64), giảm nền gốc (`−14 dB`) và áp `alimiter` chống clipping |
| **s7** Phụ đề & xuất tệp | Xuất `.srt` khớp mốc phát thật, gắn phụ đề tiếng Việt (`libass` + Noto Sans) và xuất tệp bản dịch `.docx`/`.txt` |

---

## Ảnh chụp màn hình

| Cửa sổ chính (Sẵn sàng) | Chọn & tự động nhận diện ngôn ngữ |
| :---: | :---: |
| ![Giao diện chính — sẵn sàng chọn ngôn ngữ và mô hình](images/ready.png) | ![Chọn ngôn ngữ video — Tự động, ngôn ngữ phổ biến và Khác…](images/lang_dropdown_open.png) |

| Tiến trình & độ tin cậy ngôn ngữ | Đang chạy & nút Hủy tức thì |
| :---: | :---: |
| ![Đang chạy — hiển thị ngôn ngữ nhận diện và độ tin cậy](images/details_detected_language.png) | ![Đang chạy — thanh tiến trình, nút hủy và nhật ký trực tiếp](images/running.png) |

| Hoàn tất & mở nhanh đầu ra | Quản lý khóa API an toàn |
| :---: | :---: |
| ![Hoàn tất — mở thư mục xuất, phát video hoặc mở bản dịch](images/finished.png) | ![Cài đặt — lưu khóa API an toàn trong Windows Credential Manager](images/settings_api_keys.png) |

---

## Cài đặt & Sử dụng nhanh

### Cách 1: Bản Portable cho Windows (Khuyên dùng)

1. Tải tệp `DubTool-*-win64.zip` mới nhất tại [GitHub Releases](https://github.com/namtit2932k/dubtool/releases/latest) và giải nén.
2. Mở **`DubTool.exe`** (đã đóng gói sẵn FFmpeg và phông chữ tiếng Việt Noto Sans, không cần cài Python).
3. Vào **Cài đặt → Khóa API** và thêm 1 hoặc nhiều khóa miễn phí:
   - [Google AI Studio](https://aistudio.google.com/apikey) (**Gemini** miễn phí), và/hoặc
   - [Groq Console](https://console.groq.com/keys) (**Groq** miễn phí — rất nhanh, tự ghép cùng Gemini trong chế độ mặc định **Tối ưu — Xoay tua toàn bộ API Key**).
   - *(Tùy chọn)* Thêm khóa **OpenAI** hoặc **Anthropic** nếu muốn dùng mô hình trả phí.
4. Kéo thả video vào cửa sổ, có thể điền 1 dòng **Mô tả ngắn (Brief)** (vd: *"bối cảnh cổ trang trung quốc, xưng hô cổ trang"*), chọn thư mục xuất và bấm **Tạo**.
   - *Chỉ lần chạy đầu:* Mô hình Whisper và VieNeu-TTS ONNX (~244–269 MB) sẽ tự động tải về `%LOCALAPPDATA%\DubTool\models\hf_cache`.

### Cách 2: Chạy từ mã nguồn / dòng lệnh (CLI)

Yêu cầu **Python 3.11+** (khuyên dùng 3.13) và **FFmpeg trong `PATH`**.

```bash
pip install -e ".[gui,dev]"             # CLI + Giao diện Desktop (PySide6-Essentials, keyring) + pytest
pip install --no-deps "vieneu>=3.8,<4"  # VieNeu-TTS v3 ONNX offline (không kéo thư viện WebUI/gradio thừa)
copy .env.example .env                  # điền GEMINI_API_KEY=... và/hoặc GROQ_API_KEY=...
dubtool doctor                          # kiểm tra FFmpeg, khóa API và thư viện
```

**Mở giao diện desktop từ mã nguồn:**

```bash
python -m dubtool.app.main
```

**Chạy bằng dòng lệnh (CLI):**

```bash
dubtool run video.mp4 --source-language auto --model auto-rotate --tts-mode v3turbo_int8 --voice "Trúc Ly"
dubtool stage <job_id> <s1..s7> [--force]
dubtool status <job_id>
dubtool retts <job_id> --ids 3,7,12
```

- Truyền `--brief "nội dung"` hoặc đặt tệp `<ten_video>.brief.txt` cạnh video để định hướng văn phong/xưng hô.
- Sao chép `glossary.example.json` → `glossary.json` để cố định thuật ngữ (điền `"keep original"` để giữ nguyên từ gốc).
- Xem hướng dẫn đóng gói `DubTool.exe` tại [README_BUILD.md](../README_BUILD.md).

---

## Mô hình dịch & Nhà cung cấp

| Nhà cung cấp | Khóa / Cấu hình | Ghi chú |
| --- | --- | --- |
| **Tự động tối ưu** *(mặc định)* | Khóa Gemini + Groq | Xoay vòng miễn phí toàn bộ khóa Gemini Flash-Lite & Groq `qwen/qwen3.8-27b`; không chạm khóa trả phí |
| **Gemini** | `GEMINI_API_KEY` | Gói miễn phí (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-flash-lite-latest`, `3.6`/`3.8-flash`); ép cấu trúc `json_schema` |
| **Groq Cloud** | `GROQ_API_KEY` | Gói miễn phí (`qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`); phản hồi nhanh, xoay vòng đa khóa |
| **OpenAI** | `OPENAI_API_KEY` | Trả phí (`gpt-5.4-nano`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`) — hỏi xác nhận trước lần chạy đầu |
| **Anthropic** | `ANTHROPIC_API_KEY` | Trả phí (`claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`) — hỏi xác nhận trước lần chạy đầu |
| **Ollama** | `llm_provider: ollama` | 100% cục bộ & riêng tư; chất lượng tùy mô hình máy bạn chạy |

> **Mẹo cho video dài nhiều giờ:** Thêm từ 2 khóa Gemini miễn phí (từ các Google Cloud project khác nhau) kết hợp 1–2 khóa Groq miễn phí rồi dùng chế độ mặc định **Tối ưu — Xoay tua toàn bộ API Key** để đạt tốc độ cao và mượt ngay cả khi một hãng tạm nghẽn `503`/`429`.

Các thông số mặc định (`whisper_model`, `chars_per_sec`, `max_stretch`, `mix`, `subtitles`, `tts`) nằm trong [`config.yaml`](../config.yaml) và có thể chỉnh trực tiếp trong hộp thoại Cài đặt hoặc cờ CLI.

---

## Phát triển & Kiểm thử

```bash
python -m pytest tests -q                 # 635+ test offline (không cần API key hay Internet)
python -m dubtool.app.main --selftest     # kiểm tra môi trường FFmpeg/Qt/VieNeu
python -m dubtool.app.main --layout-check # kiểm tra bố cục UI ở 25 kích thước × trạng thái
python -m dubtool.app.main --gui-smoke    # kiểm thử giao diện end-to-end trong môi trường cô lập
```

Tổng quan kiến trúc, lịch sử phát hành và báo cáo chi tiết xem tại [`docs/ARCHITECTURE.md`](ARCHITECTURE.md), [`docs/RELEASES.md`](RELEASES.md) và [`REPORT_UPDATE_19.md`](reports/REPORT_UPDATE_19.md).

---

## Quyền riêng tư & Giấy phép

- **Quyền riêng tư:** Nhận diện giọng nói (`faster-whisper`), tạo giọng tiếng Việt (`VieNeu-TTS v3 ONNX`), trộn âm thanh và gắn phụ đề đều chạy **100% cục bộ** trên máy của bạn. Chỉ văn bản lời thoại nguồn cùng ngữ cảnh tùy chọn bạn cung cấp (như mô tả video Brief và từ điển thuật ngữ Glossary) được gửi tới nhà cung cấp dịch bạn chọn. Khóa API lưu trong Windows Credential Manager (hoặc `.env`) và không bao giờ ghi ra log.
- **Giấy phép:** [Giấy phép MIT](../LICENSE) — Copyright (c) 2026 sickyturtlez. Các thành phần bên thứ ba đi kèm (FFmpeg GPL v3, PySide6 LGPL v3, faster-whisper, VieNeu-TTS, sea-g2p, soundfile, Noto Sans) giữ nguyên giấy phép riêng (chi tiết tại [`packaging/THIRD_PARTY.md`](../packaging/THIRD_PARTY.md)).
