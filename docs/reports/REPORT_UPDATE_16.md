# REPORT_UPDATE_16 — DubTool `1.0.16` (Update 16)

## 1. Summary Table

| Task | Status | Key Verification / Evidence |
| --- | --- | --- |
| Task 1 — Multilingual source detection → Vietnamese | **PASS** | `src/dubtool/languages.py`, `src/dubtool/stages/s2_transcribe.py`, `src/dubtool/stages/s8_export_readable.py`, `tests/test_update16_multilingual.py` (15/15 PASS), real exe runs on 9 clips + `test9.mp4` |
| Task 2 — Screenshot harness assertions & DoD uniqueness | **PASS** | `src/dubtool/app/main.py`, `scripts/definition_of_done.py`, `docs/images/INDEX.txt` (36 PNGs verified, 0 accidental byte-duplicates) |
| Task 3 — Voice failure root-cause classification (3.1) | **PASS** | `src/dubtool/tts_probe.py` (`classify_exact` unwraps `__cause__`/`__context__`, unit-tested) |
| Task 3 — `edge-tts` length analysis & A/B (3.2–3.4) | **WAIVED (user decision)** | Waived by project owner on 2026-11-20: `edge-tts` will be replaced completely by offline VieNeu-TTS v3 in Update 17 (`docs/PROPOSAL_VIENEU_TTS.md`) |
| Task 4 — MIT license, `docs/RELEASES.md`, real `--audit`, docs | **PASS** | `LICENSE`, `packaging/THIRD_PARTY.md`, `packaging/RELEASE_NOTES_TEMPLATE.md`, `docs/RELEASES.md`, `README_BUILD.md`, `scripts/verify_release.py` (`--audit` wired into `DOD_REPORT.md` Rule 9) |
| Mechanical DoD & Release Pipeline | **PASS** | `DOD_REPORT.md` (9/9 PASS), `RELEASE_EVIDENCE.txt`, `scripts/release.py` |

---

## 2. Release & Audit Evidence

Run via `scripts/release.py 16`, `scripts/definition_of_done.py --update 16`, and `scripts/verify_release.py --audit`.

Required verification commands (full raw post-release outputs recorded verbatim in `RELEASE_EVIDENCE.txt` and `DOD_REPORT.md`):
```bash
git log -1 --format=%H
git status -sb
git ls-remote origin refs/heads/main
```

Pre-release `scripts/verify_release.py --audit` snapshot (live GitHub API vs local repo):

| Tag | Tag Commit | GitHub Release | Evidence / BUILD_INFO | Status |
| --- | --- | --- | --- | --- |
| `v1.0.15` | `102528cd` | Yes | `BUILD_INFO=102528cd, sha256=ba9990083f37…` | ok |
| `v1.0.14` | `045660d0` | Pre-release | Mismatched tree (`63c85a18` != `045660d0`), superseded by `v1.0.15` | pre-release, superseded |
| `v1.0.13` | `63c85a18` | No | Historical | tag only |
| `v1.0.12` | `04531dd3` | Yes | missing RELEASE_EVIDENCE (pre-U15) | no evidence |
| `v1.0.11` | `0bcc7230` | Yes | missing RELEASE_EVIDENCE (pre-U15) | no evidence |
| `v1.0.10` | `1f25a1a9` | Yes | missing RELEASE_EVIDENCE (pre-U15) | no evidence |
| `v1.0.9`–`v1.0.6` | `ad373d84`–`1d8c6428` | No | Historical (`v1.0.8` / `update-8` added locally on `262f9ebd`) | tag only |

---

## 3. Task 1 — Multilingual Source Detection → Vietnamese

### 3.1 `faster-whisper` inspection
- Installed version: **`faster-whisper 1.2.1`**.
- `WhisperModel.detect_language(audio, vad_filter, language_detection_segments, language_detection_threshold)` returns `(lang, prob, all_probs)`.
- `WhisperModel.transcribe(..., multilingual=True)` **is supported** (re-detects language per 30s encoder chunk), **but `Segment` has no `language` field** (`id, seek, start, end, text, tokens, avg_logprob, compression_ratio, no_speech_prob, words, temperature`). Therefore, mixed-language videos enable `multilingual=True` when two languages each occupy ≥25% of speech windows and emit a clear warning (`"video có nhiều ngôn ngữ; một số đoạn có thể nhận dạng sai"`).
- `faster_whisper.vad.get_speech_timestamps` is used to sample up to 3 speech windows (start, middle, end); `faster_whisper.tokenizer._LANGUAGE_CODES` supplies all 100 Whisper languages for the `"Khác…"` dialog.

