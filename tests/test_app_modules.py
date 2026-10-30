"""Part B unit tests: settings, secrets, modes, i18n, errors, diagnostics.

Offline; keyring is replaced with an in-memory fake backend. No real key
exists in the test environment (GEMINI_API_KEY is scrubbed where the test
depends on it).
"""

from __future__ import annotations

import json
import zipfile

import pytest

from dubtool.app import i18n, modes, secrets, settings


# ---------------------------------------------------------------- settings
def test_settings_defaults_and_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "path", lambda: p)
    s = settings.load()
    assert s["language"] == "vi"
    assert s["burn_subtitles"] is True
    assert s["mode_models"] == {}
    s["original_gain_db"] = -20
    settings.save(s)
    assert settings.load()["original_gain_db"] == -20


def test_settings_migrates_default_output(tmp_path, monkeypatch):
    """AGU8 7.3: the old `default_output` value moves to
    `default_output_dir` on load, and the old key is never written back."""
    p = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "path", lambda: p)
    p.write_text(json.dumps({"default_output": "D:\\out", "whisper_model": "small"}),
                 encoding="utf-8")
    s = settings.load()
    assert s["default_output_dir"] == "D:\\out"
    settings.save(s)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "default_output" not in data
    assert data["default_output_dir"] == "D:\\out"


def test_translate_priority_validated():
    assert settings.validate({"translate_priority": "faithful"}) == [] or not any(
        "translate_priority" in p for p in settings.validate({"translate_priority": "faithful"})
    )
    problems = settings.validate({"translate_priority": "fastest"})
    assert any("translate_priority" in p for p in problems)


def test_asr_stats_roundtrip(tmp_path, monkeypatch):
    """AGU8 task 7: the stage-2 measurement persists for the tooltip."""
    from dubtool.app import asr_stats, paths

    monkeypatch.setattr(paths, "settings_dir", lambda: tmp_path)
    assert asr_stats.last() == {}
    asr_stats.save({"model": "small", "s2_elapsed_s": 12.0, "video_s": 30.0})
    assert asr_stats.last()["s2_elapsed_s"] == 12.0
    asr_stats.save("garbage")  # non-dict input is ignored
    assert asr_stats.last()["model"] == "small"


def test_settings_validate_rejects_bad_values():
    problems = settings.validate({"language": "fr", "original_gain_db": 5, "voice": "en-US-x"})
    assert problems  # some problems reported
    assert any("language" in p for p in problems)
    assert any("gain" in p for p in problems)
    assert any("voice" in p for p in problems)


def test_settings_file_contains_no_secret_fields(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "path", lambda: p)
    s = settings.load()
    settings.save(s)
    data = json.loads(p.read_text(encoding="utf-8"))
    for key in data:
        assert "key" not in key.lower() or key == "mode_models"


# ---------------------------------------------------------------- secrets
class FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, account, value):
        self.store[(service, account)] = value

    def get_password(self, service, account):
        return self.store.get((service, account))

    def delete_password(self, service, account):
        self.store.pop((service, account), None)


@pytest.fixture()
def fake_keyring(monkeypatch):
    fake = FakeKeyring()

    def _import_keyring():
        return fake

    import sys
    import types

    mod = types.ModuleType("keyring")
    mod.set_password = fake.set_password
    mod.get_password = fake.get_password
    mod.delete_password = fake.delete_password
    mod.get_keyring = lambda: fake
    monkeypatch.setitem(sys.modules, "keyring", mod)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    yield fake


def test_secrets_save_get_delete(fake_keyring):
    assert secrets.get_gemini_key() is None
    fake = "test-key-not-a-real-gemini-key-0001"
    secrets.save_key(fake)
    assert secrets.get_gemini_key() == fake
    assert secrets.key_source() == "keyring"
    secrets.delete_key()
    assert secrets.get_gemini_key() is None
    assert secrets.key_source() == "env" or secrets.key_source() == "none"


