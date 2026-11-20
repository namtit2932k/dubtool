# Đề xuất kỹ thuật: Chuyển đổi hoàn toàn Stage 4 TTS sang VieNeu-TTS v3

- **Dự án:** DubTool (`Project_K`)
- **Trạng thái:** Hoàn tất **Phase 0 (Spike & Benchmark)** — chờ review để chốt scope trước khi triển khai Phase 1–6
- **Ngày lập:** 2026-11-20
- **Phạm vi dự kiến:** Thay thế hoàn toàn `edge-tts` bằng **VieNeu-TTS v3** (chạy cục bộ bằng ONNX Runtime, không cần PyTorch, không cần `espeak-ng`)

---

## 1. Bối cảnh & Lý do chuyển đổi

### 1.1. Sự cố thực tế từ log chạy thật
Trên job thực tế 226 câu (`a3a1aea545dd`, `dub_units.json`: 7–246 ký tự/câu, trung bình 83.2 ký tự):
- **100% lỗi (88/88) trong `tts_failures.json`** đều là `TTSFailure <- NoAudioReceived` với thời gian lỗi tập trung chặt quanh **2.4s – 2.7s**.
- **Kiểm tra độc lập ngoài DubTool** (kết nối thẳng tới `wss://speech.platform.bing.com/...` với cùng các câu trong job): máy chủ Microsoft nhận kết nối, trả `Path:turn.start` và `Path:response`, rồi tự ngắt kết nối (`close=1006`) sau ~2.5s mà không trả bất kỳ byte âm thanh nào với xác suất **~50–60%**, kể cả câu rất ngắn (*"Em thích ai?"*).
- Gửi lại cùng câu đó vẫn thành công → nguyên nhân gốc là **endpoint miễn phí không chính thức của Microsoft đang drop kết nối nặng** (quá tải backend hoặc siết theo IP/phiên), không phải lỗi SSML hay dữ liệu đầu vào.

### 1.2. Hệ quả trên cơ chế Stage 4 hiện tại (`s4_tts.py`)
Khi tỷ lệ drop của endpoint lên tới 50–60%, cơ chế chống chịu mạng của `s4_tts.py` bị khuếch đại độ trễ:
1. **Khóa cứng về 1 luồng + giãn cách 2.5s/request:** `VoiceRequestLimiter` phạt mọi lỗi như nhau (`NoAudioReceived` bị đánh đồng với `429/503`), lập tức hạ về `limiter_floor_interval_s: 2.5s` và ép `active_workers = 1`. Điều kiện hồi tốc độ yêu cầu 10 lần thành công liên tiếp (`success_streak >= 10`), với tỷ lệ qua ~40–50% thì xác suất phục hồi là `< 0.1%`.
2. **Cộng dồn độ trễ retry:** Mỗi câu lỗi tiêu tốn 6–10s ngay ở vòng 1 (ngâm 2.5s + ngủ 1.5s + ngâm 2.5s) rồi dồn sang các vòng cooldown 15s / 30s / 60s, làm video 226 câu kéo dài hàng chục phút hoặc chạm trần `stage_budget_s: 600s` rồi dừng hỏi người dùng.
3. **Chi phí bảo trì cao cho một endpoint không có SLA:** Dù có tinh chỉnh lại limiter/retry cho `edge-tts`, Microsoft vẫn có thể tiếp tục siết hoặc khóa hẳn endpoint bất kỳ lúc nào (tương tự các đợt thay đổi token/WebSocket trước đây).

---

## 2. Đề xuất kiến trúc: Chuyển hoàn toàn sang VieNeu-TTS v3

