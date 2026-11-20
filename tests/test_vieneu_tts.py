"""Unit & integration tests for offline VieNeu-TTS v3 (Update 17)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from dubtool import config as config_mod
from dubtool.app import settings as settings_mod
from dubtool.job import Job, StageCancelled, stage_inputs_hash, write_json
from dubtool.stages import s4_tts, s5_fit_duration
from dubtool.tts.base import TTSFailure, TTSUnavailableError
from dubtool.tts.vieneu import (
    DEFAULT_TTS_MODE,
    DEFAULT_VOICE,
    SHARED_VOICES,
    VieNeuTTSProvider,
    list_preset_voices,
    migrate_voice,
    normalize_mode,
    resolve_voice,
    valid_voice_ids,
)


class _FakeVieneuEngine:
    """In-memory fake for Vieneu(mode=...) returning deterministic sine audio."""

    def __init__(self, sample_rate: int = 48_000, *, return_wav=None, raise_exc=None):
        self.sample_rate = sample_rate
        self.return_wav = return_wav
        self.raise_exc = raise_exc
        self.calls: list[dict] = []
        self.closed = False

    def infer(self, text: str, voice=None, **kwargs):
        self.calls.append({"text": text, "voice": voice, "kwargs": kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.return_wav is not None:
            return self.return_wav
        t = np.linspace(0.0, 0.5, int(self.sample_rate * 0.5), endpoint=False, dtype=np.float32)
        return (0.25 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)

    def close(self) -> None:
        self.closed = True


def test_legacy_voice_migration_and_resolve() -> None:
    assert migrate_voice("vi-VN-HoaiMyNeural") == "Trúc Ly"
    assert migrate_voice("vi-VN-NamMinhNeural") == "Đức Trí"
    assert migrate_voice("") == DEFAULT_VOICE
    assert normalize_mode("v3turbo") == "v3turbo_int8"
    assert normalize_mode("nano") == "v3nano"
    assert normalize_mode("unknown") == DEFAULT_TTS_MODE

    # Voice present only in Turbo falls back to DEFAULT_VOICE in Nano.
    assert resolve_voice("Hải Đăng", "v3turbo_int8") == "Hải Đăng"
    assert resolve_voice("Hải Đăng", "v3nano") == DEFAULT_VOICE
    # Shared voices survive mode switch intact.
    for v in SHARED_VOICES:
        assert resolve_voice(v, "v3turbo_int8") == v
        assert resolve_voice(v, "v3nano") == v


def test_preset_voice_catalog_shared_pinned_first() -> None:
    turbo = list_preset_voices("v3turbo_int8")
    nano = list_preset_voices("v3nano")
    assert len(turbo) >= 20
    assert len(nano) >= 10
    assert tuple(v["id"] for v in turbo[: len(SHARED_VOICES)]) == SHARED_VOICES
    assert tuple(v["id"] for v in nano[: len(SHARED_VOICES)]) == SHARED_VOICES
    assert turbo[0]["id"] == DEFAULT_VOICE
    assert nano[0]["id"] == DEFAULT_VOICE


@pytest.mark.parametrize("mode,expected_sr", [("v3turbo_int8", 48_000), ("v3nano", 24_000)])
def test_vieneu_provider_lazy_init_explicit_voice_and_wav_write(
    tmp_path: Path, mode: str, expected_sr: int
) -> None:
    factory_calls: list[dict] = []
    fake = _FakeVieneuEngine(sample_rate=expected_sr)

    def _factory(**kwargs):
        factory_calls.append(kwargs)
        return fake

    provider = VieNeuTTSProvider(mode=mode, threads=4, cache_dir=tmp_path / "hf", engine_factory=_factory)
    assert factory_calls == []  # lazy load: untouched until synthesize()

    out = tmp_path / "clip.wav"
    provider.synthesize("Xin chào Việt Nam.", "vi-VN-HoaiMyNeural", out)
    assert len(factory_calls) == 1
    assert factory_calls[0]["threads"] == 4
    if mode == "v3turbo_int8":
        assert factory_calls[0]["mode"] == "v3turbo"
        assert factory_calls[0]["backend"] == "onnx"
        assert factory_calls[0]["precision"] == "int8"
    else:
        assert factory_calls[0]["mode"] == "v3nano"
        assert factory_calls[0]["steps"] == 16

    assert fake.calls[0]["voice"] == "Trúc Ly"  # explicit voice, migrated, never None
    assert out.is_file()
    info = sf.info(str(out))
    assert info.samplerate == expected_sr
    assert info.subtype == "PCM_16"
    assert info.duration == pytest.approx(0.5, abs=0.02)

    provider.close()
    assert fake.closed is True


def test_vieneu_provider_rejects_empty_nonfinite_and_too_short(tmp_path: Path) -> None:
    # Empty text
    p_empty = VieNeuTTSProvider(cache_dir=tmp_path / "hf", engine_factory=lambda **kw: _FakeVieneuEngine())
    with pytest.raises(TTSFailure, match="empty text"):
        p_empty.synthesize("   ", "Trúc Ly", tmp_path / "a.wav")

    # Non-finite samples
    bad = np.array([0.0, np.nan] * 5000, dtype=np.float32)
    p_nan = VieNeuTTSProvider(cache_dir=tmp_path / "hf", engine_factory=lambda **kw: _FakeVieneuEngine(return_wav=bad))
    with pytest.raises(TTSFailure, match="non-finite"):
        p_nan.synthesize("Xin chào", "Trúc Ly", tmp_path / "b.wav")

    # Too short (<= 0.05s)
    short = np.zeros(100, dtype=np.float32)
    p_short = VieNeuTTSProvider(cache_dir=tmp_path / "hf", engine_factory=lambda **kw: _FakeVieneuEngine(return_wav=short))
    with pytest.raises(TTSFailure, match="too short"):
        p_short.synthesize("Xin chào", "Trúc Ly", tmp_path / "c.wav")


def test_vieneu_provider_cancel_honored(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    provider = VieNeuTTSProvider(cache_dir=tmp_path / "hf", engine_factory=lambda **kw: _FakeVieneuEngine())
    provider.cancel = cancel
    with pytest.raises(StageCancelled):
        provider.synthesize("Xin chào", "Trúc Ly", tmp_path / "cancel.wav")


def test_vieneu_provider_network_error_message_when_uncached(tmp_path: Path) -> None:
    def _boom(**_kw):
        raise ConnectionError("Failed to resolve huggingface.co")

    provider = VieNeuTTSProvider(cache_dir=tmp_path / "empty_hf", engine_factory=_boom)
    with pytest.raises(TTSFailure, match="Hugging Face"):
        provider.synthesize("Xin chào", "Trúc Ly", tmp_path / "net.wav")


def test_s4_wav_cache_resume_and_mode_invalidation(tmp_path: Path, monkeypatch) -> None:
    job = Job(tmp_path / "job")
    job.ensure_dirs()
    write_json(
        job.translated_path,
        [
            {"id": 1, "start": 0.0, "end": 2.0, "text": "Hello.", "vi": "Xin chào."},
            {"id": 2, "start": 2.0, "end": 4.0, "text": "World.", "vi": "Thế giới."},
        ],
    )
    cfg = config_mod.Config()
    cfg.tts_trim_silence = False
    cfg.tts_voice = "Trúc Ly"
    cfg.tts = {"provider": "vieneu", "mode": "v3turbo_int8", "direct_retries": 0, "max_chars_per_request": 200}

    engines: list[_FakeVieneuEngine] = []

    def _fake_build(c, cache_dir, media_cache=None):
        mode = normalize_mode((c.tts or {}).get("mode"))
        sr = 48_000 if mode == "v3turbo_int8" else 24_000
        eng = _FakeVieneuEngine(sample_rate=sr)
        engines.append(eng)
        prov = VieNeuTTSProvider(mode=mode, cache_dir=tmp_path / "hf", engine_factory=lambda **kw: eng)
        return s4_tts.SegmentTTS(prov, cache_dir, c.tts_voice, options=s4_tts.tts_options(c), media_cache=media_cache)

    monkeypatch.setattr(s4_tts, "build_tts", _fake_build)
    monkeypatch.setattr(s4_tts.audio_utils, "require_ffmpeg", lambda: None)

    # First run generates 2 WAV files and closes provider
    s4_tts.run(job, cfg)
    assert len(engines[0].calls) == 2
    assert engines[0].closed is True
    assert (job.tts_dir / "1.wav").is_file()
    assert (job.tts_dir / "2.wav").is_file()
    assert list((job.tts_dir / "cache").glob("*.wav"))
    assert not list((job.tts_dir / "cache").glob("*.mp3"))

    # Second run resumes from existing clips (0 synthesis calls)
    s4_tts.run(job, cfg)
    assert len(engines[1].calls) == 0

    # Changing tts mode invalidates s4 stage_inputs_hash
    h_turbo = stage_inputs_hash(job, cfg, "s4")
    cfg.tts["mode"] = "v3nano"
    h_nano = stage_inputs_hash(job, cfg, "s4")
    assert h_turbo != h_nano


def test_s4_long_text_split_and_join_native_sr(tmp_path: Path, monkeypatch) -> None:
    job = Job(tmp_path / "job_split")
    job.ensure_dirs()
    long_vi = (
        "Đây là câu thứ nhất rất dài để kiểm tra tách đoạn tự động trên VieNeu cục bộ. "
        "Đây là câu thứ hai cũng khá dài với nhiều thông tin chi tiết hơn nữa. "
        "Đây là câu thứ ba chốt lại toàn bộ ví dụ thử nghiệm."
    )
    write_json(job.translated_path, [{"id": 1, "start": 0.0, "end": 10.0, "text": "Long.", "vi": long_vi}])
    cfg = config_mod.Config()
    cfg.tts_trim_silence = False
    cfg.tts = {"provider": "vieneu", "mode": "v3turbo_int8", "direct_retries": 0, "max_chars_per_request": 80, "join_silence_s": 0.08}

    class _FailLongProvider:
        def __init__(self):
            self.calls: list[str] = []
            self.closed = False

        def synthesize(self, text, voice, out_path):
            self.calls.append(text)
            if len(text) > 100:
                raise TTSFailure("too long for direct step")
            sr = 48_000
            t = np.linspace(0.0, 0.3, int(sr * 0.3), endpoint=False, dtype=np.float32)
            sf.write(str(out_path), 0.2 * np.sin(2 * np.pi * 300 * t), sr, subtype="PCM_16")

        def close(self):
            self.closed = True

    prov = _FailLongProvider()
    monkeypatch.setattr(
        s4_tts,
        "build_tts",
        lambda c, cache_dir, media_cache=None: s4_tts.SegmentTTS(
            prov, cache_dir, "Trúc Ly", options=s4_tts.tts_options(c), media_cache=media_cache
        ),
    )
    monkeypatch.setattr(s4_tts.audio_utils, "require_ffmpeg", lambda: None)

    s4_tts.run(job, cfg)
    assert len(prov.calls) >= 3  # 1 failed direct + >=2 split chunks
    assert prov.closed is True
    out = job.tts_dir / "1.wav"
    assert out.is_file()
    info = sf.info(str(out))
    assert info.samplerate == 48_000
    assert info.duration > 0.6


def test_settings_load_migrates_legacy_edge_voice_and_validates(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps({"voice": "vi-VN-NamMinhNeural", "tts_backup_voice": "windows_sapi:An"}),
        encoding="utf-8",
    )
    loaded = settings_mod.load(p)
    assert loaded["voice"] == "Đức Trí"
    assert loaded["tts_model"] == DEFAULT_TTS_MODE
    assert "tts_backup_voice" not in loaded
    assert settings_mod.validate(loaded) == []

    # Invalid mode or voice caught by validate()
    bad = dict(loaded, tts_model="bad_mode")
    assert settings_mod.validate(bad)
    bad_v = dict(loaded, voice="NotARealVoice")
    assert settings_mod.validate(bad_v)


def test_settings_dialog_mode_switch_updates_voices(qtbot, tmp_path: Path, monkeypatch) -> None:
    from dubtool.app.ui.settings_dialog import SettingsDialog

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_mod, "path", lambda: settings_file)
    settings_mod.save(dict(settings_mod.DEFAULTS, tts_model="v3turbo_int8", voice="Hải Đăng"), settings_file)

    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    assert dlg.tts_mode_combo.currentData() == "v3turbo_int8"
    assert dlg.voice_combo.currentData() == "Hải Đăng"
    assert dlg.voice_combo.count() == len(valid_voice_ids("v3turbo_int8"))

    # Switch to v3nano -> Hải Đăng is turbo-only, so it falls back to Trúc Ly.
    nano_idx = dlg.tts_mode_combo.findData("v3nano")
    dlg.tts_mode_combo.setCurrentIndex(nano_idx)
    assert dlg.voice_combo.count() == len(valid_voice_ids("v3nano"))
    assert dlg.voice_combo.currentData() == DEFAULT_VOICE

    # Pick a shared voice and switch back -> preserved.
    ma_idx = dlg.voice_combo.findData("Mai Anh")
    dlg.voice_combo.setCurrentIndex(ma_idx)
    turbo_idx = dlg.tts_mode_combo.findData("v3turbo_int8")
    dlg.tts_mode_combo.setCurrentIndex(turbo_idx)
    assert dlg.voice_combo.currentData() == "Mai Anh"


def test_s5_reads_wav_and_closes_tts_provider(tmp_path: Path, monkeypatch) -> None:
    job = Job(tmp_path / "job_s5")
    job.ensure_dirs()
    sr = 48_000
    t = np.linspace(0.0, 0.6, int(sr * 0.6), endpoint=False, dtype=np.float32)
    sf.write(str(job.tts_dir / "1.wav"), 0.2 * np.sin(2 * np.pi * 440 * t), sr, subtype="PCM_16")
    write_json(
        job.translated_path,
        [{"id": 1, "start": 0.0, "end": 2.0, "text": "Hi.", "vi": "Xin chào.", "tts_duration": 0.6, "max_chars": 30}],
    )
    cfg = config_mod.Config()
    closed = {"flag": False}

    class _DummyProvider:
        def close(self):
            closed["flag"] = True

    monkeypatch.setattr(
        s4_tts,
        "build_tts",
        lambda c, cache_dir, media_cache=None: s4_tts.SegmentTTS(_DummyProvider(), cache_dir, "Trúc Ly", media_cache=media_cache),
    )
    monkeypatch.setattr(s5_fit_duration.audio_utils, "require_ffmpeg", lambda: None)
    monkeypatch.setattr(
        s5_fit_duration.audio_utils,
        "convert_to_wav",
        lambda src, dst, cancel=None: dst.write_bytes(src.read_bytes()),
    )

    s5_fit_duration.run(job, cfg)
    assert closed["flag"] is True
    assert (job.fitted_dir / "1.wav").is_file()


def test_no_edge_tts_runtime_references_in_src() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "dubtool"
    forbidden = ("import edge_tts", "from edge_tts", "dubtool.tts.edge", "dubtool.tts.fallback", "dubtool.tts_probe")
    hits = []
    for py in sorted(src_root.rglob("*.py")):
        txt = py.read_text(encoding="utf-8", errors="replace")
        for needle in forbidden:
            if needle in txt:
                hits.append(f"{py.name}: {needle}")
    assert hits == []