### 3.2 Real packaged-exe verification (`DubTool.exe --headless-run --model gemini-3.5-flash-lite --source-language auto`)
*Note: synthetic `edge-tts` speech is cleaner than real-world recordings; real-world ASR accuracy may be lower. Subjective translation and voice quality are left for the user to judge.*

| Clip | Dur (s) | Detected (`prob`) | 3 VAD Windows `(start_s, lang, prob)` | Units | Exe Result & Readable / Subtitles |
| --- | ---: | --- | --- | ---: | --- |
| `ja` | 54.3 | `ja` (`99.86%`) | `(0.0, ja, 0.998), (17.7, ja, 0.999), (35.4, ja, 0.999)` | 8 | **PASS (exit 0)** — `clip_ja_vi.mp4` + `clip_ja_vi_translation.docx` (`Yu Gothic UI` + `Arial`) |
| `zh` | 45.9 | `zh` (`99.68%`) | `(0.0, zh, 0.999), (15.2, zh, 0.993), (30.3, zh, 0.999)` | 8 | s1–s3 PASS; s4 stopped (`tts_unavailable`: Microsoft `edge-tts` dropped `[4]`, 7/8 cached) |
| `ko` | 50.3 | `ko` (`98.67%`) | `(0.0, ko, 0.991), (16.5, ko, 0.982), (33.1, ko, 0.987)` | 9 | s1–s3 PASS; s4 stopped (`tts_unavailable`: `edge-tts` dropped `[3,4,6]`, 6/9 cached) |
| `es` | 44.1 | `es` (`98.70%`) | `(0.0, es, 0.986), (14.5, es, 0.990), (29.0, es, 0.985)` | 8 | **PASS (exit 0)** — `clip_es_vi.mp4` + `clip_es_vi_translation.docx` |
| `fr` | 45.9 | `fr` (`99.74%`) | `(0.0, fr, 0.997), (15.0, fr, 0.996), (29.9, fr, 0.999)` | 5 | s1–s3 PASS; s4 stopped (`tts_unavailable`: `edge-tts` dropped `[0,4]`, 3/5 cached) |
| `de` | 51.7 | `de` (`99.31%`) | `(0.0, de, 0.990), (16.9, de, 0.992), (33.7, de, 0.997)` | 9 | s1–s3 PASS; s4 stopped (`tts_unavailable`: `edge-tts` dropped `[7]`, 8/9 cached) |
| `ar` | 60.8 | `ar` (`99.14%`) | `(0.0, ar, 0.997), (20.0, ar, 0.990), (40.0, ar, 0.988)` | 7 | **PASS (exit 0 on resume)** — `clip_ar_vi.mp4` + `clip_ar_vi_translation.docx` (**RTL XML verified: `w:bidi=7, w:rtl=7, right_align=7`, `Segoe UI` + `Arial`**) |
| `mixed_en_es` | 42.9 | `es` (`86.46%`) | `(0.0, es, 0.737), (13.9, es, 0.877), (27.8, es, 0.979)` | 4 | Rapid 1-sentence alternation caused `es` to dominate all 3 windows (honest limitation); s1–s3 PASS; s4 `tts_unavailable` (`[0,2]`) |
| `vi` (stop) | 45.3 | `vi` (`99.51%`) | `(0.0, vi, 0.996), (14.9, vi, 0.992), (29.8, vi, 0.997)` | — | **PASS (exit 0)** — `code=source_is_vietnamese` (`"Video có vẻ đã là tiếng Việt (độ tin cậy 100%). Không cần dịch."`), 0 LLM / 0 TTS |
| `vi` (`--subtitles-only`) | 45.3 | `vi` (`99.51%`) | resumed `s2` (0.3s) | — | **PASS (exit 0)** — `code=subtitles_only`, wrote `clip_vi.srt` + `clip_vi_transcript.txt`, 0 LLM / 0 TTS |

### 3.3 Sample raw ASR & translation outputs (from real exe runs)
- **`ja` (header `Bản gốc (Tiếng Nhật)`):**
  1. `おはようございます今日は再生可能エネルギーの未来についてお話します` → `Xin chào các bạn, hôm nay tôi sẽ nói về tương lai của năng lượng tái tạo.`
  2. `太陽光発電はここ10年でとても安くなり多くの家庭で使われています` → `Điện mặt trời đã trở nên rất rẻ trong mười năm qua và được sử dụng ở nhiều hộ gia đình.`
  3. `風力発電も海の上で大きく成長していて安定した電力を供給できます` → `Điện gió cũng đang phát triển mạnh mẽ trên biển và có thể cung cấp nguồn điện ổn định.`