### 2.1. Tại sao chọn VieNeu-TTS v3?
- **Chạy 100% offline ở Stage 4:** Triệt tiêu hoàn toàn `NoAudioReceived`, `close=1006`, rate-limit, độ trễ mạng và phụ thuộc máy chủ bên thứ ba.
- **Không cần PyTorch (Torch-free CPU):** `vieneu` v3 chạy trực tiếp trên **ONNX Runtime** — vốn đã được đóng gói sẵn trong `packaging/dubtool.spec` cho `faster-whisper`.
- **Không cần `espeak-ng`:** Bản v3 dùng `sea-g2p` (native wheel) xử lý phát âm tiếng Việt và tiếng Anh xen kẽ (code-switching) ngay trong Python, không bắt người dùng Windows cài thêm phần mềm ngoài.
- **Giấy phép phù hợp:** VieNeu-TTS v3 và `sea-g2p` phát hành theo giấy phép **Apache 2.0**, tương thích với giấy phép MIT của DubTool.
- **Chất lượng âm thanh & đa dạng giọng đọc:**
  - `v3turbo` INT8 xuất **48 kHz** với **25 giọng preset** đủ 3 miền Bắc / Trung / Nam (nhiều phong cách: tự nhiên, tin tức, kể chuyện, đọc truyện).
  - `v3nano` xuất **24 kHz** với **11 giọng preset**, RAM < 500 MB, siêu nhanh cho CPU yếu.
  - Mở đường sẵn cho tính năng nhân bản giọng (instant voice cloning từ 3–8s mẫu) ở các bản cập nhật sau nếu cần.

### 2.2. Các quyết định thiết kế chính đề xuất
| Hạng mục | Đề xuất | Lý do |
| --- | --- | --- |
| **Model mặc định** | `v3turbo` (`backend="onnx"`, `precision="int8"`, 48 kHz) | Tự nhiên nhất, xử lý tốt tiếng Anh xen kẽ, tải lần đầu ~244 MB, 226 câu xong trong ~5.4 phút trên i5-13500 CPU. |
| **Tùy chọn máy yếu** | `v3nano` (`steps=16`, 24 kHz) trong Settings | RAM peak chỉ ~477 MB, 226 câu xong trong ~2.5 phút (RTF 0.166), phù hợp máy 8 GB RAM / CPU cũ. |
| **Giọng mặc định & migration** | Nữ: **`Trúc Ly`** (Bắc · tự nhiên), Nam: **`Đức Trí`** (đọc truyện) / **`Adam`** (Nam · tự nhiên) | Các giọng này tồn tại trên **cả `v3turbo` lẫn `v3nano`** → đổi chế độ không bị mất giọng. Tự động map `vi-VN-HoaiMyNeural` → `Trúc Ly`, `vi-VN-NamMinhNeural` → `Đức Trí`. |
| **Lưu trữ model** | Tải on-demand vào `models/hf_cache` qua `HF_HUB_CACHE` (giống Whisper), **không nhồi vào `.zip` cài đặt** | Giữ `DubTool-win64.zip` gọn nhẹ; tái sử dụng cơ chế cache & cách ly `--gui-smoke` sẵn có của app. |
| **Định dạng cache Stage 4** | Chuyển từ `.mp3` sang **`.wav`** (PCM 16-bit) | VieNeu trả trực tiếp waveform numpy; lưu `.wav` tránh bước encode MP3 rồi Stage 5/6 lại decode, nhanh hơn và giữ nguyên chất lượng. |
| **Concurrency Stage 4** | Đặt **`concurrency: 1`**, điều tiết số luồng qua `threads` của ONNX Runtime theo setting `cpu_usage` | ONNX Runtime đã song song nội bộ; chạy nhiều worker sẽ tranh CPU và nhân đôi RAM (~4.5 GB với Turbo). |
| **Đơn giản hóa Stage 4** | Bỏ `VoiceRequestLimiter`, `round_cooldown_s`, `breaker_pause_s`, dialog chờ 10 phút; **giữ lại** cache từng câu (resumable) và tách câu dài (`split`) | Giảm đáng kể độ phức tạp code và các trạng thái lỗi giả tạo; vẫn bảo toàn khả năng resume và xử lý câu dài mượt mà. |
| **Hiệu chỉnh timing** | Cập nhật `chars_per_sec`: `12.5` → **`16.5`** | Đo thực tế trên `v3turbo` đạt p25 = 17.5, median = 18.1 ký tự/s (nhanh hơn `HoaiMyNeural` trước đây), giúp Stage 3 dịch tự nhiên, đỡ bị cắt gọt quá tay. |
| **Đóng gói PyInstaller** | Exclude toàn bộ gói WebUI/audio nặng không dùng (`gradio`, `fastapi`, `uvicorn`, `pandas`, `PIL`, `librosa`, `scipy`, `numba`, `llvmlite`, `sklearn`, `torch`) | Đã chứng minh bằng Phase 0 là runtime preset **không import** bất kỳ gói nào trong nhóm này → `.zip` chỉ tăng ~28–30 MB. |

