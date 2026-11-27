/* Lightweight EN / VI language switcher for the DubTool landing page.
   Default language is English ("en"). Saves user choice in localStorage and
   supports ?lang=vi or ?lang=en URL parameters. */

const STRINGS = {
  en: {
    "meta.title": "DubTool: Free Windows app to dub foreign videos into Vietnamese",
    "meta.desc": "Turn foreign-language videos into natural Vietnamese-dubbed videos with clean subtitles on Windows. No coding needed, free and open source.",
    "skip": "Skip to content",
    "nav.guide": "Guide",
    "nav.menu.start": "Get started",
    "nav.menu.s1.t": "4-step setup guide",
    "nav.menu.s1.d": "From download to your first video",
    "nav.menu.s2.t": "Live app preview",
    "nav.menu.s2.d": "See every step as it runs",
    "nav.menu.s3.t": "Built-in voices",
    "nav.menu.s3.d": "Listen offline before you dub",
    "nav.menu.more": "Options & help",
    "nav.menu.m1.t": "Free translation keys",
    "nav.menu.m1.d": "Use Google Gemini or Groq for free",
    "nav.menu.m2.t": "100% offline mode",
    "nav.menu.m2.d": "Keep private videos on your PC",
    "nav.menu.m3.t": "Common questions",
    "nav.menu.m3.d": "PC requirements, privacy, Mac support",
    "nav.card.title": "Latest Windows build",
    "nav.card.sub": "Single zip file, unzip and open directly",
    "nav.card.note.t": "DubTool v1.0.23",
    "nav.card.note.time": "latest",
    "nav.card.note.b": "Clearer voice mix & cleaner sentence translation",
    "nav.features": "Features",
    "nav.modes": "Translators",
    "nav.faq": "FAQ",
    "nav.github": "GitHub",
    "cta.download": "Download DubTool",
    "cta.guide": "See how to use",

    "hero.h1": "Turn foreign videos into Vietnamese",
    "hero.lead": "DubTool listens to your video, translates it into natural Vietnamese, adds built-in voices, and exports a ready-to-watch Windows video. Free and open source.",
    "hero.proof": "Works on Windows 10 and 11 (64-bit). No extra installs needed, and your original video stays untouched.",
    "hero.win.title": "DubTool v1.0.23 · Vietnamese Video Dubbing",
    "hero.win.side1": "Progress",
    "hero.win.side2": "Options",
    "hero.win.side3": "API Keys",
    "hero.win.side4": "Voices",
    "hero.win.side5": "Script",
    "hero.win.label": "Built-in voices",
    "hero.win.head": "Automatic steps",
    "hero.win.btn": "Start",
    "hero.win.t1": "All done",
    "hero.win.t2": "Auto-detected",
    "hero.win.t3": "Vietnamese",
    "hero.r1.t": "Read video audio",
    "hero.r1.s": "Keeps original picture untouched",
    "hero.r2.t": "Listen to speech",
    "hero.r2.s": "Auto-detected Chinese (99.9%)",
    "hero.r3.t": "Translate sentences",
    "hero.r3.s": "Free Gemini + Groq · 1,543 lines",
    "hero.r4.t": "Speak in Vietnamese",
    "hero.r4.s": "Trúc Ly · natural local voice",
    "hero.r5.t": "Match video timing",
    "hero.r5.s": "No overlapping speech",
    "hero.r6.t": "Save video & subtitles",
    "hero.r6.s": "Readable dark box behind subtitles",
    "hero.chip.done": "Done",
    "hero.note.h": "Ready to watch",
    "hero.note.l1": "<b>movie_vi.mp4</b> · Full HD video",
    "hero.note.l2": "<b>Synced voice & subtitles</b> · no overlap",
    "hero.note.l3": "Includes <b>Word script (.docx)</b>",

    "guide.h2": "How to use DubTool in 4 simple steps",
    "guide.sub": "No programming or extra installs. Set up once in about two minutes.",
    "guide.s1.h": "Unzip and open DubTool",
    "guide.s1.p": "Extract the Windows zip and double-click DubTool.exe. Everything is built in.",
    "guide.s2.h": "Paste a free translation key",
    "guide.s2.p": "Open Settings → API Keys, paste a Google or Groq key, and click Test all.",
    "guide.s3.h": "Drop your video and pick a voice",
    "guide.s3.p": "Choose an output folder, leave Auto-detect on, and preview any local voice.",
    "guide.s4.h": "Click Generate for video & Word script",
    "guide.s4.p": "Your original stays untouched; you get a new Vietnamese MP4 and Word table.",
    "guide.tip.title": "Already in Vietnamese?",
    "guide.tip.text": "DubTool detects it automatically, stops before using any key, and offers clean subtitles instead.",
    "guide.mock.h": "DubTool · First-time setup",
    "guide.mock.ok": "Ready to dub",
    "guide.mock.r1.t": "Free Gemini + Groq keys",
    "guide.mock.r1.s": "Settings → API Keys → Test all",
    "guide.mock.r1.ok": "Verified",
    "guide.mock.r2.t": "Built-in Vietnamese voice",
    "guide.mock.r2.s": "Trúc Ly · Preview voice offline",
    "guide.mock.r2.ok": "On PC",
    "guide.mock.r3.t": "Safe output folder",
    "guide.mock.r3.s": "Saves MP4 + SRT + Word (.docx)",
    "guide.mock.r3.ok": "Protected",
    "guide.mock.foot": "One-time 2-minute setup · Everything runs from a single Windows folder",

    "ws.h2": "One clear DubTool window tracks every step",
    "ws.lead": "You always see which step is running, how many lines are left, and the exact files saved. If you pause or close the app, you can resume right where it stopped.",
    "ws.link": "See screenshots on GitHub",
    "ws.win.crumb": "Recent videos",
    "ws.win.search": "Search",
    "ws.win.s1": "Recent",
    "ws.win.s2": "Finished",
    "ws.win.s3": "Paused",
    "ws.win.lang": "Languages",
    "ws.win.l1": "Chinese",
    "ws.win.l2": "English",
    "ws.win.l3": "Japanese",
    "ws.win.c1": "Video",
    "ws.win.c2": "Details",
    "ws.win.c3": "Status",
    "ws.win.c4": "Time",
    "ws.r1.s": "2:33:04 · 1,543 lines · Trúc Ly",
    "ws.r2.s": "29:15 · Vertical · 264 lines",
    "ws.r3.s": "18:40 · Full HD · Đức Trí",
    "ws.r4.s": "14:22 · Full HD · Mai Anh",
    "ws.r5.s": "11:05 · Full HD · Trúc Ly",
    "ws.r6.s": "42:10 · Full HD · Minh Quân",
    "ws.r7.s": "35:48 · Full HD · Trúc Ly",
    "ws.r8.s": "22:15 · Full HD · Bảo Ngọc",
    "ws.r9.s": "03:18 · Full HD · Trúc Ly",
    "ws.st.open": "Queued",
    "ws.st.done": "Done",
    "ws.st.run": "Running",
    "ws.pipe.chip": "Run",
    "ws.pipe.chipDone": "Done",
    "ws.pipe.msg": "Free Auto-Rotate · Trúc Ly voice · Clean subtitles",
    "ws.pipe.btn": "Cancel",
    "ws.pipe.btnDone": "Run again",
    "ws.pipe.p1": "Audio",
    "ws.pipe.p2": "Listen",
    "ws.pipe.p3": "Lines",
    "ws.pipe.p4": "Voice",
    "ws.pipe.p5": "Save",
    "ws.pipe.c1": "read original sound",
    "ws.pipe.c2": "detected Chinese",
    "ws.pipe.c3": "1,543 sentences",
    "ws.pipe.c4": "Trúc Ly (local)",
    "ws.pipe.c5": "MP4 + SRT + Word",
    "ws.pipe.wait": "Next",
    "ws.log.1": "<b>[1/5]</b> Extracted original audio cleanly in 7 seconds",
    "ws.log.2": "<b>[2/5]</b> Detected Chinese speech (99.9%) · 1,543 sentences",
    "ws.log.3": "<b>[3/5]</b> Translating with free Gemini + Groq keys automatically",
    "ws.log.4": "<b>[3/5]</b> Verified complete Vietnamese sentences · zero foreign letters left",
    "ws.log.5": "<b>[4/5]</b> Generated 1,543 natural Vietnamese voice clips on this PC",
    "ws.log.6": "<b>[5/5]</b> Synced voice timing · 0 overlaps · kept movie background sound",
    "ws.log.7": "<b>[done]</b> Saved drama_ep01_vi.mp4 and drama_ep01_vi_translation.docx",

    "feat.h2.a": "Built to be simple and reliable.",
    "feat.h2.b": "Your video never leaves your computer.",
    "feat.c1.h": "Free translation that keeps going",
    "feat.c1.p": "Add multiple free Gemini or Groq keys. If one hits its limit, DubTool switches automatically.",
    "feat.n.times": "now|1m|3m",
    "feat.n1.t": "Switched to free Groq key",
    "feat.n1.time": "now",
    "feat.n1.b": "Google busy for a moment · no progress lost",
    "feat.n2.t": "Fixed 2 untranslated lines",
    "feat.n2.time": "1m",
    "feat.n2.b": "Checked automatically · kept clean lines",
    "feat.n3.t": "All 1,543 lines translated",
    "feat.n3.time": "3m",
    "feat.n3.b": "Natural movie dialogue · zero leftover characters",
    "feat.c2.h": "Natural Vietnamese voices built in",
    "feat.c2.p": "Pick from 25 studio voices or 11 lightweight voices for older PCs, and preview any offline.",
    "feat.tg.title": "Safe defaults out of the box",
    "feat.tg.tpl": "{n} / 5 on",
    "feat.tg1.t": "Natural Vietnamese voice",
    "feat.tg1.s": "Runs on your PC · Trúc Ly voice",
    "feat.tg2.t": "Dark box behind subtitles",
    "feat.tg2.s": "Easy to read over original text",
    "feat.tg3.t": "Keep movie music & effects",
    "feat.tg3.s": "Lowers original voice softly",
    "feat.tg4.t": "Save editable Word script",
    "feat.tg4.s": "Side-by-side original & Vietnamese",
    "feat.tg5.t": "Overwrite original video",
    "feat.tg5.s": "Always off to protect your file",
    "feat.c3.h": "Ready-to-share video, subtitles and Word file",
    "feat.c3.p": "Auto-sizes subtitles for widescreen and vertical clips, and never overwrites your source.",
    "feat.f.status": "6 output & project files",
    "feat.f.reading": "Saving {n} of {total}",
    "feat.f1.k": "Dubbed video",
    "feat.f2.k": "Word script",
    "feat.f3.k": "Vietnamese subs",
    "feat.f4.k": "Translated lines",
    "feat.f5.k": "Voice timing",
    "feat.f6.k": "Saved settings",
    "feat.c4.h": "Works with 5 translators and built-in tools",
    "feat.c4.p": "Start free with Gemini and Groq, use OpenAI or Claude anytime, or run 100% offline with Ollama.",

    "stmt.h2": "DubTool keeps the original picture sharp and the sound natural",
    "stmt.lead": "Every Vietnamese sentence is paced to fit the speaker's timing and mixed cleanly over lowered movie background sound so nothing clips or overlaps, even on a 2.5-hour film.",
    "stmt.link": "Explore source code on GitHub",
    "stmt.f1.dt": "2.5× faster",
    "stmt.f1.dd": "Tested on a normal PC without a graphics card: a 2h 33m Full HD movie finished in 61 minutes.",
    "stmt.f2.dt": "0 overlaps",
    "stmt.f2.dd": "All 1,543 spoken lines stayed in sync with the video, with clean subtitles and zero leftover foreign script.",
    "stmt.f3.dt": "0 extra installs",
    "stmt.f3.dd": "One Windows 64-bit zip includes everything needed. Just unzip and double-click DubTool.exe.",
    "stmt.bar.t": "Real timing for a 2h 33m Full HD movie on a regular Windows PC (61 min total)",
    "stmt.bar.m": "100% finished · 0 errors · original file untouched",
    "stmt.bar.l1": "Listen 15m",
    "stmt.bar.l2": "Translate 3m",
    "stmt.bar.l3": "Voice 17m",
    "stmt.bar.l4": "Sync & mix 4m",
    "stmt.bar.l5": "Save subtitles 21m",

    "prov.h2": "3 easy ways to translate with DubTool",
    "prov.sub": "Listening and Vietnamese voice generation always run free on your PC. You only choose how the text gets translated.",
    "prov.tag": "Recommended",
    "prov.c1.h": "Free mode: Google & Groq",
    "prov.c1.m": "Best for almost everyone · Free keys",
    "prov.k1": "Cost",
    "prov.k2": "What leaves your PC",
    "prov.k3": "Best for",
    "prov.c1.v1": "$0 using free API keys",
    "prov.c1.v2": "Only the spoken text to translate",
    "prov.c1.v3": "Long movies, YouTube videos, courses",
    "prov.c2.h": "Paid translators: OpenAI & Claude",
    "prov.c2.m": "Optional · Use your own account",
    "prov.c2.v1": "Standard pay-as-you-go AI usage",
    "prov.c2.v2": "Only the spoken text to translate",
    "prov.c2.v3": "Technical talks with custom glossaries",
    "prov.c3.h": "100% Offline: Ollama on your PC",
    "prov.c3.m": "No internet required · Private",
    "prov.c3.v1": "$0, runs fully on your computer",
    "prov.c3.v2": "Nothing leaves your computer",
    "prov.c3.v3": "Private company or personal videos",

    "faq.h2": "Common questions about DubTool",
    "faq.sub": "Plain answers about cost, PC requirements, privacy, and current limits before you download.",
    "faq.link": "Ask a question on GitHub",
    "faq.q1": "Is DubTool completely free and without watermarks?",
    "faq.a1": "Yes. DubTool is free, open-source software (MIT license) with no video length limit and no logos stamped on your exported video.",
    "faq.q2": "Does DubTool need a gaming PC or graphics card?",
    "faq.a2": "No. DubTool runs directly on a normal Windows CPU. If your PC has less memory, switch to the lightweight Nano voice mode in Settings.",
    "faq.q3": "Is it hard to get a free Google or Groq translation key?",
    "faq.a3": "No. Sign in to Google AI Studio or Groq Console, click Create API Key, copy the text, and paste it into DubTool Settings once. It takes about two minutes.",
    "faq.q4": "Will my original video be uploaded to the internet?",
    "faq.a4": "Never. Your video and audio stay on your computer. Only the short transcribed text lines are sent for translation, or nothing at all if you choose Ollama.",
    "faq.q5": "Does DubTool work on Mac or require installing Python?",
    "faq.a5": "Right now DubTool is for Windows 10 and 11 (64-bit) only, and you do not need to install Python or FFmpeg. Just unzip and run DubTool.exe.",

    "close.meta": "v1.0.23 · Windows 10 & 11 (64-bit) · Free & Open Source",
    "close.h2": "Download DubTool for Windows",
    "close.lead": "Unzip one file, paste a free key, and start dubbing foreign videos into natural Vietnamese.",
    "close.cta": "Download DubTool for Windows (.zip)",
    "close.note": "MIT License · No watermarks · Original video stays untouched",
    "close.github": "View project on GitHub",
    "foot.lede": "Free Windows app that dubs foreign videos into natural Vietnamese.",
    "foot.c1": "Guide",
    "foot.c1.1": "4-step setup",
    "foot.c1.2": "Live preview",
    "foot.c2": "Features",
    "foot.c2.1": "Free translation",
    "foot.c2.2": "Local voices",
    "foot.c3": "Help",
    "foot.c3.1": "3 translator modes",
    "foot.c3.2": "Common questions",
    "foot.c4": "Project",
    "foot.c4.1": "GitHub repository",
    "foot.c4.2": "Vietnamese README",
    "foot.lic": "MIT License",
    "foot.tm": "Product names and logos are trademarks of their respective owners."
  },
  vi: {
    "meta.title": "DubTool: Phần mềm miễn phí lồng tiếng Việt cho video trên Windows",
    "meta.desc": "Chuyển video tiếng Trung, Anh, Nhật, Hàn sang video lồng tiếng Việt tự nhiên kèm phụ đề rõ nét trên Windows. Dễ dùng, không cần biết kỹ thuật.",
    "skip": "Chuyển tới nội dung",
    "nav.guide": "Hướng dẫn",
    "nav.menu.start": "Bắt đầu nhanh",
    "nav.menu.s1.t": "Hướng dẫn 4 bước",
    "nav.menu.s1.d": "Từ lúc tải về đến khi ra video đầu tiên",
    "nav.menu.s2.t": "Xem giao diện chạy",
    "nav.menu.s2.d": "Theo dõi từng bước rõ ràng",
    "nav.menu.s3.t": "Giọng đọc có sẵn",
    "nav.menu.s3.d": "Nghe thử ngay không cần mạng",
    "nav.menu.more": "Tùy chọn & hỗ trợ",
    "nav.menu.m1.t": "Dịch miễn phí",
    "nav.menu.m1.d": "Dùng khóa miễn phí Google hoặc Groq",
    "nav.menu.m2.t": "Chế độ không cần mạng",
    "nav.menu.m2.d": "Giữ kín 100% video trên máy",
    "nav.menu.m3.t": "Câu hỏi thường gặp",
    "nav.menu.m3.d": "Cấu hình máy, bảo mật, hỗ trợ Mac",
    "nav.card.title": "Bản Windows mới nhất",
    "nav.card.sub": "Một tệp zip duy nhất, giải nén là chạy",
    "nav.card.note.t": "DubTool v1.0.23",
    "nav.card.note.time": "mới",
    "nav.card.note.b": "Giọng đọc rõ hơn & câu dịch liền mạch hơn",
    "nav.features": "Tính năng",
    "nav.modes": "Cách dịch",
    "nav.faq": "Hỏi đáp",
    "nav.github": "GitHub",
    "cta.download": "Tải DubTool",
    "cta.guide": "Xem hướng dẫn",

    "hero.h1": "Lồng tiếng Việt cho video trên Windows",
    "hero.lead": "DubTool tự nghe video ngoại ngữ, dịch sang tiếng Việt tự nhiên, đọc bằng giọng Việt có sẵn trên máy và xuất video kèm phụ đề rõ nét. Miễn phí, ai cũng dùng được.",
    "hero.proof": "Chạy trên Windows 10 và 11 (64-bit). Không cần cài thêm phần mềm phụ — tệp video gốc luôn được giữ nguyên.",
    "hero.win.title": "DubTool v1.0.23 · Lồng tiếng Việt",
    "hero.win.side1": "Tiến trình",
    "hero.win.side2": "Tùy chọn",
    "hero.win.side3": "Khóa dịch",
    "hero.win.side4": "Giọng đọc",
    "hero.win.side5": "Bản dịch",
    "hero.win.label": "Giọng có sẵn",
    "hero.win.head": "Các bước tự động",
    "hero.win.btn": "Tạo",
    "hero.win.t1": "Đã xong",
    "hero.win.t2": "Tự nhận diện",
    "hero.win.t3": "Tiếng Việt",
    "hero.r1.t": "Đọc âm thanh video",
    "hero.r1.s": "Giữ nguyên hình ảnh gốc",
    "hero.r2.t": "Tự nghe lời thoại",
    "hero.r2.s": "Nhận diện Tiếng Trung (99.9%)",
    "hero.r3.t": "Dịch sang tiếng Việt",
    "hero.r3.s": "Khóa miễn phí · 1.543 câu",
    "hero.r4.t": "Đọc giọng tiếng Việt",
    "hero.r4.s": "Trúc Ly · giọng tự nhiên có sẵn",
    "hero.r5.t": "Căn khớp nhịp nói",
    "hero.r5.s": "Không bị chồng tiếng hay lệch lời",
    "hero.r6.t": "Xuất video & phụ đề",
    "hero.r6.s": "Nền tối mờ dễ đọc chữ Việt",
    "hero.chip.done": "Xong",
    "hero.note.h": "Đã sẵn sàng xem",
    "hero.note.l1": "<b>phim_vi.mp4</b> · Video Full HD",
    "hero.note.l2": "<b>Giọng & phụ đề khớp nhịp</b> · rõ chữ",
    "hero.note.l3": "Kèm <b>bản dịch Word (.docx)</b>",

    "guide.h2": "Hướng dẫn dùng DubTool trong 4 bước đơn giản",
    "guide.sub": "Không cần lập trình hay cài thêm phần mềm phụ. Chỉ mất khoảng 2 phút thiết lập lần đầu.",
    "guide.s1.h": "Giải nén và mở DubTool",
    "guide.s1.p": "Giải nén tệp zip Windows rồi mở DubTool.exe. Mọi công cụ đã đóng gói sẵn bên trong.",
    "guide.s2.h": "Dán khóa dịch miễn phí",
    "guide.s2.p": "Vào Cài đặt → Khóa API, dán khóa Google hoặc Groq rồi bấm Kiểm tra tất cả.",
    "guide.s3.h": "Kéo thả video và chọn giọng đọc",
    "guide.s3.p": "Chọn thư mục lưu, giữ Tự động nhận diện và bấm Nghe thử giọng ngay trên máy.",
    "guide.s4.h": "Bấm Tạo để nhận video & bản Word",
    "guide.s4.p": "Tệp gốc luôn nguyên vẹn; bạn nhận video MP4 tiếng Việt kèm bảng dịch Word.",
    "guide.tip.title": "Video đã là tiếng Việt?",
    "guide.tip.text": "DubTool tự nhận ra, dừng trước khi tốn lượt dịch và đề xuất xuất ngay phụ đề sạch.",
    "guide.mock.h": "DubTool · Thiết lập lần đầu",
    "guide.mock.ok": "Sẵn sàng chạy",
    "guide.mock.r1.t": "Khóa miễn phí Gemini + Groq",
    "guide.mock.r1.s": "Cài đặt → Khóa API → Kiểm tra tất cả",
    "guide.mock.r1.ok": "Hợp lệ",
    "guide.mock.r2.t": "Giọng tiếng Việt có sẵn",
    "guide.mock.r2.s": "Trúc Ly · Nghe thử không cần mạng",
    "guide.mock.r2.ok": "Trên máy",
    "guide.mock.r3.t": "Thư mục xuất an toàn",
    "guide.mock.r3.s": "Lưu MP4 + SRT + Word (.docx)",
    "guide.mock.r3.ok": "Giữ gốc",
    "guide.mock.foot": "Thiết lập 1 lần mất 2 phút · Chạy gọn từ một thư mục Windows duy nhất",

    "ws.h2": "Một cửa sổ DubTool theo dõi rõ từng bước",
    "ws.lead": "Bạn luôn biết phần mềm đang làm tới đâu, còn bao nhiêu câu và đã lưu tệp nào. Nếu cần tạm dừng hay tắt máy, lần sau mở lại vẫn chạy tiếp đúng chỗ cũ.",
    "ws.link": "Xem ảnh giao diện trên GitHub",
    "ws.win.crumb": "Video gần đây",
    "ws.win.search": "Tìm",
    "ws.win.s1": "Gần đây",
    "ws.win.s2": "Đã xong",
    "ws.win.s3": "Tạm dừng",
    "ws.win.lang": "Ngôn ngữ",
    "ws.win.l1": "Tiếng Trung",
    "ws.win.l2": "Tiếng Anh",
    "ws.win.l3": "Tiếng Nhật",
    "ws.win.c1": "Video",
    "ws.win.c2": "Chi tiết",
    "ws.win.c3": "Trạng thái",
    "ws.win.c4": "Thời gian",
    "ws.r1.s": "2:33:04 · 1.543 câu · Trúc Ly",
    "ws.r2.s": "29:15 · Video dọc · 264 câu",
    "ws.r3.s": "18:40 · Full HD · Đức Trí",
    "ws.r4.s": "14:22 · Full HD · Mai Anh",
    "ws.r5.s": "11:05 · Full HD · Trúc Ly",
    "ws.r6.s": "42:10 · Full HD · Minh Quân",
    "ws.r7.s": "35:48 · Full HD · Trúc Ly",
    "ws.r8.s": "22:15 · Full HD · Bảo Ngọc",
    "ws.r9.s": "03:18 · Full HD · Trúc Ly",
    "ws.st.open": "Đang chờ",
    "ws.st.done": "Xong",
    "ws.st.run": "Đang chạy",
    "ws.pipe.chip": "Chạy",
    "ws.pipe.chipDone": "Xong",
    "ws.pipe.msg": "Tự xoay khóa miễn phí · Giọng Trúc Ly · Phụ đề rõ nét",
    "ws.pipe.btn": "Hủy",
    "ws.pipe.btnDone": "Chạy lại",
    "ws.pipe.p1": "Âm thanh",
    "ws.pipe.p2": "Nghe",
    "ws.pipe.p3": "Dịch",
    "ws.pipe.p4": "Giọng",
    "ws.pipe.p5": "Xuất",
    "ws.pipe.c1": "tách âm thanh gốc",
    "ws.pipe.c2": "nhận diện tiếng Trung",
    "ws.pipe.c3": "1.543 câu thoại",
    "ws.pipe.c4": "Trúc Ly (trên máy)",
    "ws.pipe.c5": "MP4 + SRT + Word",
    "ws.pipe.wait": "Chờ",
    "ws.log.1": "<b>[1/5]</b> Đã tách âm thanh gốc sạch sẽ trong 7 giây",
    "ws.log.2": "<b>[2/5]</b> Nhận diện tiếng Trung (99.9%) · 1.543 câu thoại",
    "ws.log.3": "<b>[3/5]</b> Đang dịch tự động bằng khóa miễn phí Gemini + Groq",
    "ws.log.4": "<b>[3/5]</b> Đã kiểm tra đủ câu tiếng Việt · không sót chữ ngoại ngữ",
    "ws.log.5": "<b>[4/5]</b> Đã tạo 1.543 đoạn giọng Việt tự nhiên ngay trên máy",
    "ws.log.6": "<b>[5/5]</b> Đã khớp nhịp giọng · 0 chồng tiếng · giữ nguyên nhạc nền",
    "ws.log.7": "<b>[xong]</b> Đã lưu drama_ep01_vi.mp4 và drama_ep01_vi_translation.docx",

    "feat.h2.a": "Dễ dùng và ổn định cho phim dài.",
    "feat.h2.b": "Video gốc luôn nằm yên trên máy bạn.",
    "feat.c1.h": "Dịch miễn phí không lo đứt quãng",
    "feat.c1.p": "Thêm nhiều khóa miễn phí Google và Groq. Khóa này hết lượt, phần mềm tự đổi sang khóa khác.",
    "feat.n.times": "vừa xong|1p|3p",
    "feat.n1.t": "Tự chuyển sang khóa Groq miễn phí",
    "feat.n1.time": "vừa xong",
    "feat.n1.b": "Google bận tạm thời · không mất tiến độ",
    "feat.n2.t": "Tự sửa 2 câu còn sót chữ gốc",
    "feat.n2.time": "1p",
    "feat.n2.b": "Kiểm tra tự động · giữ nguyên các câu đã chuẩn",
    "feat.n3.t": "Dịch xong đủ 1.543 câu thoại",
    "feat.n3.time": "3p",
    "feat.n3.b": "Xưng hô tự nhiên đúng phim · không sót chữ ngoại",
    "feat.c2.h": "Giọng đọc tiếng Việt có sẵn trên máy",
    "feat.c2.p": "Chọn 25 giọng tự nhiên hoặc 11 giọng nhẹ cho máy yếu. Nghe thử trực tiếp không cần mạng.",
    "feat.tg.title": "Cài sẵn an toàn cho người mới",
    "feat.tg.tpl": "{n} / 5 bật",
    "feat.tg1.t": "Giọng tiếng Việt tự nhiên",
    "feat.tg1.s": "Chạy ngay trên máy · giọng Trúc Ly",
    "feat.tg2.t": "Hộp nền tối sau phụ đề",
    "feat.tg2.s": "Đọc rõ ngay cả khi phim có chữ gốc",
    "feat.tg3.t": "Giữ nhạc nền & tiếng động",
    "feat.tg3.s": "Hạ êm giọng gốc để nghe rõ tiếng Việt",
    "feat.tg4.t": "Lưu bản dịch Word dễ đọc",
    "feat.tg4.s": "Bảng đối chiếu câu gốc và tiếng Việt",
    "feat.tg5.t": "Ghi đè tệp video gốc",
    "feat.tg5.s": "Luôn tắt để bảo vệ video của bạn",
    "feat.c3.h": "Xuất sẵn video, phụ đề và tệp Word",
    "feat.c3.p": "Tự căn cỡ phụ đề cho video ngang lẫn video dọc, luôn lưu tệp mới và giữ nguyên video gốc.",
    "feat.f.status": "6 tệp đầu ra & dự án",
    "feat.f.reading": "Đang lưu {n} / {total}",
    "feat.f1.k": "Video lồng tiếng",
    "feat.f2.k": "Bản dịch Word",
    "feat.f3.k": "Phụ đề Việt",
    "feat.f4.k": "Câu đã dịch",
    "feat.f5.k": "Nhịp giọng đọc",
    "feat.f6.k": "Tùy chọn lưu",
    "feat.c4.h": "Linh hoạt 5 nguồn dịch khác nhau",
    "feat.c4.p": "Bắt đầu miễn phí với Gemini và Groq, chuyển sang OpenAI/Claude hoặc chạy offline bằng Ollama.",

    "stmt.h2": "DubTool giữ hình ảnh gốc sắc nét và âm thanh hài hòa",
    "stmt.lead": "Mỗi câu tiếng Việt được căn vừa đúng nhịp nói của nhân vật, hòa êm trên nền nhạc phim gốc để không bị chồng tiếng hay vỡ âm — ngay cả với phim dài hơn 2 tiếng rưỡi.",
    "stmt.link": "Xem mã nguồn mở trên GitHub",
    "stmt.f1.dt": "Nhanh gấp 2.5×",
    "stmt.f1.dd": "Chạy thử trên máy tính văn phòng không có card rời: phim Full HD dài 2 giờ 33 phút xong trong 61 phút.",
    "stmt.f2.dt": "0 chồng tiếng",
    "stmt.f2.dd": "Toàn bộ 1.543 câu thoại khớp nhịp video, phụ đề rõ nét và không sót chữ ngoại ngữ.",
    "stmt.f3.dt": "0 cần cài thêm",
    "stmt.f3.dd": "Một gói zip Windows 64-bit chứa sẵn mọi thứ — giải nén và mở DubTool.exe là chạy ngay.",
    "stmt.bar.t": "Thời gian chạy thực tế cho phim Full HD 2 giờ 33 phút trên máy Windows thường (tổng 61 phút)",
    "stmt.bar.m": "Xong 100% · 0 lỗi · tệp gốc giữ nguyên",
    "stmt.bar.l1": "Nghe thoại 15p",
    "stmt.bar.l2": "Dịch 3p",
    "stmt.bar.l3": "Đọc giọng 17p",
    "stmt.bar.l4": "Khớp & trộn 4p",
    "stmt.bar.l5": "Lưu phụ đề 21p",

    "prov.h2": "3 cách dịch linh hoạt trong DubTool",
    "prov.sub": "Phần nghe lời thoại và tạo giọng đọc tiếng Việt luôn chạy miễn phí ngay trên máy. Bạn chỉ cần chọn nơi dịch chữ.",
    "prov.tag": "Khuyên dùng",
    "prov.c1.h": "Miễn phí: Google & Groq",
    "prov.c1.m": "Phù hợp nhất cho đa số người dùng",
    "prov.k1": "Chi phí",
    "prov.k2": "Dữ liệu rời khỏi máy",
    "prov.k3": "Phù hợp nhất",
    "prov.c1.v1": "0 đồng với khóa miễn phí",
    "prov.c1.v2": "Chỉ gửi các dòng chữ cần dịch",
    "prov.c1.v3": "Phim dài, vlog YouTube, bài giảng",
    "prov.c2.h": "Nguồn dịch trả phí: OpenAI & Claude",
    "prov.c2.m": "Tùy chọn · Dùng tài khoản sẵn có",
    "prov.c2.v1": "Tính phí theo mức dùng của tài khoản",
    "prov.c2.v2": "Chỉ gửi các dòng chữ cần dịch",
    "prov.c2.v3": "Video chuyên ngành nhiều thuật ngữ",
    "prov.c3.h": "Không cần mạng: Ollama trên máy",
    "prov.c3.m": "Riêng tư tuyệt đối · Không gửi dữ liệu",
    "prov.c3.v1": "0 đồng, chạy hoàn toàn trên máy bạn",
    "prov.c3.v2": "Không gửi bất kỳ dữ liệu nào ra ngoài",
    "prov.c3.v3": "Video nội bộ hoặc tài liệu cần bảo mật",

    "faq.h2": "Câu hỏi thường gặp trước khi dùng DubTool",
    "faq.sub": "Giải đáp ngắn gọn về chi phí, máy tính cần có, quyền riêng tư và các giới hạn hiện tại.",
    "faq.link": "Đặt câu hỏi trên GitHub",
    "faq.q1": "DubTool có thật sự miễn phí và không gắn logo không?",
    "faq.a1": "Đúng vậy. Phần mềm hoàn toàn miễn phí theo giấy phép mở MIT, không giới hạn độ dài video và không chèn logo hay chữ ký vào video xuất.",
    "faq.q2": "DubTool có cần máy chơi game hay card rời không?",
    "faq.a2": "Có. DubTool chạy thẳng trên chip xử lý (CPU) của máy Windows bình thường. Nếu máy ít bộ nhớ RAM, bạn chỉ cần chọn chế độ giọng Nano nhẹ hơn trong Cài đặt.",
    "faq.q3": "Lấy khóa dịch miễn phí từ Google hoặc Groq có khó không?",
    "faq.a3": "Không khó. Bạn đăng nhập Google AI Studio hoặc Groq Console, bấm tạo khóa (Create API Key), sao chép rồi dán vào mục Cài đặt của DubTool 1 lần duy nhất, mất khoảng 2 phút.",
    "faq.q4": "Video gốc của tôi có bị tải lên mạng không?",
    "faq.a4": "Không bao giờ. Video và âm thanh nằm hoàn toàn trên máy bạn. Chỉ các dòng chữ đã nghe ra mới được gửi đi dịch — hoặc không gửi gì cả nếu bạn chọn Ollama.",
    "faq.q5": "DubTool có chạy trên máy Mac hay cần cài Python không?",
    "faq.a5": "Hiện tại DubTool chỉ hỗ trợ Windows 10 và 11 (64-bit), và bạn không cần cài Python hay bất kỳ phần mềm phụ nào — chỉ cần giải nén là dùng ngay.",

    "close.meta": "v1.0.23 · Windows 10 & 11 (64-bit) · Miễn phí mã nguồn mở",
    "close.h2": "Tải DubTool cho Windows",
    "close.lead": "Giải nén một tệp duy nhất, dán khóa miễn phí và bắt đầu lồng tiếng Việt cho video ngoại ngữ.",
    "close.cta": "Tải DubTool cho Windows (.zip)",
    "close.note": "Giấy phép MIT · Không gắn logo · Video gốc luôn nguyên vẹn",
    "close.github": "Xem dự án trên GitHub",
    "foot.lede": "Phần mềm Windows miễn phí giúp lồng tiếng Việt tự nhiên cho video ngoại ngữ.",
    "foot.c1": "Hướng dẫn",
    "foot.c1.1": "Cài đặt 4 bước",
    "foot.c1.2": "Giao diện chạy",
    "foot.c2": "Tính năng",
    "foot.c2.1": "Dịch miễn phí",
    "foot.c2.2": "Giọng trên máy",
    "foot.c3": "Hỗ trợ",
    "foot.c3.1": "3 cách dịch",
    "foot.c3.2": "Câu hỏi thường gặp",
    "foot.c4": "Dự án",
    "foot.c4.1": "Kho mã nguồn GitHub",
    "foot.c4.2": "Tài liệu tiếng Việt",
    "foot.lic": "Giấy phép MIT",
    "foot.tm": "Tên sản phẩm và biểu trưng thuộc quyền sở hữu của chủ sở hữu tương ứng."
  }
};

