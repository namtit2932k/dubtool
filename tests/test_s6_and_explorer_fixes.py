"""Regression tests for:

1. O(N) streaming audio mixer in Stage 6 (``audio_utils.render_dub_track`` &
   ``mix_overlays``): eliminates multi-GB ``_silence.wav`` and pydub's O(N^2)
   full-track memory copying, handles sample-rate/channel adaptation and
   30-second block boundaries accurately, and honors cancellation.
2. Windows Explorer folder reveal (``_open_in_explorer``): handles paths with
   spaces (e.g. ``New folder``) via Win32 ``SHOpenFolderAndSelectItems`` instead
   of broken ``explorer "/select,..."`` quoting that fell back to Documents.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from dubtool import audio_utils
from dubtool.job import StageCancelled


def _write_tone(path: Path, freq: float, duration_s: float, sr: int = 48000, channels: int = 1, amp: float = 0.5) -> Path:
    n = int(round(duration_s * sr))
    t = np.arange(n, dtype=np.float32) / float(sr)
    wave = (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    if channels > 1:
        wave = np.repeat(wave[:, None], channels, axis=1)
    sf.write(str(path), wave, sr, subtype="PCM_16")
    return path


def test_render_dub_track_crosses_block_boundary_and_adapts_rate_and_channels(tmp_path: Path):
    """Mono 24 kHz clips placed at t=1.0s and crossing the 30s block boundary
    (t=29.5s..31.0s) render accurately into a 48 kHz stereo output track."""
    c1 = _write_tone(tmp_path / "c1.wav", 440.0, 1.0, sr=24000, channels=1, amp=0.5)
    c2 = _write_tone(tmp_path / "c2.wav", 880.0, 1.5, sr=48000, channels=1, amp=0.4)
    out = tmp_path / "vi.wav"

    audio_utils.render_dub_track(
        33.0,
        [(c1, 1.0), (c2, 29.5)],
        out,
        sample_rate=48000,
        channels=2,
    )

    assert not (tmp_path / "_silence.wav").exists()
    data, sr = sf.read(str(out), dtype="float32", always_2d=True)
    assert sr == 48000
    assert data.shape == (33 * 48000, 2)

    # Silence before clip 1 (0..0.9s) and between clips (2.5..29.0s).
    assert np.max(np.abs(data[: int(0.9 * sr)])) < 1e-4
    assert np.max(np.abs(data[int(2.5 * sr) : int(29.0 * sr)])) < 1e-4

    # Clip 1 active at 1.2..1.8s on both stereo channels (~0.5 peak).
    seg1 = data[int(1.2 * sr) : int(1.8 * sr)]
    assert 0.45 <= float(np.max(np.abs(seg1[:, 0]))) <= 0.55
    assert np.allclose(seg1[:, 0], seg1[:, 1], atol=1e-4)

    # Clip 2 active across the 30.0s streaming block seam (29.7..30.8s, ~0.4 peak).
    before_seam = data[int(29.7 * sr) : int(29.95 * sr)]
    after_seam = data[int(30.05 * sr) : int(30.8 * sr)]
    assert 0.35 <= float(np.max(np.abs(before_seam))) <= 0.45
    assert 0.35 <= float(np.max(np.abs(after_seam))) <= 0.45


def test_render_dub_track_fast_for_many_clips_over_long_timeline(tmp_path: Path):
    """200 clips over a 600-second stereo 48 kHz track must stream in a few seconds
    (old pydub O(N^2) copy took >30s even for 10 min and >35 min for 2.6 hours)."""
    clip = _write_tone(tmp_path / "clip.wav", 440.0, 0.5, sr=48000, channels=1, amp=0.3)
    clips = [(clip, float(i * 2.9)) for i in range(200)]
    out = tmp_path / "long_vi.wav"

    t0 = time.monotonic()
    audio_utils.render_dub_track(600.0, clips, out, sample_rate=48000, channels=2)
    elapsed = time.monotonic() - t0

    assert elapsed < 10.0, f"streaming mixer too slow: {elapsed:.2f}s"
    info = sf.info(str(out))
    assert info.samplerate == 48000
    assert info.channels == 2
    assert abs(info.duration - 600.0) < 0.05


def test_render_dub_track_honors_cancel(tmp_path: Path):
    clip = _write_tone(tmp_path / "c.wav", 440.0, 0.5, sr=48000, channels=1)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(StageCancelled):
        audio_utils.render_dub_track(10.0, [(clip, 0.0)], tmp_path / "out.wav", cancel=cancel)


def test_mix_overlays_preserves_base_and_adds_clip(tmp_path: Path):
    base = _write_tone(tmp_path / "base.wav", 220.0, 4.0, sr=48000, channels=1, amp=0.2)
    clip = _write_tone(tmp_path / "clip.wav", 880.0, 1.0, sr=48000, channels=1, amp=0.3)
    out = tmp_path / "mixed.wav"

    audio_utils.mix_overlays(base, [(clip, 2.0)], out)
    data, sr = sf.read(str(out), dtype="float32")
    assert sr == 48000
    # 0..1.5s has base (~0.2); 2.1..2.9s has base+clip (~0.5).
    assert 0.17 <= float(np.max(np.abs(data[: int(1.5 * sr)]))) <= 0.23
    assert 0.42 <= float(np.max(np.abs(data[int(2.1 * sr) : int(2.9 * sr)]))) <= 0.53


def test_open_in_explorer_uses_shell_reveal_and_falls_back_to_startfile(tmp_path: Path, monkeypatch):
    """Paths with spaces (like 'New folder') use Win32 reveal API instead of
    broken explorer '/select,...' subprocess quoting; fallback calls os.startfile."""
    from dubtool.app.ui import main_window as mw

    folder = tmp_path / "New folder"
    folder.mkdir()
    video = folder / "demo_vi.mp4"
    video.write_bytes(b"mp4")

    revealed: list[Path] = []
    started: list[str] = []
    monkeypatch.setattr(mw.sys, "platform", "win32")
    monkeypatch.setattr(mw, "_win_reveal_in_explorer", lambda p: (revealed.append(p), True)[1])
    monkeypatch.setattr(mw.os, "startfile", lambda p: started.append(p), raising=False)

    mw._open_in_explorer(folder, select=video)
    assert revealed == [video]
    assert started == []

    # When reveal returns False, fallback must open the exact normalized folder.
    revealed.clear()
    monkeypatch.setattr(mw, "_win_reveal_in_explorer", lambda p: (revealed.append(p), False)[1])
    mw._open_in_explorer(folder, select=video)
    assert revealed == [video]
    assert len(started) == 1
    assert Path(started[0]).resolve() == folder.resolve()