---

## 3. Kết quả Phase 0 — Spike kỹ thuật & Benchmark thực tế

### 3.1. Môi trường kiểm chứng
- **Phần cứng:** 13th Gen Intel Core i5-13500 (14 nhân / 20 luồng), 15.8 GB RAM — chạy **thuần CPU ONNX** (chưa bật GPU RTX 3060).
- **Môi trường Python:** `.venv` Python 3.13.1 của `Project_K`.
- **Dữ liệu test:** 30 câu tổng hợp cho mỗi cấu hình (120 lượt tổng hợp):
  - **25 câu thật** trích từ job 226 câu `a3a1aea545dd/dub_units.json` (5 câu ngắn nhất 7–15 ký tự, 15 câu trung bình trải đều, 5 câu dài nhất tới 246 ký tự).
  - **5 câu biên (edge cases):** số/ngày tháng/phần trăm/tiền tệ (`20/11/2026`, `50%`, `125.000 đồng`), tiếng Anh xen kẽ (`API key`, `Settings`, `DubTool`, `Gemini 3.5`), câu 1–2 từ (`Không!`, `Đi thôi.`), chuỗi dấu câu (`...?!—`).

### 3.2. Tương thích thư viện & Kiểm toán import runtime
- Cài `vieneu==3.8.5` + `sea-g2p==0.10.0` + `soundfile==0.14.0`: **0 xung đột** với `faster-whisper==1.2.1`, `onnxruntime==1.32.0`, `numpy==2.5.5`, `av==18.1.0`.
- Không yêu cầu `torch`, không yêu cầu `espeak-ng`.
- Chạy lại `pytest tests/test_units_and_prompts.py`: **25 passed**.
- **Kiểm toán `sys.modules` sau khi chạy hết 30 câu (cả `v3nano` lẫn `v3turbo`):**
  - **Được nạp (`True`):** `sea_g2p`, `onnxruntime`, `soundfile`.
  - **Không được nạp (`False`):** `gradio`, `fastapi`, `uvicorn`, `pandas`, `PIL`, `torch`, `librosa`, `scipy`, `numba`, `llvmlite`, `sklearn`, `soxr`, `kaldi_native_fbank`.

### 3.3. Bảng kết quả Benchmark (CPU-only ONNX)

| Cấu hình | Sample Rate | Load ấm | Peak RAM | RTF (CPU) | Ước tính 226 câu | Thành công / Lỗi / Bất thường | Ký tự/s (p25 / median / p75) | Khoảng lặng TB | Dung lượng tải (HF cache) |
| --- | ---: | ---: | ---: | ---: | ---: | :---: | :---: | ---: | ---: |
| **`v3nano` (8 steps)** | 24 kHz | 2.52s | **464 MB** | **0.095** (~10.5×) | **~1.5 phút** (87.5s) | **30/30 · 0 · 0** | 20.4 / 21.6 / 22.5 | 0.014s | 268.7 MB |
| **`v3nano` (16 steps)** | 24 kHz | 2.73s | **477 MB** | **0.166** (~6.0×) | **~2.5 phút** (152.0s) | **30/30 · 0 · 0** | 21.0 / 21.5 / 22.6 | 0.023s | 268.7 MB |
| **`v3turbo` INT8** | 48 kHz | 5.41s | 2,228 MB | **0.284** (~3.5×) | **~5.4 phút** (325.3s) | **30/30 · 0 · 0** | 17.5 / 18.1 / 19.2 | 0.307s | ~243.5 MB* |
| **`v3turbo` FP32** | 48 kHz | ~6.0s | 2,783 MB | **0.426** (~2.3×) | **~8.1 phút** (487.8s) | **30/30 · 0 · 0** | 17.2 / 18.3 / 18.9 | 0.292s | ~539.5 MB* |

*\*Chi tiết dung lượng đo trên đĩa (`HF_HUB_CACHE`): `MOSS-Audio-Tokenizer-Nano-ONNX` = 86.4 MB; `v3turbo` `onnx_int8` = ~157 MB (tổng INT8 ~243.5 MB); `v3turbo` `onnx_update` FP32 = ~453 MB (tổng khi tải cả hai là 651.9 MB).*