function applyLang(lang, persist = true) {
  const code = lang === "vi" ? "vi" : "en";
  const dict = STRINGS[code];
  document.documentElement.lang = code;
  if (dict["meta.title"]) document.title = dict["meta.title"];
  const desc = document.querySelector('meta[name="description"]');
  if (desc && dict["meta.desc"]) desc.setAttribute("content", dict["meta.desc"]);

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key && dict[key] !== undefined) el.textContent = dict[key];
  });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => {
    const key = el.getAttribute("data-i18n-html");
    if (key && dict[key] !== undefined) el.innerHTML = dict[key];
  });
  document.querySelectorAll("[data-i18n-attrs]").forEach((el) => {
    const pairs = (el.getAttribute("data-i18n-attrs") || "").split(",");
    pairs.forEach((pair) => {
      const idx = pair.indexOf(":");
      if (idx <= 0) return;
      const attr = pair.slice(0, idx).trim();
      const key = pair.slice(idx + 1).trim();
      if (attr && key && dict[key] !== undefined) el.setAttribute(attr, dict[key]);
    });
  });

  document.querySelectorAll("[data-lang-btn]").forEach((btn) => {
    const active = btn.getAttribute("data-lang-btn") === code;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });

  if (persist) {
    try { localStorage.setItem("dubtool-site-lang", code); } catch (_) {}
  }

  window.dispatchEvent(new CustomEvent("dubtool:lang", { detail: { lang: code } }));
}

