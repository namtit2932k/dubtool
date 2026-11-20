<p align="center">
  <img src="images/logo.png" width="120" alt="DubTool" />
</p>

<h1 align="center">DubTool</h1>

<p align="center">
  <strong>Ứng dụng lồng tiếng video đa ngôn ngữ sang tiếng Việt cho Windows — nhận diện giọng nói cục bộ, giọng đọc VieNeu-TTS v3 offline và gắn phụ đề tự động.</strong>
</p>

<p align="center">
  <a href="https://github.com/namtit2932k/dubtool/releases/latest">
    <img src="https://img.shields.io/github/v/release/namtit2932k/dubtool?label=release&color=e05d44" alt="Phiên bản mới nhất" />
  </a>
  <a href="https://github.com/namtit2932k/dubtool/releases">
    <img src="https://img.shields.io/github/downloads/namtit2932k/dubtool/total?label=downloads&color=2ea043" alt="Lượt tải" />
  </a>
  <a href="../LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-0969da" alt="Giấy phép MIT" />
  </a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0969da" alt="Windows 10/11 x64" />
  <img src="https://img.shields.io/badge/TTS-VieNeu--TTS%20v3%20(Offline)-2ea043" alt="VieNeu-TTS v3 Offline" />
</p>

<p align="center">
  <a href="../README.md">English</a> · <strong>Tiếng Việt</strong>
</p>

<p align="center">
  <a href="https://github.com/namtit2932k/dubtool/releases/latest">
    <img src="https://img.shields.io/badge/%E2%86%93%20T%E1%BA%A2I%20V%E1%BB%80-WINDOWS%2010%2F11%20X64-2ea043?style=for-the-badge&labelColor=555555" alt="Tải về cho Windows 10/11 x64" />
  </a>
</p>

---

DubTool tự động nhận diện ngôn ngữ nói của video (hoặc dùng ngôn ngữ bạn chọn tay), nhận diện lời thoại **100% cục bộ** bằng `faster-whisper`, dịch sang tiếng Việt tự nhiên qua LLM tùy chọn (**Gemini**, **OpenAI**, **Anthropic**, hoặc **Ollama** cục bộ), tạo giọng đọc tiếng Việt **100% offline** bằng **VieNeu-TTS v3 ONNX** (`48 kHz` Turbo INT8 hoặc `24 kHz` Nano tiết kiệm RAM), căn chỉnh khớp thời lượng, trộn đè lên âm thanh gốc đã hạ âm lượng (có bộ giới hạn chống rè), gắn phụ đề tiếng Việt và xuất kèm tệp bản dịch `.docx`/`.txt`.

Nếu video đầu vào đã là tiếng Việt, DubTool sẽ tự dừng trước bước dịch và đề xuất xuất phụ đề `.srt` + bản chép lời mà không gọi API dịch.

> ⚖️ Chỉ sử dụng cho video bạn sở hữu hoặc có quyền chỉnh sửa. Khi tải và sử dụng DubTool, bạn đồng ý với [Giấy phép MIT](../LICENSE) và các [thông báo bên thứ ba](../packaging/THIRD_PARTY.md).

<p align="center">
  <img src="images/ready.png" alt="Giao diện chính DubTool" />
</p>

---

## Tính năng nổi bật

- **Xử lý cục bộ & riêng tư:** Video, âm thanh, nhận diện giọng nói (`faster-whisper`) và tổng hợp giọng tiếng Việt (`VieNeu-TTS v3 ONNX`) đều chạy trực tiếp trên máy bạn. Chỉ văn bản lời thoại nguồn được gửi đến nhà cung cấp LLM bạn chọn để dịch.
- **25+ giọng đọc tiếng Việt offline:** Mặc định dùng `v3turbo_int8` (48 kHz, giọng *Trúc Ly*) cho chất lượng tự nhiên, hoặc `v3nano` (24 kHz, chỉ ~480 MB RAM) cho máy cấu hình thấp. Chỉ tải model 1 lần (~244–269 MB) vào `%LOCALAPPDATA%\DubTool\models\hf_cache`.
- **Khớp thời lượng & không chồng tiếng:** Tự động sửa lỗi trôi timestamp của Whisper, co giãn tốc độ mượt (`atempo`) / rút gọn câu khi dài quá khung, xếp lịch không để các đoạn lồng tiếng đè nhau, phụ đề khớp chính xác thời điểm phát thực tế.
- **Không bao giờ ghi đè file gốc:** Mọi giai đoạn đều có thể hủy giữa chừng và chạy tiếp (`Tiếp tục`) mà không mất công đoạn đã xong.
- **Lưu khóa API an toàn:** Khóa API được lưu trong **Windows Credential Manager**, không bao giờ ghi ra `settings.json`, log hay thư mục tạm.

---

## Cài đặt & Sử dụng nhanh

### Cách 1: Bản Portable cho Windows (Khuyên dùng)

1. Tải tệp `DubTool-*-win64.zip` mới nhất tại [GitHub Releases](https://github.com/namtit2932k/dubtool/releases/latest) và giải nén.
2. Mở **`DubTool.exe`** (đã đóng gói sẵn FFmpeg và phông chữ tiếng Việt, không cần cài Python).
3. Vào **Cài đặt → Khóa API**, thêm khóa Gemini miễn phí từ [Google AI Studio](https://aistudio.google.com/apikey) (hoặc khóa OpenAI / Anthropic).
4. Kéo thả video vào cửa sổ, chọn thư mục xuất và bấm **Tạo**.

### Cách 2: Chạy từ mã nguồn / dòng lệnh (CLI)

Yêu cầu **Python 3.11+** và **FFmpeg trong `PATH`**.

```bash
pip install -e .[dev]
copy .env.example .env      # điền GEMINI_API_KEY=...
python -m dubtool.app.main  # mở giao diện desktop
```

Xem chi tiết đóng gói `.exe` tại [README_BUILD.md](../README_BUILD.md).

---

## Giấy phép

Mã nguồn ứng dụng phát hành theo [Giấy phép MIT](../LICENSE) — Copyright (c) 2026 sickyturtlez. Các thành phần bên thứ ba đi kèm giữ nguyên giấy phép riêng (chi tiết tại [`packaging/THIRD_PARTY.md`](../packaging/THIRD_PARTY.md)).