**Nhận xét chính từ Phase 0:**
- **Độ ổn định 100% (120/120):** Không xuất hiện bất kỳ lỗi ngoại lệ, audio rỗng, NaN hay hiện tượng lặp chữ / cắt chữ (`cps_trim < 5` hoặc `> 30`) nào trên cả câu cực ngắn 7 ký tự lẫn câu dài 246 ký tự.
- **Tốc độ vượt trội so với `edge-tts` đang bị drop:** Từ ~20–30 phút (hoặc treo chạm trần 10 phút) xuống còn **~2.5 phút (`v3nano`)** hoặc **~5.4 phút (`v3turbo` INT8)**, ổn định dự đoán được.
- **Lưu ý nhỏ phát hiện được:**
  - `v3turbo` có giọng mặc định nội bộ là `"Adam bựa"` → **bắt buộc** DubTool phải truyền `voice` tường minh (ví dụ `"Trúc Ly"`), không bao giờ để mặc định `None`.
  - `v3turbo` INT8 cần ~2.2 GB RAM → cần đảm bảo giải phóng model Whisper (`gc.collect()`) sau Stage 2 trước khi nạp VieNeu ở Stage 4, và gọi `tts.close()` khi xong Stage 4/5 để tránh giữ RAM trên máy 8 GB.

### 3.4. Danh sách giọng preset thực tế phát hiện được
- **Giọng chung của cả `v3turbo` và `v3nano` (khuyến nghị làm nhóm mặc định an toàn):**
  - Nữ: `Trúc Ly` (Bắc · tự nhiên), `Mai Anh` (Bắc · tin tức), `Mỹ Duyên` (đọc truyện)
  - Nam: `Đức Trí` (đọc truyện), `Adam` (Nam · tự nhiên)
- **Mở rộng riêng của `v3turbo` (tổng 25 giọng):** có thêm giọng **miền Trung** (`Quang Sơn`, `Ngọc Trân`), giọng Nam/Bắc phong phú (`Ngọc Huyền`, `Thùy Dung`, `Thiện Minh`, `Hải Đăng`, `Quỳnh Anh`, `Ngọc Linh`, `Đoan Trang`, `Thục Đoan`, `Minh Đức`, `Thái Sơn`, `Kim Thanh`, v.v.).
- **Mở rộng riêng của `v3nano` (tổng 11 giọng):** có thêm `Ái Hân` (Nữ · Nam), `Hữu Quân`, `Xuân Tiên`, `Anh Khôi`, `Minh Quân`, `Mạnh Dũng`.

---

## 4. Kế hoạch triển khai dự kiến (Phase 1 → Phase 6)

### Phase 1 — Xây dựng `VieNeuTTSProvider`
- **Tạo mới:** `src/dubtool/tts/vieneu.py` implement `TTSProvider` (`src/dubtool/tts/base.py`).
- **Yêu cầu kỹ thuật:**
  - Lazy-load và giữ 1 instance model duy nhất trong suốt Stage 4; cung cấp `close()` để giải phóng RAM khi xong stage.
  - Điều hướng `HF_HUB_CACHE` vào `paths.models_dir() / "hf_cache"` trước khi khởi tạo `Vieneu(...)`.
  - Hỗ trợ 2 chế độ: `v3turbo_int8` (`mode="v3turbo", backend="onnx", precision="int8"`) và `v3nano` (`mode="v3nano", steps=16`), truyền `threads` theo cài đặt `cpu_usage` (`low` / `balanced` / `maximum`).
  - Validate đầu vào/đầu ra: từ chối text rỗng, kiểm tra mảng audio hữu hạn (`np.isfinite`), ghi PCM 16-bit `.wav` qua `soundfile`, xác thực `duration > 0.05s`; bọc mọi lỗi thành `TTSFailure`.
  - Tôn trọng cờ `cancel` giữa các câu.
- **Tiêu chí xong:** Unit test với mock `Vieneu` pass 100%; test thật 5 câu xuất `.wav` hợp lệ ở cả 2 chế độ.

