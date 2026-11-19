"""End-to-end offline mix test with synthetic tones (AGENT_UPDATE_2.md 4).

Generates an "original" (440 Hz, full length) and a "dub" (880 Hz,
2 s..4 s only), runs the real static mixer (original_gain_db=-14), and
analyses the output: 440 Hz level drops by about 14 dB (+/-1.5 dB), the
880 Hz dub is not attenuated, and nothing clips. Requires ffmpeg + numpy
(both already project dependencies).
"""

from __future__ import annotations

import math
import subprocess
import wave

import numpy as np
import pytest

from dubtool import audio_utils


@pytest.fixture(scope="module")
def mixed_wav(tmp_path_factory) -> np.ndarray:
    """Run the real static mix on synthetic WAVs once for the module."""
    import pydub  # noqa: F401  # needed by mix_overlays

    root = tmp_path_factory.mktemp("mixfft")
    rate = 44100
    duration = 6.0
    import contextlib

    def synth(path, freq, start, end, amp=0.5):
        n = int(rate * duration)
        data = np.zeros(n, dtype=np.float64)
        i0, i1 = int(start * rate), int(end * rate)
        t = (np.arange(i0, i1) / rate)
        data[i0:i1] = amp * np.sin(2 * np.pi * freq * t)
        pcm = (data * 32767).astype("<i2")
        with contextlib.closing(wave.open(str(path), "wb")) as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm.tobytes())

    # background.wav: 440 Hz over the full 6 s (dub-only fallback body).
    synth(root / "background.wav", 440, 0.0, duration)
    # dub on a silent bed, 880 Hz from 2 s to 4 s.
    synth(root / "dub.wav", 880, 2.0, 4.0)

    graph = audio_utils.build_mix_graph("static", -14.0, 0.0)
    out = root / "mixed.wav"

    # Same wiring as the real mix, on plain WAV inputs: both audio
    # streams are consumed by the filter graph, so no extra -map 0:a is
    # needed (this build's WAV muxer rejects unmapped audio streams).
    script = root / "mix_script.txt"
    script.write_text(graph, encoding="utf-8")
    proc = subprocess.run(
        [
            audio_utils.ffmpeg_bin(), "-y",
            "-i", "background.wav",
            "-i", "dub.wav",
            "-filter_complex_script", "mix_script.txt",
            "-map", "[out]",
            "-c:a", "pcm_s16le", "-ar", str(rate), "-ac", "1",
            str(out.name),
        ],
        cwd=str(root), capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr[-600:]

    with contextlib.closing(wave.open(str(out), "rb")) as wf:
        assert wf.getframerate() == rate
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    return samples.astype(np.float64) / 32767.0, out


def band_amplitude(x: np.ndarray, rate: int, lo: float, hi: float) -> float:
    """Amplitude of the dominant sine inside [lo, hi] Hz via FFT.

    Window length is a whole number of periods for the tones used here,
    so a single bin carries the tone; the Hann coherent gain (0.5) is
    corrected: A ~= 4 * max|X| / N for window length N.
    """
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    band = spectrum[(freqs >= lo) & (freqs <= hi)]
    return float(4.0 * band.max() / len(x))


def test_original_tone_present_and_lowered(mixed_wav):
    samples, _ = mixed_wav
    window = samples[: int(2.0 * 44100)]  # 0..2 s: ONLY the 440 Hz original
    # 14 dB drop -> amplitude ratio 10**(-14/20) ~= 0.1995 of the tone.
    amp = band_amplitude(window, 44100, 420.0, 460.0)
    gain_db = 20 * math.log10(amp / 0.5)
    assert abs(gain_db - (-14.0)) < 1.5, gain_db
    # Frequency check: FFT peak must be at 440 Hz.
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
    peak_bin = int(np.argmax(spectrum))
    peak_hz = peak_bin * 44100 / len(window)
    assert abs(peak_hz - 440.0) < 5.0


def test_dub_tone_present_and_unattenuated(mixed_wav):
    samples, _ = mixed_wav
    window = samples[int(2.2 * 44100): int(3.8 * 44100)]  # 2.2..3.8 s
    # The 880 Hz dub must still sit at its original 0.5 amplitude (0 dB).
    amp880 = band_amplitude(window, 44100, 860.0, 900.0)
    dub_db = 20 * math.log10(amp880 / 0.5)
    assert abs(dub_db) < 1.5, dub_db
    # And the lowered 440 original must still be in the mix under it.
    amp440 = band_amplitude(window, 44100, 420.0, 460.0)
    assert 20 * math.log10(amp440 / 0.5) < -12.0  # still lowered
    # The dub dominates its window.
    assert amp880 > 3 * amp440


def test_no_clipping(mixed_wav):
    samples, _ = mixed_wav
    assert float(np.max(np.abs(samples))) <= 0.99