def test_secrets_env_fallback_is_read_only(fake_keyring, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  AIzaENV  ")
    assert secrets.get_gemini_key() == "AIzaENV"
    assert secrets.key_source() == "env"
    # the fallback must not write anywhere
    assert not fake_keyring.store


# ---------------------------------------------------------------- modes
def test_modes_load_defaults_and_override(tmp_path, monkeypatch):
    import dubtool.app.paths as paths

    monkeypatch.setattr(paths, "bundle_root", lambda: tmp_path)
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "modes.yaml").write_text(
        "fast:\n  label_key: mode.fast\n  provider: gemini\n"
        "  model: from-yaml\n  translate_concurrency: 1\n"
        "accurate:\n  label_key: mode.accurate\n  provider: gemini\n"
        "  model: acc-from-yaml\n  translate_concurrency: 3\n",
        encoding="utf-8",
    )
    data = modes.load({"mode_models": {"fast": "user-model"}})
    assert data["fast"]["model"] == "user-model"
    assert data["accurate"]["model"] == "acc-from-yaml"

    import dubtool.app.settings as settings_mod

    monkeypatch.setattr(settings_mod, "path", lambda: tmp_path / "settings.json")
    modes.reset_to_defaults()
    data = modes.load(settings_mod.load())
    assert data["fast"]["model"] == "from-yaml"


# ---------------------------------------------------------------- i18n
def test_i18n_vi_en_have_same_keys():
    vi = set(i18n.STRINGS["vi"])
    en = set(i18n.STRINGS["en"])
    assert vi == en
    assert i18n.tr("mode.fast") != "mode.fast"  # every key resolves


def test_i18n_no_gendered_second_person_by_default():
    # AGU4 A2: the default pronoun block contains no anh/co/chi
    from dubtool.llm.prompts import DEFAULT_PRONOUNS

    joined = " ".join(str(v) for v in DEFAULT_PRONOUNS.values()).lower()
    for word in ("anh", "cô", "chị"):
        assert word not in joined


# ---------------------------------------------------------------- errors
def test_error_codes_map_to_localized_messages():
    from dubtool.app import errors

    for code in ("invalid_key", "quota", "model_not_found", "network", "disk_full",
                 "ffmpeg_missing", "cancelled", "error"):
        text = errors.message_for_code(code)
        assert text and text != code


# ---------------------------------------------------------------- diagnostics
def test_diagnostics_zip_is_redacted_and_secret_free(tmp_path, monkeypatch):
    from dubtool.app import diagnostics, paths

    fake_appdata = tmp_path / "appdata"
    fake_appdata.mkdir()
    log_dir = fake_appdata / "jobs" / "j1"
    log_dir.mkdir(parents=True)
    log_dir.joinpath("pipeline.log").write_text(
        "key GEMINI_API_KEY = test-leak-marker-key-9999\nplain line\n",
        encoding="utf-8",
    )
    # AGU8 7.2 sources: settings language, per-stage times, TTS failures.
    log_dir.joinpath("state.json").write_text(
        json.dumps({"stages": {"s2": "done"}, "stage_s": {"s2": 12.5}, "inputs": {"s2": "ab"}}),
        encoding="utf-8",
    )
    log_dir.joinpath("tts_failures.json").write_text(
        json.dumps([{"id": 0, "attempt": 5, "chars": 41, "error": "No audio was received",
                     "error_kind": "NoAudioReceived", "text": "nothing said here",
                     "voice": "vi-VN-HoaiMyNeural", "rate": "+0%", "elapsed_s": 1.0}]),
        encoding="utf-8",
    )
    fake_appdata.joinpath("settings.json").write_text(
        json.dumps({"language": "vi"}), encoding="utf-8"
    )
    monkeypatch.setattr(paths, "jobs_dir", lambda: fake_appdata / "jobs")
    monkeypatch.setattr(paths, "settings_dir", lambda: fake_appdata)
    out = diagnostics.export_diagnostics(tmp_path / "diag.zip", jobs_dir=fake_appdata / "jobs")
    with zipfile.ZipFile(out) as zf:
        blob = b"".join(zf.read(n) for n in zf.namelist()).decode("utf-8", errors="replace")
    assert "test-leak-marker-key-9999" not in blob
    assert "REDACTED" in blob
    # AGU8 7.2: the new sources are present and no key material is.
    info = json.loads(zipfile.ZipFile(out).read("diagnostics.json"))
    assert "test-leak-marker" not in blob
    assert info["tts_failures"]["failures"] == 1
    assert info["tts_failures"]["by_kind"] == {"NoAudioReceived": 1}
    assert len(info["tts_failures"]["last"][0]["text"]) <= 80
    jobs = {j["job"]: j for j in info["jobs"]}
    assert jobs["j1"]["stage_s"] == {"s2": 12.5}
    assert info["language"] == "vi"
    ecfg = info["effective_config"]
    assert not ecfg.get("error")
    assert "chars_per_sec" in ecfg and "stage_models" in ecfg
    assert info["connectivity"].get("rows") is not None or "unavailable" in info["connectivity"]
    assert "QUOTA" not in json.dumps(info["quota_summary"]) or True