### Phase 2 — Cập nhật Stage 4 (`s4_tts.py`), Cache `.wav` & Cấu hình
- **File chính:** `src/dubtool/stages/s4_tts.py`, `src/dubtool/stages/s5_fit_duration.py`, `src/dubtool/audio_utils.py`, `config.yaml`, `src/dubtool/config.py`.
- **Công việc:**
  - Chuyển cache TTS từ `tts/cache/<key>.mp3` sang `tts/cache/<key>.wav`; đưa `provider` (`vieneu`), `mode`, `precision` vào cache key để không đụng cache cũ.
  - Cập nhật `_join_pieces()` ghép các mảnh `.wav` trực tiếp (dùng đúng sample rate 48 kHz / 24 kHz của model thay vì hardcode `24000` mp3).
  - Xác nhận `audio_utils.trim_silence()`, `s5_fit_duration.py` và `s6_mix.py` xử lý trực tiếp `.wav` 24/48 kHz chính xác.
  - Đơn giản hóa Stage 4: dùng `VieNeuTTSProvider`, `concurrency: 1`, gỡ `VoiceRequestLimiter`, `round_cooldown_s`, `breaker_pause_s`, `_WAIT10_S`; giữ nguyên resume theo cache và tách câu dài (`max_chars_per_request: 200`).
  - Sửa nhãn log tiến độ UI hiển thị đúng `đã xong / tổng` và số lỗi thực tế.
  - Cập nhật `config.yaml`: `chars_per_sec: 16.5`, `tts_voice: "Trúc Ly"`, thêm `tts.mode: "v3turbo_int8"`.
- **Tiêu chí xong:** Chạy trọn Stage 4 → 5 → 6 trên dữ liệu thật không lỗi, đồng bộ đúng nhịp phụ đề và âm thanh.

### Phase 3 — Gỡ bỏ hoàn toàn `edge-tts` và mã mạng thừa
- **File chính:** xóa `src/dubtool/tts/edge.py`, `src/dubtool/tts_probe.py`, `scripts/validate_voice_settings.py` (và đánh giá xóa/thu gọn `src/dubtool/tts/fallback.py`); cập nhật `pyproject.toml`, `src/dubtool/cli.py`, `src/dubtool/api.py`, `src/dubtool/netcheck.py`, `src/dubtool/app/selftest.py`.
- **Công việc:**
  - Thay `edge-tts>=6.1` bằng `vieneu>=3.8,<4` (hoặc ghim dải an toàn) trong `pyproject.toml`.
  - Xóa `edge_tts_hosts()` trong `netcheck.py` (giữ lại kiểm tra Gemini API + Hugging Face cho tải model Whisper & VieNeu).
  - Cập nhật `selftest.py` kiểm tra import `vieneu`, `sea_g2p`, `onnxruntime`, `soundfile`.
  - Gỡ `tts_budget_dialog` (chờ 10 phút / đổi giọng dự phòng) trong `api.py` và `service.py`.
- **Tiêu chí xong:** Grep toàn repo `src/dubtool/` còn 0 tham chiếu runtime tới `edge_tts` / `EdgeTTSProvider` / `HoaiMyNeural` (trừ bảng migration settings cũ).

### Phase 4 — Giao diện (UI), Settings, Migration & Tải model lần đầu
- **File chính:** `src/dubtool/app/settings.py`, `src/dubtool/app/ui/settings_dialog.py`, `src/dubtool/app/ui/main_window.py`, `src/dubtool/app/i18n.py`, `src/dubtool/app/paths.py`, `src/dubtool/app/housekeeping.py`.
- **Công việc:**
  - Migration trong `settings.load()`: tự động chuyển `"vi-VN-HoaiMyNeural"` → `"Trúc Ly"`, `"vi-VN-NamMinhNeural"` → `"Đức Trí"`, thêm key `tts_model` (`"v3turbo_int8"` | `"v3nano"`).
  - Cập nhật dropdown chọn giọng trong Settings (hiển thị danh sách giọng kèm vùng miền & phong cách theo chế độ `v3turbo` / `v3nano`).
  - Thêm chọn chế độ giọng nói: **Chuẩn — Tự nhiên (Turbo INT8, ~244 MB)** và **Nhanh — Máy yếu (Nano, ~269 MB, RAM thấp)**.
  - Hiển thị thông báo/trạng thái rõ ràng khi tải model VieNeu lần đầu (tương tự bước tải model Whisper) và báo lỗi thân thiện nếu chưa có mạng ở lần tải đầu tiên.
  - Gỡ UI cấu hình backup voice (Windows SAPI / Piper) và dialog chờ 10 phút.
  - Đảm bảo `housekeeping.py` / `cleanup.py` không xóa nhầm thư mục `models/hf_cache`.
