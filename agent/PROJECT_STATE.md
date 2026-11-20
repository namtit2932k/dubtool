# DubTool — Project State (Single Source of Truth)

> **Cập nhật lần cuối:** 2026-11-20  
> **Mục đích:** Lưu trạng thái thực tế của dự án để giữ liên tục 100% qua các lần compact context và giữa các session. Luôn đối chiếu với `git status` / `git log` khi bắt đầu phiên mới.

---

## 1. Trạng thái Git & Phiên bản hiện tại

- **Repository:** `namtit2932k/dubtool` (public), workspace `d:\Projects\Vibe\Project_K`, build root `D:\Projects\Vibe\Project_K_build`.
- **Phiên bản đã phát hành gần nhất:** **`v1.0.18`** (commit `a9c4680aae9b1abd0fc7a4d760dee9c398864a76`, 2026-11-20) — Official, đã verify đầy đủ qua `scripts/release.py 18` (`DOD_REPORT.md` 9/9 PASS, `RELEASE_EVIDENCE.txt` PASS, `verify_release.py --audit` PASS, `DubTool-1.0.18-win64.zip` 220,855,212 B, SHA-256 `387c4b58777c44b67ffd28532d50fc111dcc37f0b8aecdb3a85f3e86f3f31dc9`, `https://github.com/namtit2932k/dubtool/releases/tag/v1.0.18`).
- **Nhánh hiện tại (`main`):** Đã phát hành chính thức **`v1.0.18` (Update 18)** bao gồm toàn bộ chuỗi sửa lỗi thực tế và cải tiến sau `v1.0.17`:
  1. Nút Hủy dừng ngay khi S3 gặp 503 liên tục + short-circuit `punctuate` + UI `"Đang hủy…"`.
  2. Fix mất/cắt UI bên phải khi thu nhỏ cửa sổ chính (`_make_shrinkable_combo`, `minimumSize` động, kiểm tra `scroll-content-overflow-x`).
  3. Tách riêng `fallback_model` khỏi mô hình chính ở S3 (`fast_model_id` ưu tiên `config.llm_model`) và giữ nguyên lựa chọn `model_combo` sau khi đóng Cài đặt.
  4. Fix S4 `'NoneType' object has no attribute 'write'` trên `.exe` windowed (`_ensure_stdio` + `_hf_cache_context`) + fail-fast `TTSEngineInitError`.
  5. Fix lệch mốc thời gian giọng lồng tiếng (`repair_word_timings` chữa chữ mồ côi đầu đoạn & tách khoảng lặng nội bộ `>= 1.5s`, guard `max_gap_s=0.5` cho `merge_short`, `vad_parameters={"min_silence_duration_ms": 500}`).
  6. Fix header `Chi tiết tiến trình` không còn giữ dòng cũ từ S5 khi sang S7/hoàn tất (`Hoàn tất`).
  7. Nâng cấp `README.md` + `docs/README.vi.md` theo phong cách hiện đại với logo trắng không viền (`docs/images/logo.png`), đổi icon `.exe` và icon cửa sổ sang logo trắng nền đen (`packaging/icon.ico`).
  8. Dọn gọn thư mục gốc (`REPORT_UPDATE_*.md` → `docs/reports/`, xóa rác untracked) và giảm tổng dung lượng `Project_K` + `Project_K_build` hơn **5.5 GB**.
  9. Tinh chỉnh UI/UX & Gemini catalog: căn thẳng thanh tiến trình (`setTextVisible(False)`, `counter` ẩn khi rỗng) & 2 nút `Sao chép chi tiết`/`Mở thư mục log` 50/50, thêm nút **Nghe thử giọng** offline (`voice_preview.py`) ngay dưới chọn giọng đọc ở cả màn hình chính và Cài đặt, cho phép resize & thu gọn `SettingsDialog` (tab Chung tự xuống dòng `WrapLongRows` không bị nuốt UI khi thu hẹp; tab Nâng cao bố cục lưới 2 cột gọn gàng cho giữ job tối đa & giới hạn tổng dung lượng + 2 nút đáy chia 50/50), hiển thị rõ đường dẫn mặc định thư mục job + nút `Chọn…`, bổ sung các model Gemini chạy ổn định (`gemini-3.1-flash-lite`, `gemini-3-flash-preview`, `gemini-flash-lite-latest`), giảm concurrency của `3.6-flash`/`3.8-flash` xuống 1, giữ `gemini-3.5-flash-lite` làm mô hình chính mặc định và đặt `gemini-3.1-flash-lite` làm mô hình dự phòng mặc định lần đầu.
  10. Bộ test offline **595 passed, 2 skipped (`SKIPS.md`), 0 failed**; báo cáo cập nhật tại `docs/reports/REPORT_UPDATE_18.md`.

