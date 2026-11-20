# DubTool — Project State (Single Source of Truth)

> **Cập nhật lần cuối:** 2026-11-20  
> **Mục đích:** Lưu trạng thái thực tế của dự án để giữ liên tục 100% qua các lần compact context và giữa các session. Luôn đối chiếu với `git status` / `git log` khi bắt đầu phiên mới.

---

## 1. Trạng thái Git & Phiên bản hiện tại

- **Repository:** `namtit2932k/dubtool` (public), workspace `d:\Projects\Vibe\Project_K`
- **Remote (`origin/main`):** `102528c` — **`v1.0.15`** (phát hành 2026-11-14, `RELEASE_EVIDENCE.txt` đang là bản 1.0.15).
- **Local (`main`):** `7cabd63` (**11 commits ahead** of `origin/main`), đang dở **Update 16 (`1.0.16`)**, chưa push và chưa tạo tag `v1.0.16`.
- **Tags cục bộ mới thêm cho Update 16:** `v1.0.8` và `update-8` → `262f9eb` (chưa push).
- **File đề xuất chưa tracked:** `docs/PROPOSAL_VIENEU_TTS.md` (hoàn tất Phase 0 benchmark cho kế hoạch Update 17).

---

## 2. Decision Log (Các quyết định đã chốt)

| Ngày | Chủ đề | Quyết định đã chốt |
| --- | --- | --- |
| 2026-10-26 | Chữ ký số & watermark | **Bỏ hoàn toàn**, không thêm lại trừ khi người dùng yêu cầu. |
| 2026-11-19 | `v1.0.14` release | Giữ trạng thái `pre-release, superseded` trong `docs/RELEASES.md`, không xóa/sửa lịch sử. |
| 2026-11-19 | Thư mục `agent/` | Giữ public trong repo. |
| 2026-11-20 | Giấy phép (License) | **MIT** (`Copyright (c) 2026 sickyturtlez`) cho mã nguồn app; giữ nguyên thông báo & license bên thứ ba (FFmpeg GPL, PySide6 LGPL, ...). |
| 2026-11-20 | Phạm vi Update 16 (`1.0.16`) | Hoàn thiện đầy đủ Task 1 (đa ngôn ngữ → Việt), Task 2 (screenshot harness & DoD), Task 4 (license/audit/docs/report) và **sửa hết lỗi đang tồn đọng**. **Lược bỏ phần A/B test `edge-tts` ở Task 3** (vì sẽ thay hẳn TTS ở Update 17). |
| 2026-11-20 | TTS mới (VieNeu-TTS v3) | **Tách sang Update 17 (`1.0.17`)** theo `docs/PROPOSAL_VIENEU_TTS.md` (thay thế hoàn toàn `edge-tts` bằng VieNeu-TTS v3 ONNX offline). |
| 2026-11-20 | Cách phối hợp | Agent **trực tiếp sửa code, chạy test và verify** trong workspace; giao tiếp hoàn toàn bằng tiếng Việt theo `GEMINI.md`. |
| 2026-11-20 | Quản lý ngữ cảnh | Lưu `GEMINI.md` và `agent/PROJECT_STATE.md` trực tiếp trong Git (Cách 1). |

---

## 3. Trạng thái chi tiết Update 16 (`1.0.16`)

### Đã có nền tảng trong 11 commits cục bộ (`102528c..7cabd63`)
- [x] `LICENSE` (MIT), cập nhật `README.md`, bump version `1.0.16`.
- [x] `config.yaml`: `asr.language: auto`, `asr.language_min_confidence: 0.60`.
- [x] `s2_transcribe.py`: gọi `model.detect_language` (`faster-whisper` 1.2.1), mở rộng `_SENTENCE_ENDS`, chuyển `split_long` sang iterative (`7cabd63`), bỏ `initial_prompt` cho non-English, bỏ space-dedupe cho `zh/ja/th/lo/km`.
- [x] `s3_translate.py` / `llm/client.py` / `llm/prompts.py`: truyền `source_lang` vào prompt dịch.
- [x] `app/main.py` / `layout_check.py` / `definition_of_done.py`: sửa lỗi tab Advanced cho `dialog_settings_keys_*`, scroll `progress_group` cho ảnh `error` vs `finished`, thêm `_check_image_contents()`.
- [x] `tts_probe.py`: `classify_exact()` unwrap `__cause__` / `__context__`.

### Lỗi & Regression bắt buộc sửa trong Update 16
- [x] **Dọn rác đã lỡ commit / debug print (Bước 1):**
  - Đã `git rm` `scratch/images/a.png`, `scratch/images/b.png`, `scripts/scratch/test_assertions.py`, `scripts/scratch/test_image_checks.py`.
  - Đã gỡ `print("DEBUG: ...")` trong `src/dubtool/app/ui/main_window.py` (`on_finished`) và gỡ `test_dod_image_checks()` khỏi `scripts/definition_of_done.py` (chuyển thành unit test chuẩn trong `tests/test_definition_of_done.py`).
  - Đã gỡ vòng lặp cleanup `_orig` bị trùng trong `src/dubtool/app/main.py`.
  - Đã khôi phục `yaml.safe_load` trong `src/dubtool/fillers.py` (`tests/test_fillers.py` 19/19 PASS).