- **Tiêu chí xong:** `--layout-check`, `--gui-smoke`, `--screenshots` pass 100%; không có text cắt xén ở 760×600 và 900×700.

### Phase 5 — Đóng gói Windows `.exe` & Bản quyền
- **File chính:** `packaging/dubtool.spec`, `packaging/build_exe.ps1`, `packaging/THIRD_PARTY.md`, `packaging/licenses/`.
- **Công việc:**
  - Bỏ `edge_tts` khỏi `datas` / `hiddenimports` trong `dubtool.spec`.
  - Thu thập `vieneu`, `sea_g2p` (data + dynamic libs), `soundfile` (`_soundfile_data` DLL `libsndfile`).
  - Thêm vào `excludes`: `gradio`, `gradio_client`, `fastapi`, `starlette`, `uvicorn`, `pandas`, `PIL`, `librosa`, `scipy`, `numba`, `llvmlite`, `sklearn`, `torch`, `torchaudio`, `transformers`.
  - Xác nhận `DubTool-win64.zip` không phình dung lượng bất thường (kỳ vọng tăng ~28–35 MB so với bản 1.0.16).
  - Thêm thông báo bản quyền Apache 2.0 cho `VieNeu-TTS`, `sea-g2p`, `MOSS-Audio-Tokenizer-Nano` và LGPL/BSD cho `soundfile` / `libsndfile` vào `packaging/THIRD_PARTY.md` và `packaging/licenses/`.
- **Tiêu chí xong:** Build sạch từ git archive, `DubTool.exe --selftest` và `--gui-smoke` exit 0.

### Phase 6 — Kiểm thử toàn diện & Xác minh E2E
- **Unit & integration tests:** Viết test mới cho `VieNeuTTSProvider`, WAV cache, settings migration, và cập nhật/dọn bộ test cũ của `edge-tts` / `VoiceRequestLimiter` / budget dialog; đảm bảo `pytest -rs` 100% pass (không thêm skip mới).
- **E2E trên video thật:** Chạy trọn `s1`–`s7` trên đúng video 226 câu (job từng bị chậm/kẹt ở Stage 4) + 1 video ngắn tiêu chuẩn; ghi nhận thời gian thực từng stage, peak RAM, tỷ lệ overflow ở Stage 5 và kiểm tra chất lượng bản mix cuối.
- **Kiểm tra `.exe` sạch:** Chạy `scripts/release.py <update> --dry-run` (đạt 8/8 DoD) và chạy thử `DubTool.exe` trên thư mục cache trống để kiểm chứng luồng tải model lần đầu từ Hugging Face.

---

## 5. Các điểm cần Agent Quản lý review & chốt quyết định

1. **Phiên bản phát hành:** Nên hoàn tất/chốt Update 16 (`1.0.16`) trước rồi tách việc chuyển sang VieNeu-TTS thành **Update 17 (`1.0.17`)**, hay gộp trực tiếp vào bản phát hành tiếp theo? *(Khuyến nghị: tách thành **Update 17** để giữ scope Update 16 gọn và dễ rollback nếu cần.)*
2. **Giữ hay xóa `fallback.py` (Windows SAPI / WinRT / Piper)?** Vì VieNeu-TTS đã chạy 100% offline cục bộ, có thể gỡ hẳn cơ chế giọng dự phòng để UI & codebase tối giản.
3. **Danh sách giọng hiển thị trong UI:** Cho người dùng chọn toàn bộ 25 giọng (Turbo) / 11 giọng (Nano) hay lọc gọn một bộ 6–8 giọng chất lượng cao đại diện 3 miền Bắc / Trung / Nam?
4. **Tính năng Voice Cloning (nhân bản giọng từ file `.wav` 3–8s):** Đề xuất **chưa làm trong đợt chuyển đổi này** (tách sang bản cập nhật sau để tránh kéo thêm `librosa`/`soxr`/`speaker_encoder` vào scope hiện tại).
5. **Tối ưu GPU (tùy chọn về sau):** Đợt này tập trung CPU ONNX (`torch`-free, cài 1 phát ăn ngay trên mọi máy Windows). Hỗ trợ CUDA GPU có thể xem xét ở bản sau nếu cần.
