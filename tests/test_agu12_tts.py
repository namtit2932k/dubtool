"""Update 12 task 5: the voice-request limiter and the honest experiment.

- the global limiter halves to one request per 5 s when more than 30% of
  the last 10 requests were refused, and recovers (slowly) after 10
  consecutive successes — with the rate change.visible in the panel;
- a handshake 503 counts as a refusal like any other failure;
- `--tts-probe` (the same code from source and from the exe) interleaves
  the three rate conditions, fires a control every 10 requests, and
  prints a results table.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dubtool.tts.base import TTSFailure
from dubtool.tts_probe import _classify, run_experiment


# ---------------------------------------------------------------- limiter


def test_limiter_halves_on_refusals_with_a_floor():
    from dubtool.stages.s4_tts import VoiceRequestLimiter

    lim = VoiceRequestLimiter(max_rps=1.0)
    assert lim.current_interval_s == pytest.approx(1.0)
    emoted: list[str] = []
    for _ in range(3):
        lim.record(False)
    # Three refusals in a row: the share of the last 10 exceeds 30% every
    # time -> 1 s -> 2 s -> 4 s -> the 5 s floor.
    assert lim.current_interval_s == pytest.approx(5.0)
    assert lim.refusals == 3


def test_limiter_recovers_slowly_after_successes():
    from dubtool.stages.s4_tts import VoiceRequestLimiter

    lim = VoiceRequestLimiter(max_rps=1.0)
    lim.current_interval_s = 5.0
    for _ in range(10):
        lim.record(True)
    # 10 consecutive successes recover HALF of the excess (never below the
    # configured interval) — a slow recovery, not a full reset.
    assert lim.current_interval_s == pytest.approx(3.0)
    assert lim.success_streak == 0


def test_limiter_gate_paces_requests(tmp_path, monkeypatch):
    from dubtool import audio_utils
    from dubtool.stages import s4_tts
    from dubtool.stages.s4_tts import SegmentTTS, VoiceRequestLimiter

    monkeypatch.setattr(audio_utils, "require_ffmpeg", lambda: None)

    sleeps: list[float] = []

    lim = VoiceRequestLimiter(max_rps=2.0)  # 0.5 s between requests

    class _Ok:
        def synthesize(self, text, voice, out_path):
            import wave

            out_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_path), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(24000)
                f.writeframes(b"\x00\x00" * 2400)

    (tmp_path / "cache").mkdir(parents=True, exist_ok=True)
    tts = SegmentTTS(_Ok(), tmp_path / "cache", "vi-VN-Test")
    tts.limiter = lim
    monkeypatch.setattr("dubtool.heartbeat.sleeping",
                        lambda s, text=None, cancel=None: sleeps.append(s))
    tts.synthesize_text("câu một", "vi-VN-Test")
    tts.synthesize_text("câu hai", "vi-VN-Test")
    assert lim.requests == 2 and lim.refusals == 0
    # The second request was paced (a non-negative wait within the interval;
    # execution time between the requests may consume part of it).
    assert sleeps and 0 < sleeps[0] <= 0.5


def test_handshake_503_counts_as_refusal(tmp_path):
    from dubtool.stages.s4_tts import SegmentTTS, VoiceRequestLimiter

    class _Handshake503:
        def synthesize(self, text, voice, out_path):
            raise TTSFailure(
                "WSServerHandshakeError: HTTP 503 Invalid response status "
                "from speech.platform.bing.com"
            )

    lim = VoiceRequestLimiter(max_rps=1.0)
    tts = SegmentTTS(_Handshake503(), tmp_path / "cache", "vi-VN-Test")
    tts.limiter = lim
    with pytest.raises(TTSFailure):
        tts.synthesize_text("câu thử", "vi-VN-Test")
    assert lim.requests == 1 and lim.refusals == 1


def test_handshake_503_is_classified():
    class _E(Exception):
        pass

    assert _classify(TTSFailure(
        "WSServerHandshakeError: HTTP 503 Invalid response status")) == "handshake_503"
    assert _classify(TTSFailure("NoAudioReceived for unknown reason")) == "no_audio"
    assert _classify(TTSFailure("connection timeout after 45 s")) == "timeout"
    assert _classify(TTSFailure("something else entirely")) == "other"


# ----------------------------------------------------------------- probe


def test_probe_interleaves_with_control_and_table(tmp_path, monkeypatch, capsys):
    import dubtool.tts_probe as probe

    monkeypatch.setattr(probe, "_sleep", lambda s: None)  # no real waiting
    calls = {"n": 0}

    class _Fake:
        def synthesize(self, text, voice, out_path):
            calls["n"] += 1
            if calls["n"] % 4 == 0:
                raise TTSFailure(
                    "WSServerHandshakeError: HTTP 503 Invalid response status"
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"mp3")

    cfg = SimpleNamespace(tts_voice="vi-VN-Test", tts={"request_timeout_s": 5.0})
    data = run_experiment(
        cfg, requests=12, provider=_Fake(),
        texts=[f"câu {i}" for i in range(12)],
    )
    captured = capsys.readouterr().out
    # Every condition saw all 12 texts (interleaved, same window).
    assert all(c["requests"] == 12 for c in data["conditions"].values())
    # A control request fired at the 10th request.
    assert data["controls"]["requests"] == 1
    # The failures were classified, not merged (the fake refuses every
    # 4th of the 36 condition requests).
    sums = {
        k: sum(c[k] for c in data["conditions"].values())
        for k in ("ok", "handshake_503", "other")
    }
    assert sums["handshake_503"] == 9
    assert sums["ok"] + sums["handshake_503"] + sums["other"] == 36
    # The printed table names every condition and the control row.
    assert "handshake_503" in captured
    assert "control" in captured
    assert "1/3s" in captured and "1/10s" in captured


def test_probe_writes_results_file(tmp_path, monkeypatch):
    import dubtool.tts_probe as probe

    monkeypatch.setattr(probe, "_sleep", lambda s: None)

    class _Ok:
        def synthesize(self, text, voice, out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"mp3")

    cfg = SimpleNamespace(tts_voice="vi-VN-Test", tts={"request_timeout_s": 5.0})
    out = tmp_path / "probe.json"
    run_experiment(cfg, out_path=str(out), requests=3,
                   provider=_Ok(), texts=["câu một", "câu hai", "câu ba"])
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert '"controls"' in body and '"conditions"' in body
