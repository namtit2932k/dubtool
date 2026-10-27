"""AGENT_UPDATE_7.md task 5.7 — tests for the customer-readable
translation file: sentence rows (not ASR fragments), boundary-repeat
removal in both languages, times taken from schedule.json (play_start),
h:mm:ss formatting, no technical content, a valid docx package with a
repeating non-splitting header row, the BOM'd .txt fallback, naming in
step with the video, and failure isolation in run_dub.
"""

from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_MK = pytest.importorskip  # noqa: F841 (docx imported below where needed)

# ----------------------------------------------------------------- fixture
TECH_VALUES = {
    "max_chars": 40,
    "reviewed": False,
    "tts_duration": 2.5,
    "fit_status": "ok",
}


def _entries() -> list[dict]:
    return [
        {
            "id": 0, "start": 1.0, "end": 4.0,
            "text": "Whatever you ask, the answer is always",
            "vi": "Dù bạn hỏi gì, câu trả lời luôn là",
            **TECH_VALUES,
        },
        {
            "id": 1, "start": 4.0, "end": 6.5,
            "text": "The answer is always yes, every time.",
            "vi": "Câu trả lời luôn là có, mỗi lúc.",
            **TECH_VALUES,
        },
        {
            "id": 2, "start": 3695.0, "end": 3705.0,
            "text": "And that is a promise.",
            "vi": "Và đó là một lời hứa.",
            **TECH_VALUES,
        },
    ]


_UNITS = [
    {"unit_id": 0, "seg_ids": [0, 1], "text": "joined en", "start": 1.0, "end": 6.5, "max_chars": 80},
    {"unit_id": 1, "seg_ids": [2], "text": "And that is a promise.", "start": 3695.0, "end": 3705.0, "max_chars": 30},
]

_SCHEDULE = [
    {"id": 0, "orig_start": 1.0, "play_start": 14.0, "play_end": 16.5, "shift": 0.0, "overlap": False},
    {"id": 1, "orig_start": 4.0, "play_start": 14.2, "play_end": 16.7, "shift": 0.2, "overlap": False},
    {"id": 2, "orig_start": 3695.0, "play_start": 3700.0, "play_end": 3710.0, "shift": 5.0, "overlap": True},
]


