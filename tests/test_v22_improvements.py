"""Regression tests for Update 22 (v1.0.22):
- Portrait subtitle auto line width (PORTRAIT_LINE_CHARS not shadowed by config.yaml),
  portrait font cap (width / 20), translucent bg_box, and CRF 21 default.
- S3 sentence-initial capitalization after terminal punctuation and drama-aware
  pronoun guidance in prompts + Groq compact system prompt.
- DropZone accessibility (QPushButton inheritance for keyboard/UIA Invoke) and
  Emerald Studio theme / StageItemDelegate / brand icon rendering.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from dubtool import config as config_mod
from dubtool.llm import groq as groq_mod
from dubtool.llm import parsing, prompts
from dubtool.stages.s7_subtitles import build_burn_filter, style_in_units

ROOT = Path(__file__).resolve().parents[1]


def test_config_yaml_enables_portrait_auto_line_chars_and_crf_21():
    cfg = config_mod.load(ROOT)
    assert cfg.subtitles.get("max_line_chars") is None
    assert cfg.subtitles.get("crf") == 21
    assert cfg.subtitles.get("bg_box") is True

    portrait = style_in_units(1280, 720, cfg.subtitles)
    assert portrait["max_line_chars"] == 28
    assert portrait["font_px"] == pytest.approx(36.0)
    assert portrait["bg_box"] is True

    landscape = style_in_units(1080, 1920, cfg.subtitles)
    assert landscape["max_line_chars"] == 42
    assert landscape["font_px"] == pytest.approx(45.0)


def test_build_burn_filter_bg_box_and_plain():
    g_box = build_burn_filter("out.srt", "fonts", "Arial", 10.0, 16.0, 0.5, bg_box=True)
    assert "BorderStyle=3" in g_box
    assert "BackColour=&H38000000" in g_box

    g_plain = build_burn_filter("out.srt", "fonts", "Arial", 10.0, 16.0, 0.5)
    assert "BorderStyle=1" in g_plain
    assert "OutlineColour=&H00000000" in g_plain


def test_normalize_vietnamese_casing_and_parse_integration():
    assert parsing.normalize_vietnamese_casing("Xin lỗi. lúc chàng hôn mê.") == "Xin lỗi. Lúc chàng hôn mê."
    assert parsing.normalize_vietnamese_casing("Hôm nay là ngày gì? ngày mùng ba.") == "Hôm nay là ngày gì? Ngày mùng ba."
    assert parsing.normalize_vietnamese_casing("Đứng lại! ngươi đi đâu?") == "Đứng lại! Ngươi đi đâu?"
    # Ellipsis & decimals must remain intact.
    assert parsing.normalize_vietnamese_casing("À... ừ, khoảng 1.5 phút thôi.") == "À... ừ, khoảng 1.5 phút thôi."
    assert parsing.normalize_vietnamese_casing("đời yên bình. đời sau nữa.") == "đời yên bình. Đời sau nữa."

    parsed = parsing.parse_json_array('[{"id": 1, "vi": "Xin lỗi. lúc đó ta sai."}]', [1])
    assert parsed == [{"id": 1, "vi": "Xin lỗi. Lúc đó ta sai."}]
    assert parsing.parse_shortened('{"vi": "Thôi. đi nhanh lên."}') == "Thôi. Đi nhanh lên."


def test_prompts_allow_contextual_drama_pronouns():
    block = prompts.render_pronoun_block(None, has_brief=False)
    assert "multi-character dramas" in block.lower()
    assert "trẫm/ta/ngươi" in block
    assert "multi-character" in groq_mod._COMPACT_TRANSLATE_SYSTEM.lower()
    assert "trẫm/ta/ngươi" in groq_mod._COMPACT_TRANSLATE_SYSTEM


def test_dropzone_is_qpushbutton_and_emerald_theme_works(qtbot):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QPushButton

    from dubtool.app.ui.theme import (
        EmeraldProxyStyle,
        apply_emerald_theme,
        render_brand_icon_pixmap,
    )
    from dubtool.app.ui.widgets import DropZone, StageList

    app = QApplication.instance()
    assert app is not None
    apply_emerald_theme(app)
    assert bool(app.property("_dubtool_emerald_theme")) is True
    assert "#0B0F17" in app.styleSheet()

    dz = DropZone()
    qtbot.addWidget(dz)
    assert isinstance(dz, QPushButton)
    assert dz.objectName() == "drop_zone"
    clicked = []
    dz.clicked.connect(lambda: clicked.append(True))
    dz.click()
    assert clicked == [True]

    stages = StageList()
    qtbot.addWidget(stages)
    stages.update_state("s3", "progress", 2, 5)
    assert "2/5" in stages.item(2).text()
    assert stages.item(2).text().startswith("…")

    pm = render_brand_icon_pixmap(64)
    assert not pm.isNull()
    assert pm.width() == 64 and pm.height() == 64
