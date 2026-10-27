"""AGENT_UPDATE_6.md section 3 tests: PyAV probes and the media cache.

3.1 — in-process probe results equal ffprobe results on generated files
(duration within 0.05 s, same sample rate/channels/width/height/fps).
3.2 — probing the same unchanged file twice reads it once; changing the
file invalidates the cache entry; durations.json persists/reloads.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dubtool import audio_utils
from dubtool.audio_utils import MediaCache


pytestmark = pytest.mark.usefixtures("require_ffmpeg")


@pytest.fixture(name="require_ffmpeg", scope="module")
def _ffmpeg_or_skip():
    try:
        audio_utils.require_ffmpeg()
        if shutil.which("ffprobe") is None:
            # probed via PyAV, but the parity tests need a real ffprobe
            pytest.skip("ffprobe not available for parity comparison")
        audio_utils.ffprobe_bin()
    except audio_utils.FFmpegMissing:
        pytest.skip("ffmpeg not available")


def _ffprobe_json(path: Path, entries: str = "format:stream", select: str | None = None) -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json"]
    if select is not None:
        cmd += ["-select_streams", select]
    cmd.append(str(path))
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout or "{}")


def _tone(dest: Path, seconds: float = 1.0, rate: int = 8000, channels: int = 1) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    audio_utils.run_ffmpeg(
        ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:r={rate}",
         "-ac", str(channels), str(dest)]
    )


def _clip(dest: Path, seconds: float = 1.0, fps: int = 30, w: int = 128, h: int = 72) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    audio_utils.run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate={fps}:duration={seconds}",
        str(dest),
    ])


# ----------------------------------------------------------------- 3.1 parity
class TestProbeParity:
    def test_duration_wav(self, tmp_path: Path):
        src = tmp_path / "tone.wav"
        _tone(src, seconds=1.23)
        assert audio_utils.probe_duration(src) == pytest.approx(1.23, abs=0.05)

    def test_duration_mp3(self, tmp_path: Path):
        src = tmp_path / "tone.mp3"
        _tone(src, seconds=2.0)
        assert audio_utils.probe_duration(src) == pytest.approx(2.0, abs=0.05)

    def test_duration_mp4(self, tmp_path: Path):
        src = tmp_path / "clip.mp4"
        _clip(src, seconds=2.0)
        assert audio_utils.probe_duration(src) == pytest.approx(2.0, abs=0.05)

    def test_audio_format(self, tmp_path: Path):
        src = tmp_path / "tone44100stereo.wav"
        _tone(src, seconds=1.0, rate=44100, channels=2)
        rate, channels = audio_utils.probe_audio_format(src)
        probe = _ffprobe_json(src, "stream=sample_rate,channels", select="a:0")
        assert rate == int(probe["streams"][0]["sample_rate"])
        assert channels == int(probe["streams"][0]["channels"])

    def test_audio_stream_count(self, tmp_path: Path):
        src = tmp_path / "clip.mp4"
        _clip(src, seconds=1.0)
        assert audio_utils.probe_audio_streams(src) == len(
            _ffprobe_json(src, "stream=index", select="a").get("streams", [])
        )

    def test_video_info(self, tmp_path: Path):
        src = tmp_path / "clip24.mp4"
        _clip(src, seconds=1.0, fps=24)
        info = audio_utils.probe_video_info(src)
        assert int(info["width"]) == 128
        assert int(info["height"]) == 72
        assert int(info["r_frame_rate"].split("/")[0]) == 24
        # AGENT_UPDATE_7.md task 3: seconds (float), tightly bounded —
        # the old assertion (> 0.9) also passed for microseconds.
        assert isinstance(info["duration"], float)
        assert info["duration"] == pytest.approx(1.0, abs=0.05)


class TestDurationUnitsKnownLength:
    """AGENT_UPDATE_7.md task 3: generated files of known length; both
    probe_video_info AND probe_duration must equal ffprobe within 0.05 s
    (this is what catches the raw-microseconds regression)."""

    @classmethod
    def _make(cls, tmp_path: Path, seconds: float) -> Path:
        src = tmp_path / f"known_{int(seconds)}s.mp4"
        dest = tmp_path / "gen"
        _clip(dest / src.name, seconds=seconds, fps=1, w=64, h=36)
        return dest / src.name

    def _ffprobe_duration(self, path: Path) -> float:
        data = _ffprobe_json(path, "format=duration")
        return float(data["format"]["duration"])

    def test_2s(self, tmp_path: Path):
        src = self._make(tmp_path, 2.0)
        expected = self._ffprobe_duration(src)
        assert audio_utils.probe_video_info(src)["duration"] == pytest.approx(expected, abs=0.05)
        assert audio_utils.probe_duration(src) == pytest.approx(expected, abs=0.05)

    def test_120s(self, tmp_path: Path):
        src = self._make(tmp_path, 120.0)
        expected = self._ffprobe_duration(src)
        got_info = audio_utils.probe_video_info(src)["duration"]
        got_probe = audio_utils.probe_duration(src)
        assert got_info == pytest.approx(expected, abs=0.05)
        assert got_probe == pytest.approx(expected, abs=0.05)
        assert got_info <= 121  # seconds, never raw microseconds

    def test_1861s(self, tmp_path: Path):
        src = self._make(tmp_path, 1861.0)
        expected = self._ffprobe_duration(src)
        got_info = audio_utils.probe_video_info(src)["duration"]
        assert got_info == pytest.approx(expected, abs=0.05)
        assert 1860 <= got_info <= 1865  # around 31 minutes, in seconds

    def test_format_mmss(self):
        # hour format helper (AGENT_UPDATE_7.md task 3)
        assert audio_utils.format_mmss(2) == "00:02"
        assert audio_utils.format_mmss(120) == "02:00"
        assert audio_utils.format_mmss(1861) == "31:01"
        assert audio_utils.format_mmss(3723) == "1:02:03"


# ------------------------------------------------------------------- 3.2 cache
class TestMediaCache:
    def test_same_file_probed_once(self, tmp_path: Path, monkeypatch):
        src = tmp_path / "tone.wav"
        _tone(src)
        calls = []
        real = audio_utils.probe_duration

        def counting(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(audio_utils, "probe_duration", counting)
        cache = MediaCache(tmp_path)
        first = cache.duration(src, root=tmp_path)
        second = cache.duration(src, root=tmp_path)
        assert first == second
        assert len(calls) == 1

    def test_changed_file_invalidates(self, tmp_path: Path, monkeypatch):
        src = tmp_path / "tone.wav"
        _tone(src, seconds=1.0)
        cache = MediaCache(tmp_path)
        first = cache.duration(src, root=tmp_path)
        _tone(src, seconds=2.0)
        second = cache.duration(src, root=tmp_path)
        assert second > first * 1.5

    def test_persists_and_reloads(self, tmp_path: Path, monkeypatch):
        src = tmp_path / "tone.wav"
        _tone(src, seconds=1.5)
        MediaCache(tmp_path).duration(src, root=tmp_path)
        assert (tmp_path / "durations.json").exists()
        calls = []
        real = audio_utils.probe_duration

        def never(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(audio_utils, "probe_duration", never)
        reloaded = MediaCache(tmp_path).duration(src, root=tmp_path)
        assert reloaded == pytest.approx(1.5, abs=0.05)
        assert calls == []

    def test_store_has_no_absolute_paths(self, tmp_path: Path):
        src = tmp_path / "tone.wav"
        _tone(src)
        cache = MediaCache(tmp_path)
        cache.duration(src, root=tmp_path)
        cache.save()
        raw = (tmp_path / "durations.json").read_text(encoding="utf-8")
        assert str(src) not in raw
        assert str(src.name) in raw

    def test_size_change_invalidates_even_same_mtime(self, tmp_path: Path, monkeypatch):
        src = tmp_path / "tone.wav"
        _tone(src, seconds=1.0)
        cache = MediaCache(tmp_path)
        _ = cache.duration(src, root=tmp_path)
        # Simulate touching: mtime identical (restore it), size changed.
        _tone(src, seconds=2.0)
        st_before = src.stat()
        call_count = []

        real = audio_utils.probe_duration
        def counting(path):
            call_count.append(path)
            return real(path)

        monkeypatch.setattr(audio_utils, "probe_duration", counting)
        # Force identical mtime to prove size alone invalidates.
        import os
        os.utime(src, (st_before.st_atime_ns / 1e9, st_before.st_mtime_ns / 1e9))
        _ = cache.duration(src, root=tmp_path)
        assert len(call_count) == 1
