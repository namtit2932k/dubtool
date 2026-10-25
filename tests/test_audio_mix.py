"""Offline mixing test: build tiny WAV fixtures with the stdlib (`wave`),
then verify pydub overlay math used by stage 6. No ffmpeg needed for WAV->WAV.
"""

from __future__ import annotations

import math
import wave

import pytest

pydub = pytest.importorskip("pydub")

from pydub import AudioSegment  # noqa: E402

SAMPLE_RATE = 44100


def write_tone(path, seconds: float, freq: float = 440.0) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        n = int(seconds * SAMPLE_RATE)
        frames = bytearray()
        for i in range(n):
            value = int(12000 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE))
            frames += value.to_bytes(2, "little", signed=True)
        wf.writeframes(bytes(frames))


def test_overlay_positions_match_stage6_math(tmp_path):
    base = AudioSegment.silent(duration=3000, frame_rate=SAMPLE_RATE).set_channels(1)
    clip = AudioSegment.from_wav(str(_make(tmp_path / "tone.wav"))).set_frame_rate(SAMPLE_RATE)

    # Simulate stage 6: overlay one clip at 1.0s offset.
    mixed = base.overlay(clip, position=1000)
    assert len(mixed) == 3000  # total length preserved

    # Region before the offset stays silent; the tone region is loud.
    def rms(seg):
        return seg.rms if hasattr(seg, "rms") else None

    assert rms(mixed[:1000]) == 0  # silence before clip starts
    assert rms(mixed[1000:2000]) > 0  # tone audible during the clip


def _make(path):
    write_tone(path, 1.0)
    return path