- [x] **Khôi phục `packaging/THIRD_PARTY.md` & cập nhật `packaging/RELEASE_NOTES_TEMPLATE.md` (Bước 1):** Đã khôi phục đầy đủ chuỗi `ffmpeg -version`, `--enable-gpl`, URL bản dựng/mã nguồn, Noto Sans SHA-256, bảng package; cập nhật license sang MIT (`Copyright (c) 2026 sickyturtlez`) và cập nhật privacy notice sang ngôn ngữ nguồn nhận diện được.
- [x] **`scripts/verify_release.py --audit` & `scripts/definition_of_done.py` Rule 9 (Bước 1):** Đã thay phần hardcode bằng kiểm toán thực tế tag/GitHub release/RELEASE_EVIDENCE.txt/docs/RELEASES.md, nối vào Rule 9 của `definition_of_done.py`, thêm unit test (4/4 PASS) và chạy kiểm chứng thật (`v1.0.15` `ok`, `v1.0.14` `pre-release, superseded`).
- [x] **Cập nhật `docs/RELEASES.md` & `README_BUILD.md` (Bước 1):** Đã sửa hash `v1.0.8` (`262f9ebd`), ghi rõ các release lịch sử, và ghi nhận trung thực giới hạn `pre-push` hook với `gh release create`.
- [x] **CLI `--source-language` & `--subtitles-only` (Bước 2):** Đã nối thật `--source-language` và `--subtitles-only` trong `src/dubtool/cli.py` (`cmd_run`, `cmd_stage`) và `src/dubtool/app/main.py` (`--headless-run`).
- [x] **UI dropdown ngôn ngữ (Bước 2):** Đã xóa `OptionsWidget.ui_lang_combo` trùng lặp; giữ duy nhất `MainWindow.lang_video_combo` với mặc định `"Tự động nhận diện"`, 14 ngôn ngữ (tên bản địa + Việt) + `"Khác…"` (`OtherLanguageDialog` liệt kê 100 ngôn ngữ Whisper), thêm vào `_busy_widgets()`, thêm `lang_status_lbl` hiển thị ngôn ngữ nhận diện + gợi ý `medium`.

### Phần còn thiếu theo `agent/AGENT_UPDATE_16.md` (trừ A/B `edge-tts` Task 3 đã lược bỏ)
- [x] **Task 1.1–1.2 (Detection & UI — Bước 2):**
  - Đã tạo `src/dubtool/languages.py` và hoàn thiện nhận diện 3 cửa sổ VAD (`detect_source_language`) lưu `windows: [{start, language, probability}]` vào `state.json`.
  - Đã hoàn thiện `LanguageUncertainDialog` (3 ứng viên GUI) + cảnh báo CLI/headless (`uncertain: true`), hỗ trợ `multilingual=True` cho video trộn ngôn ngữ (ghi cảnh báo rõ vì `faster-whisper 1.2.1` `Segment` không có field `language`).
  - Đã hoàn thiện luồng dừng video tiếng Việt (`VietnameseSourceDetected`, 0 LLM/TTS) kèm `VietnameseSourceDialog` (nút xuất `.srt` + bản chép lời) và CLI/headless `--subtitles-only`.
  - Đã từ chối model `*.en` cho non-English, cập nhật `first_run.privacy`, đưa `source_language` vào `stage_inputs_hash` (`s3`, `s5`).
- [x] **Task 1.3 (Language-aware text & readable export — Bước 2):**
  - Đã cập nhật `units.py`, `dub_units.py`, `punctuate.py`, `s3_translate.py`, `s5_fit_duration.py`, `llm/prompts.py`, `llm/client.py` (dấu câu đa ngôn ngữ, abbreviation chỉ cho English, Thai ngắt theo pause, tắt filler tiếng Anh cho non-English, `initial_prompt` chỉ cho English).
  - Đã cập nhật `s8_export_readable.py` (`{ngôn ngữ} → Tiếng Việt`, `Bản gốc ({ngôn ngữ})`, `w:bidi` + `w:rtl` + căn phải cho RTL, font fallback Windows cho CJK/Arabic/Thai/Devanagari, `export_subtitles_only()`).
- [x] **Task 1.5, Task 2.3 & Task 3.1 (Tests & Screenshots — Bước 3):**
  - Đã thêm 15 test toàn diện trong `tests/test_update16_multilingual.py` (kèm Task 3.1 root-cause test); toàn bộ bộ test đạt **599 passed, 2 skipped (SKIPS.md), 0 failed**; `--layout-check` **0 violations**.
  - Đã thêm 5 screenshot mới (`lang_dropdown_closed.png`, `lang_dropdown_open.png`, `dialog_language_uncertain.png`, `dialog_vietnamese_source.png`, `details_detected_language.png`), đồng bộ `README.md`, cập nhật `packaging/dubtool.spec` (MIT copyright, `LICENSE` + `fillers.yaml` + hiddenimports).
- [ ] **Task 1.6 (Verify thật trên `.exe` 1.0.16 — Bước 4):** Build `.exe` 1.0.16 sạch, chạy bộ clip đa ngôn ngữ + trộn + tiếng Việt + regression `test9.mp4` (`auto`).
- [ ] **Task 4 (Report & Release — Bước 5):** Hoàn thiện `REPORT_UPDATE_16.md` (<= 400 dòng, đủ bằng chứng thật), cập nhật commit hash thật vào `docs/RELEASES.md`, chạy `scripts/release.py 16`.

---

## 4. Lộ trình kế tiếp (Sau khi xong Update 16)

- **Mở session mới cho Update 17 (`1.0.17`):** Triển khai Phase 1–6 trong `docs/PROPOSAL_VIENEU_TTS.md` (thay thế hoàn toàn `edge-tts` bằng VieNeu-TTS v3 ONNX offline). Các điểm sẽ chốt đầu Update 17: giữ/xóa `fallback.py`, danh sách giọng UI (25/11 giọng hay lọc gọn), voice cloning (tách sang bản sau).