- **`zh`:**
  1. `大家好,今天我们来聊一聊海洋探索的最新进展。` → `Xin chào các bạn, hôm nay chúng ta cùng thảo luận về những tiến bộ mới nhất trong việc khám phá đại dương.`
  2. `地球表面有超过70的面积,被海洋覆盖,但深海依然充满未知。` → `Hơn 70 phần trăm bề mặt Trái Đất được đại dương bao phủ, nhưng biển sâu vẫn đầy rẫy những điều chưa biết.`
- **`ko`:**
  1. `안녕하세요.` → `Xin chào bạn.`
  2. `오늘은 도시 농업과 수직 농장에 대해 알아보겠습니다.` → `Hôm nay, chúng ta sẽ tìm hiểu về nông nghiệp đô thị và nông trại thẳng đứng.`
- **`es` (header `Bản gốc (Tiếng Tây Ban Nha)`):**
  1. `¿Sabías que las bibliotecas públicas están cambiando rápidamente en todo el mundo?` → `Bạn có biết rằng các thư viện công cộng đang thay đổi nhanh chóng trên toàn thế giới không?`
  2. `Antes eran lugares silenciosos dedicados casi únicamente a prestar libros en papel.` → `Trước đây chúng là những nơi yên tĩnh gần như chỉ dành riêng cho việc cho mượn sách giấy.`
- **`fr`:**
  1. `Bonjour à tous. Aujourd'hui, nous découvrons l'histoire fascinante de l'horlogerie...` → `Xin chào tất cả các bạn. Hôm nay, chúng ta khám phá lịch sử hấp dẫn của ngành chế tạo đồng hồ...`
- **`de`:**
  1. `Guten Tag.` → `Xin chào.`
  2. `Heute sprechen wir über moderne Eisenbahnnetze in Europa.` → `Hôm nay chúng ta sẽ nói về các mạng lưới đường sắt hiện đại ở châu Âu.`
- **`ar` (header `Bản gốc (Tiếng Ả Rập)`, RTL verified `w:bidi=7, w:rtl=7, right_align=7`):**
  1. `مرحبا بكم جميعا` → `Xin chào tất cả các bạn.`
  2. `سنتحدث اليوم عن أهمية علم الفلك في تاريخ الحضارة الإنسانية` → `Hôm nay chúng ta sẽ bàn về tầm quan trọng của thiên văn học trong lịch sử văn minh nhân loại.`
  3. `هل تعلم أن التلسكوبات الحديثة تستطيع رصد مجرات بعيدة جدا تشكلت في بدايات الكون؟` → `Bạn có biết rằng các kính thiên văn hiện đại có thể quan sát những thiên hà rất xa được hình thành từ thuở ban đầu của vũ trụ không?`

### 3.4 English regression (`test9.mp4`, `--source-language auto`)
- Packaged exe detected **`en` (`99.52%`)** across 3 VAD windows `(1.49s: 99.94%, 79.79s: 99.84%, 348.24s: 98.77%)` in 42.9s.
- Structural comparison vs Update 15 baseline (`REPORT_UPDATE_15.md`):
  - Segments: **80** vs **80** (exact match)
  - Non-terminal segments: **20** vs **20** (exact match)
  - Sentence units: **62** vs **62** (exact match — 0 regression)

---

## 4. Task 2 — Screenshots & DoD Image Checks

- Pre-`grab()` state assertions added in `src/dubtool/app/main.py` for Settings tabs, per-provider key views (`dialog_settings_keys_{gemini,openai,anthropic}.png` verified on `Khóa API` tab), `error` vs `finished` states (with scroll-to-bottom), and 5 new Update 16 shots: `docs/images/lang_dropdown_closed.png`, `docs/images/lang_dropdown_open.png`, `docs/images/dialog_language_uncertain.png`, `docs/images/dialog_vietnamese_source.png`, `docs/images/details_detected_language.png`.
- `scripts/definition_of_done.py` rejects byte-identical non-canonical PNGs and byte-identical states; `tests/test_definition_of_done.py` verifies fake image folders and Rule 9 audit failures.
- Visual inspection of captured PNGs caught and fixed two issues before release: Qt `&` mnemonic stripping on the Vietnamese subtitles button (`&` → `và`), and `details_detected_language.png` using 1100×800 two-column layout so the detected language line and open details log render unobstructed.

---

## 5. Task 3 — Voice Failure Classification & Scope Note

