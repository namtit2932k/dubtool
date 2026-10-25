"""Command construction tests (AGENT_UPDATE_2.md section 4)."""

from __future__ import annotations

import pytest

from dubtool.audio_utils import alimiter_limit, build_mix_graph
from dubtool.stages.s7_subtitles import build_burn_filter


def test_static_graph_uses_volume_amix_no_normalize_and_limiter():
    graph = build_mix_graph("static", -14, 0)
    assert "[0:a]volume=-14dB[o0]" in graph
    assert "[1:a]volume=0dB[d0]" in graph
    assert "amix=inputs=2:normalize=0" in graph  # no automatic level halving
    assert f"alimiter=limit={alimiter_limit()}" in graph
    assert "sidechaincompress" not in graph


def test_duck_graph_has_sidechain_and_split():
    params = {"threshold": 0.03, "ratio": 8, "attack_ms": 20, "release_ms": 400}
    graph = build_mix_graph("duck", -14, 0, params)
    assert "asplit=2" in graph
    expected = "sidechaincompress=threshold=0.03:ratio=8:attack=0.020:release=400"
    assert expected in graph
    assert "amix=inputs=2:normalize=0" in graph
    assert graph.endswith("alimiter=limit=0.97[out]")


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        build_mix_graph("loud", -14, 0)


def test_burn_filter_style_and_paths():
    fx = build_burn_filter(
        srt_name="output.vi.srt",
        fontsdir="fonts",
        font_name="Arial",
        font_size=12.0,
        margin_v=16.0,
        outline=0.53,
    )
    assert fx.startswith("subtitles=output.vi.srt:fontsdir=fonts:")
    assert "force_style='" in fx
    for part in (
        "FontName=Arial",
        "FontSize=12.00",
        "PrimaryColour=&H00FFFFFF",
        "OutlineColour=&H00000000",
        "BorderStyle=1",
        "Outline=0.53",
        "Shadow=0",
        "Alignment=2",
        "MarginV=16.00",
    ):
        assert part in fx
    # No absolute Windows paths inside the filter (relative names only).
    assert ":\\" not in fx and "C:" not in fx
    assert "original_size" not in fx  # AGENT_UPDATE_3.md 2.1: has no effect
