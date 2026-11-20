# DubTool — Workspace Assistant Rules (`GEMINI.md`)

Tệp này được Antigravity tự động nạp vào mọi lượt hội thoại (kể cả sau khi compact context hoặc khi mở session mới). Áp dụng cùng với `AGENTS.md` (các quy tắc an toàn trong `AGENTS.md` luôn giữ nguyên hiệu lực).

## 1. Quy tắc giao tiếp bắt buộc

- **Giao tiếp hoàn toàn bằng tiếng Việt** tự nhiên, rõ ràng, dễ hiểu.
- **Giữ nguyên thuật ngữ kỹ thuật tiếng Anh** khi là tên công nghệ, framework, API, function, variable, error message hoặc thuật ngữ chuyên ngành (không dịch gượng ép).
- Giải thích vấn đề kỹ thuật bằng tiếng Việt.
- Khi hướng dẫn người dùng làm một việc, chia thành **các bước cụ thể, có thứ tự**.
- Khi phân tích code, luôn nêu rõ: **nguyên nhân**, **cách giải quyết**, và **ảnh hưởng có thể xảy ra**.
- **Ngắn gọn, đi thẳng vào trọng tâm**; không lặp lại thông tin đã biết.
- **Không tự giả định** người dùng đã thực hiện một bước nào đó khi người dùng chưa xác nhận.

## 2. Giao thức chống mất ngữ cảnh (Compact & Session mới)

1. **Đầu session mới hoặc ngay sau khi context bị compact:** Đọc [agent/PROJECT_STATE.md](agent/PROJECT_STATE.md) trước khi lập kế hoạch hay sửa code để khôi phục chính xác trạng thái hiện tại, các quyết định đã chốt và checklist đang dở.
2. **Sau mỗi milestone/task hoàn thành** (và trước khi người dùng chạy `/compact` hoặc kết thúc phiên): Cập nhật ngắn gọn [agent/PROJECT_STATE.md](agent/PROJECT_STATE.md) (trạng thái git, việc đã xong, bug còn mở, bước tiếp theo).
3. **Không đoán hành động trong quá khứ sau compact:** Nếu cần xác minh việc đã làm trước đó, kiểm tra `transcript_full.jsonl` và log thực tế trên đĩa (theo `AGENTS.md` rule 4).
4. **Phân tách session hợp lý:** Mỗi Update lớn nên hoàn tất gọn trong một session sạch (cập nhật `PROJECT_STATE.md` cuối phiên); dùng subagent `research` cho các tác vụ đọc/quét diện rộng để tránh phình context chính.

## 3. Ràng buộc sản phẩm & quy trình kỹ thuật của DubTool

- **Mục tiêu cốt lõi:** Ứng dụng desktop Windows (Python + PySide6, đóng gói `.exe` bằng PyInstaller) nhận diện ngôn ngữ nguồn của video (hoặc chọn tay) và xuất video lồng tiếng Việt:
  - Giữ nguyên hình ảnh video gốc, **không bao giờ ghi đè file input**.
  - Audio tiếng Việt đè lên audio gốc đã hạ âm lượng (`original_gain_db`), có limiter chống clipping.
  - Burn phụ đề tiếng Việt khớp thời lượng audio thực tế sau fit/shorten, kèm file bản dịch dễ đọc (`.docx`/`.txt`) cho người dùng cuối.
  - Đầu ra luôn là tiếng Việt; nếu video đầu vào đã là tiếng Việt thì dừng trước bước dịch và đề xuất xuất `.srt` / transcript.
- **Không tin báo cáo hay DoD một cách mù quáng:** Luôn kiểm chứng bằng code thực tế, git diff, test chạy thật, ảnh chụp màn hình thật và chạy `.exe` thật trong môi trường sạch.
- **Bảo mật & sạch repo:**
  - Tuyệt đối không lộ `.env` hay API key vào chat, log, screenshot, review zip hoặc Git.
  - Giữ thư mục build ngoài repo (`DUBTOOL_BUILD_ROOT`). Không commit file tạm/scratch vào repo.
  - Không dùng `git add -A`, không sửa lịch sử Git (`--force`), không xóa/dời tag hay release đã công khai.
- **Quyết định cố định của chủ dự án:**
  - **Không** thêm chữ ký số hay author watermark `sickyturtlez` (đã hủy từ Update 5).
  - Giấy phép mã nguồn ứng dụng: **MIT (`Copyright (c) 2026 sickyturtlez`)**; giữ nguyên toàn văn thông báo & giấy phép bên thứ ba (FFmpeg GPL, PySide6 LGPL, v.v.).
  - Thư mục `agent/` được phép công khai trên repo `namtit2932k/dubtool`.
  - Release luôn đi qua `scripts/release.py <N>` từ commit sạch (`git archive`), có kiểm chứng `DOD_REPORT.md`, `RELEASE_EVIDENCE.txt` và `verify_release.py --audit`.
  - Người dùng cho phép agent **trực tiếp sửa code, chạy test và verify** trong workspace.