- `src/dubtool/tts_probe.py` (`classify_exact`) walks nested `__cause__` and `__context__` chains to the innermost root cause (`NoAudioReceived`, `handshake_503`, `timeout`, `http_429`, or exact class name — never generic `TTSFailure`). Verified in `tests/test_update16_multilingual.py`.
- Tasks 3.2–3.4 (`edge-tts` 120-text length study and real-video A/B/B/A) were **waived by the user on 2026-11-20** because `edge-tts` (which also caused the 5 `tts_unavailable` stops in §3.2 above) is scheduled to be completely replaced by offline ONNX **VieNeu-TTS v3** in **Update 17** (`docs/PROPOSAL_VIENEU_TTS.md`).

---

## 6. Quyết định đã thay người dùng (và cách hoàn tác)

| Chủ đề | Quyết định | Lý do | Cách hoàn tác |
| --- | --- | --- | --- |
| Giấy phép mã nguồn ứng dụng | **MIT** (`Copyright (c) 2026 sickyturtlez`), cập nhật `LICENSE`, `README.md`, `packaging/THIRD_PARTY.md`, `packaging/RELEASE_NOTES_TEMPLATE.md`, `packaging/dubtool.spec` (xác nhận bởi người dùng 2026-11-20) | Đơn giản, tương thích với việc gọi FFmpeg GPL như chương trình riêng và thư viện LGPL | Thay tệp `LICENSE` và cập nhật các tệp tài liệu ở bản phát hành sau |
| Bản `v1.0.14` | Giữ trạng thái `pre-release, superseded` trong `docs/RELEASES.md` | Đã phát hành từ cây làm việc lệch tag; giữ lịch sử | Chỉnh cờ release trên GitHub nếu muốn |
| Thư mục `agent/` | Giữ public trong repo (thêm `agent/PROJECT_STATE.md` + `GEMINI.md`) | Theo xác nhận của chủ dự án | Xóa hoặc đưa vào `.gitignore` |
| Tag thiếu `v1.0.8` / `update-8` | Gắn cục bộ vào `262f9ebd` | Khớp duy nhất 1 commit Update 8 trong lịch sử | `git tag -d v1.0.8 update-8` |
| Cài đặt TTS Update 14 vs 12 | Không chạy A/B `edge-tts` (giữ nguyên cho 1.0.16, chuyển sang VieNeu-TTS v3 ở Update 17) | Người dùng chốt lược bỏ A/B `edge-tts` ngày 2026-11-20 | Chạy `scripts/validate_voice_settings.py` nếu cần |
| Chính sách ngôn ngữ nguồn | Mặc định `auto`; tiếng Việt thì dừng + đề xuất `.srt`/transcript; không chắc thì hỏi (GUI) / cảnh báo (CLI); Whisper mặc định `small` + gợi ý `medium` | Tránh dịch ngược tiếng Việt và tránh nhận dạng sai âm thầm | Đổi `asr.language` trong `config.yaml` |
| Ngôn ngữ đích | Chỉ tiếng Việt | Yêu cầu cốt lõi của sản phẩm | Mở rộng kiến trúc ở bản sau |
| Quy trình phát hành | `scripts/release.py` + `scripts/verify_release.py --audit` (Rule 9 trong DoD) | Bù giới hạn của `pre-push` hook với `gh release create` (đã ghi rõ trong `docs/RELEASES.md` và `README_BUILD.md`) | Chỉnh `scripts/definition_of_done.py` |

---

## 7. Build, Tests & Limitations

- **Offline test suite:** **599 passed, 2 skipped** (`tests/test_cleanup.py`, `tests/test_housekeeping.py` — Windows symlink privilege, documented in `tests/SKIPS.md`), **0 failed**.
- **Clean-env packaged exe checks:** `--version`, `--selftest`, `--layout-check` (0 violations), `--gui-smoke` (10/10 PASS), `--screenshots` (36 PNGs, all PASS).
- **Exact build artifact paths, sizes, SHA-256, and review zip SHA-256:** generated mechanically from clean `git archive HEAD` by `scripts/release.py 16` and recorded in `DOD_REPORT.md` and `RELEASE_EVIDENCE.txt`.
- **Not verified:** real-world noisy non-English speech accuracy (synthetic `edge-tts` voices used), subjective audio/translation quality, Anthropic Claude live key, OpenAI live key (depleted credit), rendering on user's physical display, and `zh`/`ko`/`fr`/`de`/`mixed_en_es` stage-4 completion on Microsoft `edge-tts` (dropped due to endpoint instability, tracked for VieNeu-TTS v3 in Update 17).
- **Noticed but deferred:** `edge-tts` WebSocket drops (~50% on current Microsoft endpoint) remain the main bottleneck in stage 4 → addressed cleanly in Update 17 (`docs/PROPOSAL_VIENEU_TTS.md`).