---

## 2. Decision Log (Các quyết định đã chốt)

| Ngày | Chủ đề | Quyết định đã chốt |
| --- | --- | --- |
| 2026-10-26 | Chữ ký số & watermark | **Bỏ hoàn toàn**, không thêm lại trừ khi người dùng yêu cầu. |
| 2026-11-19 | `v1.0.14` release | Giữ trạng thái `pre-release, superseded` trong `docs/RELEASES.md`, không xóa/sửa lịch sử. |
| 2026-11-19 | Thư mục `agent/` | Giữ public trong repo. |
| 2026-11-20 | Giấy phép (License) | **MIT** (`Copyright (c) 2026 sickyturtlez`) cho mã nguồn app; giữ nguyên thông báo & license bên thứ ba (FFmpeg GPL, PySide6 LGPL, VieNeu-TTS/sea-g2p/MOSS-Audio-Tokenizer-Nano Apache-2.0, soundfile/libsndfile BSD-3/LGPL). |
| 2026-11-20 | Update 16 (`v1.0.16`) | Đã ship chính thức ở `76ba8313` (đa ngôn ngữ → Việt, bỏ A/B `edge-tts` Task 3.2–3.4 theo quyết định người dùng). |
| 2026-11-20 | Update 17 (`1.0.17`) | Thay thế **hoàn toàn** `edge-tts`, `fallback.py`, `tts_probe.py`, `TtsBudgetDialog` bằng **VieNeu-TTS v3 ONNX offline** (`v3turbo_int8` 48 kHz mặc định + `v3nano` 24 kHz low-RAM). Voice cloning & CUDA GPU tách sang bản sau. |
| 2026-11-20 | Cách phối hợp | Agent trực tiếp sửa code, chạy test, build và verify tự động; giao tiếp hoàn toàn bằng tiếng Việt theo `GEMINI.md`. |

---

## 3. Trạng thái chi tiết Update 17 (`1.0.17`)

- [x] **Phase 0 (Spike & Benchmark):** Hoàn tất trước đó (`docs/PROPOSAL_VIENEU_TTS.md`).
- [x] **Phase 1 (`src/dubtool/tts/vieneu.py`):** Hoàn tất `VieNeuTTSProvider`, catalog 25 giọng Turbo + 11 giọng Nano (ghim 5 giọng dùng chung lên đầu, mặc định `"Trúc Ly"`, không bao giờ truyền `voice=None`), định tuyến `HF_HUB_CACHE` vào `models/hf_cache`, smoke test thật trên cả `v3turbo_int8` (48 kHz) và `v3nano` (24 kHz) PASS.
- [x] **Phase 2 (Stage 4/5/Config/Job):**
  - Chuyển `s4_tts.py` sang cache `.wav` PCM 16-bit, nối mảnh bằng `soundfile` + `numpy` giữ nguyên native sample rate (48/24 kHz), bỏ limiter/cooldown/breaker/fallback/budget wait, giải phóng model ONNX trong `finally`, fix progress log `done/total`.
  - Cập nhật `s5_fit_duration.py` đọc `.wav` (có fallback `.mp3` resume cũ) và gọi `provider.close()` trong `finally`; giải phóng model Whisper + `gc.collect()` sau `s2_transcribe.py`.
  - Cập nhật `config.yaml` (`chars_per_sec: 16.5`, `tts_voice: Trúc Ly`, `tts.mode: v3turbo_int8`), `config.py`, `job.py` (`stage_inputs_hash` cho `s4` gồm `tts.mode`).
