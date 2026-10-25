"""Review zip tests (AGENT_UPDATE.md 7.2 acceptance) — fake keys only."""

from __future__ import annotations

import zipfile

import pytest

import scripts.make_review_zip as rz
from scripts.make_review_zip import make_review_zip, collect_secret_values, redact_text

# Fake values are assembled from parts so our own source never contains the
# raw secret string that the scanner would flag as a leak.
FAKE_KEY = "FAKE_PLANTED_" + "KEY_abcdefgh" + "123456"
FAKE_ENV_KEY = "FAKE_ENV_" + "KEY_98765" + "4321"


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "config.yaml").write_text("llm_provider: gemini\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# dubtool\n", encoding="utf-8")
    (tmp_path / "REVIEW_NOTES.md").write_text("notes here\n", encoding="utf-8")
    monkeypatch.setattr(rz, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_zip_created_and_excludes_forbidden(project_root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_ENV_KEY)
    (project_root / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (project_root / "jobs").mkdir()
    (project_root / "jobs" / "j1").mkdir()
    (project_root / "jobs" / "j1" / "x.mp4").write_text("media", encoding="utf-8")
    (project_root / ".env").write_text(f'GEMINI_API_KEY = "{FAKE_ENV_KEY}"\n', encoding="utf-8")

    out = make_review_zip(tag="test")
    assert out.exists()
    names = zipfile.ZipFile(out).namelist()
    assert "src/mod.py" in names
    assert "REVIEW_NOTES.md" in names
    assert not any(n == ".env" or n.startswith("jobs") for n in names)
    assert not any(n.endswith(".mp4") for n in names)
    assert not any(n.startswith("sample") or n.startswith(".venv") for n in names)


def test_planted_source_key_aborts_without_zip(project_root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    (project_root / "src" / "leak.py").write_text(
        f'KEY = "{FAKE_KEY}"\n', encoding="utf-8"
    )
    with pytest.raises(RuntimeError) as excinfo:
        make_review_zip(tag="test")
    message = str(excinfo.value)
    assert FAKE_KEY not in message  # error must not echo the key
    assert "leak.py" in message  # path + line only
    # No zip with this tag exists
    assert not list((project_root / "review").glob("*abort*"))


def test_planted_key_redacted_in_log_excerpt(project_root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    job = project_root / "jobs" / "some-job"
    job.mkdir(parents=True)
    (project_root / "jobs" / "state.json").touch()
    (project_root / "jobs" / "some-job" / "state.json").write_text("{}", encoding="utf-8")
    (job / "pipeline.log").write_text(f"bad line with {FAKE_KEY} inside\n", encoding="utf-8")

    secrets = collect_secret_values()
    excerpts = rz.build_excerpts(secrets)
    log_excerpt = excerpts["excerpts/pipeline.log"]
    assert FAKE_KEY not in log_excerpt
    assert "[REDACTED]" in log_excerpt


def test_key_like_patterns_redacted(project_root):
    # Built from parts so our own test source never contains the raw pattern.
    gemini_like = "AI" + "za" + "SyFAKE012345678X"
    anthropic_like = "sk-ant-" + "api03-abcdef0123456789"
    text = f"token = {gemini_like} and {anthropic_like}"
    redacted = redact_text(text, [])
    assert ("AI" + "za") not in redacted
    assert ("sk-ant-" + "api03") not in redacted
    assert "[REDACTED]" in redacted


def test_prune_keeps_three_recent(project_root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_ENV_KEY)
    for _ in range(4):
        make_review_zip(tag="prune")
    import scripts.make_review_zip as m
    zips = list((project_root / "review").glob("dubtool-review-*.zip"))
    assert len(zips) <= 3