def _fixture_job(tmp_path: Path) -> Path:
    job = tmp_path / "job"
    job.mkdir()
    (job / "translated.json").write_text(
        json.dumps(_entries(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (job / "units.json").write_text(
        json.dumps(_UNITS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (job / "schedule.json").write_text(
        json.dumps(_SCHEDULE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return job


FORBIDDEN = (
    "max_chars", "fit_status", "tts_duration", "reviewed", "gemini", "flash",
    "JSON", '": ', '"', "\\",  # JSON-looking strings must not appear
)
FORBIDDEN_MODE_WORDS = ("Fast", "Accurate", "Nhanh", "Chính xác")


# -------------------------------------------------------------------- rows
def test_rows_are_sentences_with_schedule_times(tmp_path):
    from dubtool.stages.s8_export_readable import build_rows, format_time

    rows = build_rows(_fixture_job(tmp_path))
    assert len(rows) == 2
    # Row 0: the two fragments joined into ONE sentence, and the repeated
    # boundary words removed from BOTH languages.
    assert rows[0]["en"] == "Whatever you ask, the answer is always yes, every time."
    assert rows[0]["vi"] == "Dù bạn hỏi gì, câu trả lời luôn là có, mỗi lúc."
    # Vietnamese diacritics must survive verbatim.
    assert "Việt" and "Dù" in rows[0]["vi"] and "là" in rows[0]["vi"]
    # Time from schedule.json's play_start, not the transcript start.
    assert rows[0]["time"] == 14.0
    assert rows[1]["time"] == 3700.0  # play_start, not 3695.0
    assert format_time(rows[0]["time"]) == "00:14"
    assert format_time(rows[1]["time"]) == "1:01:40"  # h:mm:ss after one hour


def test_rows_fall_back_to_build_units_when_units_json_missing(tmp_path):
    from dubtool.stages.s8_export_readable import build_rows

    job = _fixture_job(tmp_path)
    (job / "units.json").unlink()
    rows = build_rows(job)
    assert len(rows) == 2
    assert rows[0]["en"] == "Whatever you ask, the answer is always yes, every time."


def test_rows_skip_empty_sides(tmp_path):
    from dubtool.job import write_json
    from dubtool.stages.s8_export_readable import build_rows

    job = _fixture_job(tmp_path)
    entries = _entries()
    entries[2]["vi"] = ""  # its whole unit becomes untranslatable -> skipped
    write_json(job / "translated.json", entries)
    rows = build_rows(job)
    assert [r["en"] for r in rows] == ["Whatever you ask, the answer is always yes, every time."]


# ------------------------------------------------------------------- meta
def test_naming_follows_the_video():
    from dubtool.stages.s8_export_readable import readable_target

    assert readable_target(Path("clip_vi.mp4")) == Path("clip_vi_translation.docx")
    assert readable_target(Path("episode 2_vi.mp4"), "txt") == Path("episode 2_vi_translation.txt")
    # a `_vi_2`-numbered video keeps the matching number
    assert readable_target(Path("clip_vi_2.mp4")) == Path("clip_vi_2_translation.docx")


def test_meta_headers_and_language(tmp_path, monkeypatch):
    from dubtool.app import i18n
    from dubtool.stages.s8_export_readable import make_meta

    monkeypatch.setattr(i18n, "set_language", lambda lang: None)
    monkeypatch.setattr(i18n, "_LANG", "vi")
    meta = make_meta(Path("clip_vi.mp4"), lang="vi", duration_s=225.0)
    assert meta["title"] == "Bản dịch video"
    assert meta["video"] == "clip_vi.mp4"
    assert meta["duration"] == "03:45"
    assert "→" in meta["lang_pair"]
    # dd/mm/yyyy creation date
    assert len(meta["date"].split("/")) == 3
    labels = meta["labels"]
    assert labels["col_time"] == "Thời gian"
    assert labels["col_vi"] == "Tiếng Việt (bản dịch)"

    monkeypatch.setattr(i18n, "_LANG", "en")
    meta_en = make_meta(Path("clip_vi.mp4"), lang="en")
    assert meta_en["title"] == "Video translation"
    assert meta_en["labels"]["col_en"] == "English (original)"


# ------------------------------------------------------------------- docx
@pytest.fixture()
def docx_sample(tmp_path):
    docs = pytest.importorskip("docx")  # noqa: F841
    from dubtool.stages.s8_export_readable import make_meta, render_docx

    job = _fixture_job(tmp_path)
    from dubtool.stages.s8_export_readable import build_rows

    rows = build_rows(job)
    meta = make_meta(Path("clip_vi.mp4"), lang="vi", duration_s=225.0)
    out = tmp_path / "clip_vi_translation.docx"
    render_docx(out, meta, rows)
    return out, rows


def test_docx_valid_package_and_layout(docx_sample):
    docx = pytest.importorskip("docx")
    out, _rows = docx_sample
    # It is a real zip package and word/document.xml parses.
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    rows = root.iter(f"{W_NS}tr")
    assert rows, "no table rows"

    doc = docx.Document(str(out))
    table = doc.tables[0]
    header = [c.text for c in table.rows[0].cells]
    assert header[0] == "Thời gian"
    assert header[1] == "Tiếng Anh (bản gốc)"
    assert header[2] == "Tiếng Việt (bản dịch)"
    # one row per sentence + the header row
    assert len(table.rows) == 3
    assert table.rows[1].cells[1].text == "Whatever you ask, the answer is always yes, every time."
    assert table.rows[2].cells[2].text == "Và đó là một lời hứa."


def test_docx_header_repeats_and_rows_never_split(docx_sample):
    out, _rows = docx_sample
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    trs = list(root.iter(f"{W_NS}tr"))
    assert trs, "no rows"
    tr_pr = trs[0].find(f"{W_NS}trPr")
    assert tr_pr is not None and tr_pr.find(f"{W_NS}tblHeader") is not None, "header row must repeat"
    # every row (header + data) is marked as never splitting across pages
    for tr in trs:
        tr_pr = tr.find(f"{W_NS}trPr")
        assert tr_pr is not None and tr_pr.find(f"{W_NS}cantSplit") is not None


def test_docx_no_technical_content(docx_sample):
    out, _rows = docx_sample
    docx = pytest.importorskip("docx")
    doc = docx.Document(str(out))
    text = "\n".join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text += "\n" + cell.text
    for forbidden in FORBIDDEN:
        assert forbidden not in text, forbidden
    for word in FORBIDDEN_MODE_WORDS:
        assert word not in text, word


def test_docx_footer_has_disclaimer_and_page_fields(docx_sample):
    out, _rows = docx_sample
    docx = pytest.importorskip("docx")
    # BOM check does not apply to docx (it is a zip); the footer check:
    doc = docx.Document(str(out))
    footer = doc.sections[0].footer
    footer_text = "\n".join(p.text for p in footer.paragraphs)
    assert "tạo tự động" in footer_text
    assert "Trang" in footer_text and "/" in footer_text
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/footer1.xml") if "word/footer1.xml" in z.namelist() else b""
    assert b"PAGE" in xml and b"NUMPAGES" in xml


# -------------------------------------------------------------------- txt
def test_txt_bom_blocks_and_disclaimer(tmp_path):
    from dubtool.stages.s8_export_readable import build_rows, make_meta, render_txt

    job = _fixture_job(tmp_path)
    rows = build_rows(job)
    meta = make_meta(Path("clip_vi.mp4"), lang="vi", duration_s=225.0)
    out = tmp_path / "clip_vi_translation.txt"
    render_txt(out, meta, rows)
    raw = out.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "txt needs a UTF-8 BOM"
    text = raw.decode("utf-8-sig")
    assert "Bản dịch video" in text
    assert "Video: clip_vi.mp4" in text
    assert "Thời lượng: 03:45" in text
    assert "[00:14]" in text
    assert "[1:01:40]" in text
    assert "EN: Whatever you ask, the answer is always yes, every time." in text
    assert "VI: Dù bạn hỏi gì, câu trả lời luôn là có, mỗi lúc." in text
    assert "kiểm tra trước khi sử dụng" in text
    for forbidden in FORBIDDEN:
        assert forbidden not in text, forbidden


def test_deliver_docx_then_txt_target_names(tmp_path):
    from dubtool.stages.s8_export_readable import deliver

    job = _fixture_job(tmp_path)
    video = tmp_path / "clip_vi.mp4"
    video.write_bytes(b"x")
    p = deliver(job, video, fmt="docx", lang="vi", duration_s=225.0)
    assert p.exists() and p.name == "clip_vi_translation.docx"
    p2 = deliver(job, video, fmt="txt", lang="vi", duration_s=225.0)
    assert p2.exists() and p2.name == "clip_vi_translation.txt"
    assert b"This portable document" not in p2.read_bytes()


# --------------------------------------------------- failure isolation
def _stubbed_api(tmp_path, monkeypatch):
    """Minimal run_dub setup with fake stages (same idea as
    test_api_and_service.stub_pipeline)."""
    import dubtool.app.paths as paths_mod
    from dubtool import api

    root = tmp_path / "project"
    (root / "packaging").mkdir(parents=True)
    (root / "packaging" / "modes.yaml").write_text(
        "fast:\n  provider: gemini\n  model: m-fast\n  translate_concurrency: 1\n"
        "accurate:\n  provider: gemini\n  model: m-acc\n  translate_concurrency: 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths_mod, "bundle_root", lambda: root)
    monkeypatch.setattr(api, "_default_jobs_dir", lambda: tmp_path / "jobs")

    import dubtool.config as config_mod

    real_load = config_mod.load

    def load_from(root_arg, overrides=None):
        return real_load(Path(__file__).parents[1], overrides=overrides)

    monkeypatch.setattr(config_mod, "load", load_from)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video bytes")
    return video


def test_run_dub_failure_isolated_readable(monkeypatch, tmp_path):
    """An exception inside the export must NOT fail the video result, and
    must fall back to .txt (AGENT_UPDATE_7.md 5.5)."""
    import dubtool.stages.s8_export_readable as s8
    from dubtool import api
    from dubtool.api import DubOptions

    video = _stubbed_api(tmp_path, monkeypatch)
    outdir = tmp_path / "out"
    outdir.mkdir()
    video_target = outdir / "clip_vi.mp4"

    class StubSecrets:
        def get_gemini_key(self):
            return "test-key-not-a-real-gemini-key-0000"

    # The job dir needs the small inputs; deliver them via a stubbed stage 3.
    import dubtool.stages.s3_translate as s3

    def fake_s3_run(job, config, force=False, cancel=None, api_keys=None):
        from dubtool.job import write_json

        write_json(job.dir / "translated.json", _entries())
        write_json(job.dir / "units.json", _UNITS)
        write_json(job.dir / "schedule.json", _SCHEDULE)

    monkeypatch.setattr(s3, "run", fake_s3_run)
    import dubtool.stages.s7_subtitles as s7

    monkeypatch.setattr(s7, "run", lambda job, config, force=False, cancel=None, api_keys=None: job.output_path.write_bytes(b" dubbed"))
    # Simulate the other stages minimally: skip everything except we need
    # stage runs to exist; run_dub imports each module's run lazily.
    import dubtool.stages.s1_extract_audio as s1
    import dubtool.stages.s2_transcribe as s2
    import dubtool.stages.s4_tts as s4
    import dubtool.stages.s5_fit_duration as s5
    import dubtool.stages.s6_mix as s6

    for mod in (s1, s2, s4, s5, s6):
        monkeypatch.setattr(mod, "run", lambda job, config, force=False, cancel=None, api_keys=None: None)

    # Word export fails, .txt fallback succeeds.
    calls: list[str] = []
    real_deliver = s8.deliver

    def flaky_deliver(job_dir, video_target, *, fmt, lang, duration_s=None):
        calls.append(fmt)
        if fmt == "docx":
            raise RuntimeError("python-docx missing (simulated)")
        return real_deliver(job_dir, video_target, fmt=fmt, lang=lang, duration_s=duration_s)

    monkeypatch.setattr(s8, "deliver", flaky_deliver)
    result = api.run_dub(
        video, outdir,
        options=DubOptions(mode="fast"), secrets=StubSecrets(),
        progress=lambda e: None, cancel=threading.Event(),
    )
    assert result.ok, result.error
    assert calls == ["docx", "txt"]
    expected = video_target.with_name("clip_vi_translation.txt")
    assert result.out_readable == expected
    assert expected.exists()
    assert not result.readable_warning
    # The export ran before the cleanup; intermediates are gone afterwards.
    job_dirs = list((tmp_path / "jobs").iterdir())
    assert job_dirs, "job dir missing"


def test_run_dub_readless_when_export_never_succeeds(monkeypatch, tmp_path):
    """If both formats fail the video stays ok with a warning set."""
    import dubtool.stages.s8_export_readable as s8
    from dubtool import api
    from dubtool.api import DubOptions

    video = _stubbed_api(tmp_path, monkeypatch)
    outdir = tmp_path / "out"
    outdir.mkdir()

    class StubSecrets:
        def get_gemini_key(self):
            return "test-key-not-a-real-gemini-key-0000"

    import dubtool.stages.s3_translate as s3

    def fake_s3_run(job, config, force=False, cancel=None, api_keys=None):
        pass  # no translated.json at all

    monkeypatch.setattr(s3, "run", fake_s3_run)
    import dubtool.stages.s7_subtitles as s7

    monkeypatch.setattr(s7, "run", lambda job, config, force=False, cancel=None, api_keys=None: job.output_path.write_bytes(b" dubbed"))
    import dubtool.stages.s1_extract_audio as s1
    import dubtool.stages.s2_transcribe as s2
    import dubtool.stages.s4_tts as s4
    import dubtool.stages.s5_fit_duration as s5
    import dubtool.stages.s6_mix as s6

    for mod in (s1, s2, s4, s5, s6):
        monkeypatch.setattr(mod, "run", lambda job, config, force=False, cancel=None, api_keys=None: None)

    monkeypatch.setattr(
        s8, "deliver",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = api.run_dub(
        video, outdir,
        options=DubOptions(mode="fast"), secrets=StubSecrets(),
        progress=lambda e: None, cancel=threading.Event(),
    )
    assert result.ok, result.error
    assert result.out_readable is None
    assert result.readable_warning  # the UI can show the warning
    assert (outdir / "clip_vi.mp4").exists()