- [x] **Phase 3 (Gỡ bỏ `edge-tts` & mã thừa):** Đã `git rm` `tts/edge.py`, `tts/fallback.py`, `tts_probe.py`, `scripts/validate_voice_settings.py`, `scripts/tts_experiment.py`; cập nhật `pyproject.toml`, `netcheck.py`, `selftest.py`, `diagnostics.py`, `cli.py`, `api.py`, `tts_failures.py` (0 runtime reference tới `edge_tts` trong `src/dubtool/`).
- [x] **Phase 4 (UI, Settings, Migration, i18n):**
  - Migrate tự động `vi-VN-HoaiMyNeural` → `Trúc Ly`, `vi-VN-NamMinhNeural` → `Đức Trí` trong `settings.py`; thêm `tts_model` (`v3turbo_int8` | `v3nano`), bỏ `tts_backup_voice` và `TtsBudgetDialog`.
  - Thêm dropdown chế độ giọng đọc + danh sách giọng động theo chế độ trong `SettingsDialog` và đồng bộ với `MainWindow.options`; cập nhật `first_run.privacy` (cả ASR + TTS chạy 100% cục bộ).
  - Verify `--layout-check` (0 violations), `--gui-smoke` (10/10 PASS), Rule 7 AST scan (0 spec IDs).
- [x] **Phase 5 (Packaging & Licenses):** Cập nhật `packaging/build_exe.ps1` (cài `vieneu` `--no-deps` để tránh kéo `gradio`/`librosa`/`numba`), `packaging/dubtool.spec` (collect `vieneu`, `vieneu_utils`, `sea_g2p`, `_soundfile_data`), `packaging/THIRD_PARTY.md`, `packaging/RELEASE_NOTES_TEMPLATE.md`, `packaging/licenses/THIRD_PARTY_NOTICES.txt`, `README.md`, `README_BUILD.md`.
- [x] **Phase 6 (Testing, Build `.exe`, Real E2E, Report & Official Release):**
  - [x] Thêm `tests/test_vieneu_tts.py` (13 tests) và cập nhật test cũ; toàn bộ bộ test **579 passed, 2 skipped (`SKIPS.md`), 0 failed**.
  - [x] Build `DubTool.exe` `1.0.17` (`DubTool-1.0.17-win64.zip` 210.51 MB, +11.5 MB so với `1.0.16`), pass `--version`, `--selftest`, `--layout-check` (0 violations), `--gui-smoke` (10/10 PASS).
  - [x] Verify E2E thật `s1–s8` trên `DubTool.exe` `1.0.17`:
    - `test9.mp4` (`v3turbo_int8`, `Trúc Ly`): 200.9s, s4=81.2s (0/62 lỗi), fit `59 ok / 1 shortened / 2 stretched / 0 overflow`, peak RAM 1,903 MB.
    - `clip_zh.mp4` (`v3nano`, `Mai Anh`): 32.8s, s4=8.8s (0/8 lỗi), fit `2 ok / 2 shortened / 4 stretched / 0 overflow`, peak RAM 635 MB.
  - [x] Đã sửa thêm 4 lỗi nhỏ bắt được khi verify (`update_readme_images.py` copy threshold, `dubtool.spec` `copy_metadata("vieneu")`, `settings_dialog.py` `_valid_id`, `s4_tts.py` sync `job.state`).
  - [x] Viết xong `REPORT_UPDATE_17.md`, phát hành chính thức `v1.0.17` (`https://github.com/namtit2932k/dubtool/releases/tag/v1.0.17`) qua `scripts/release.py 17` (`RELEASE_EVIDENCE.txt` PASS, `DOD_REPORT.md` 9/9 PASS).
  - [x] Hậu-phát hành: sửa `scripts/verify_release.py` để `--audit` nhận diện mọi bản `Official` cũ hơn bản mới nhất trong `docs/RELEASES.md` (tránh báo thiếu `RELEASE_EVIDENCE` khi phát hành bản mới ghi đè `RELEASE_EVIDENCE.txt`), cập nhật `docs/RELEASES.md` cho `v1.0.17`.

---

## 4. Lộ trình kế tiếp (Tùy chọn cho Update 19+)

- **Voice Cloning (VieNeu-TTS v3):** Cho phép người dùng nạp clip giọng mẫu 3–10 giây (cần đóng gói thêm `soxr`, `kaldi-native-fbank` và tải encoder/denoiser ONNX theo yêu cầu).
- **Tăng tốc GPU (CUDA) cho Whisper & VieNeu-TTS v3:** Tuỳ chọn cho máy có GPU NVIDIA.
- **Mở rộng UX/chất lượng theo phản hồi thực tế sau khi dùng bản `v1.0.18`.**