function initLang() {
  let initial = "en";
  try {
    const q = new URLSearchParams(window.location.search).get("lang");
    if (q === "vi" || q === "en") {
      initial = q;
    } else {
      const saved = localStorage.getItem("dubtool-site-lang");
      if (saved === "vi" || saved === "en") initial = saved;
    }
  } catch (_) {}

  document.querySelectorAll("[data-lang-btn]").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyLang(btn.getAttribute("data-lang-btn") || "en", true);
    });
  });

  // Always apply language & notify live UI fragments once mounted.
  applyLang(initial, false);
  setTimeout(() => {
    window.dispatchEvent(new CustomEvent("dubtool:lang", { detail: { lang: document.documentElement.lang || initial } }));
  }, 120);
  syncLatestReleaseZip();
}

/* Keep direct-download zip links pointed at the latest GitHub Release asset automatically. */
async function syncLatestReleaseZip() {
  const links = document.querySelectorAll("a[data-download-zip]");
  if (!links.length) return;
  const cacheKey = "dubtool-latest-zip-v1";
  try {
    const cached = JSON.parse(sessionStorage.getItem(cacheKey) || "null");
    if (cached && cached.url && Date.now() - cached.ts < 10 * 60 * 1000) {
      links.forEach((a) => { a.href = cached.url; });
      return;
    }
  } catch (_) {}
  try {
    const res = await fetch("https://api.github.com/repos/namtit2932k/dubtool/releases/latest", {
      headers: { Accept: "application/vnd.github+json" }
    });
    if (!res.ok) return;
    const data = await res.json();
    const zip = (data.assets || []).find((a) => typeof a.name === "string" && a.name.toLowerCase().endsWith(".zip") && a.browser_download_url);
    if (!zip) return;
    links.forEach((a) => { a.href = zip.browser_download_url; });
    try { sessionStorage.setItem(cacheKey, JSON.stringify({ url: zip.browser_download_url, ts: Date.now() })); } catch (_) {}
  } catch (_) {}
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initLang);
} else {
  initLang();
}
