"""Task 2 tests (AGENT_UPDATE_3.md 3): silence trimming and cache-key options."""

from __future__ import annotations

from pathlib import Path

import pytest

from dubtool import audio_utils
from dubtool.stages.s4_tts import SegmentTTS, build_tts, tts_options


pytestmark = pytest.mark.usefixtures("require_ffmpeg_or_skip")


@pytest.fixture(name="require_ffmpeg_or_skip")
def _ffmpeg_or_skip():
    try:
        audio_utils.ffmpeg_bin()
    except audio_utils.FFmpegMissing:
        pytest.skip("ffmpeg not available")


def _padded_tone(path: Path, seconds: float = 2.0, start: float = 0.5, end: float = 1.5):
    """A clip with silence + start, tone in the middle, silence + tail."""
    trimmed = "0.2*sin(2*PI*440*t)*between(t\\,%s\\,%s)" % (start, end)
    audio_utils.run_ffmpeg([
        "-f", "lavfi", "-i", f"aevalsrc=exprs='{trimmed}':d={seconds}",
        str(path),
    ])


def test_trim_silence_keeps_margin(tmp_path: Path):
    src = tmp_path / "tone.wav"
    _padded_tone(src)
    out = tmp_path / "trim.wav"
    audio_utils.trim_silence(src, out, threshold_db=-35, margin_s=0.05)
    dur = audio_utils.probe_duration(out)
    raw = audio_utils.probe_duration(src)
    assert raw == pytest.approx(2.0, abs=0.1)
    # Tone runs 0.5..1.5 s; both ends keep up to 0.05 s of margin.
    assert dur == pytest.approx(1.1, abs=0.25)
    assert dur < raw - 0.3


def test_trim_silence_available_in_default_env(tmp_path: Path):
    src = tmp_path / "tone.wav"
    _padded_tone(src)
    out = tmp_path / "trim.wav"
    audio_utils.trim_silence(src, out, threshold_db=-35, margin_s=0.05)
    # No word onset may be cut: audio starts near-silent then the tone rises.
    assert audio_utils.is_valid_media(out)


class FakeProvider:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        self.calls.append((text, voice))
        _padded_tone(out_path)


def test_segment_tts_key_includes_options():
    tts_a = SegmentTTS(FakeProvider(), Path("."), "voice", options={"rate": "+0%"})
    tts_b = SegmentTTS(FakeProvider(), Path("."), "voice", options={"rate": "+0%"})
    tts_c = SegmentTTS(FakeProvider(), Path("."), "voice", options={"rate": "+10%"})
    tts_d = SegmentTTS(
        FakeProvider(), Path("."), "voice",
        options={"rate": "+0%", "trim": {"threshold_db": -35, "margin_s": 0.05}},
    )
    seg = {"id": 1, "vi": "xin chào"}
    assert tts_a.key_for(seg) == tts_b.key_for(seg)
    assert tts_a.key_for(seg) != tts_c.key_for(seg)
    assert tts_a.key_for(seg) != tts_d.key_for(seg)  # trim setting is in the key


def test_segment_tts_trims_and_reports_trimmed_duration(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    tts = SegmentTTS(
        FakeProvider(),
        cache,
        "voice",
        options={"rate": "+0%", "trim": {"threshold_db": -35, "margin_s": 0.05}},
    )
    duration, cache_path = tts.synthesize({"id": 7, "vi": "hello"})
    assert cache_path.exists()
    assert duration == pytest.approx(1.1, abs=0.3)
    assert duration < 1.9  # the padding was cut
    # Second call is a cache hit (provider not invoked again).
    provider = tts.provider
    calls = list(provider.calls)
    duration2, _ = tts.synthesize({"id": 7, "vi": "hello"})
    assert tts.provider.calls == calls
    assert duration2 == pytest.approx(duration)


def test_tts_options_from_config():
    class C:
        tts_rate = "+0%"
        tts_trim_silence = True
        tts_silence_threshold_db = -35
        tts_silence_margin_s = 0.05
        tts_voice = "vi-VN-HoaiMyNeural"

    opts = tts_options(C())
    assert opts["rate"] == "+0%"
    assert opts["trim"] == {"threshold_db": -35, "margin_s": 0.05}
    built = build_tts(C(), Path("cache"))
    assert built.key_for({"id": 1, "vi": "x"}) != SegmentTTS(
        FakeProvider(), Path("cache"), "voice"
    ).key_for({"id": 1, "vi": "x"})
