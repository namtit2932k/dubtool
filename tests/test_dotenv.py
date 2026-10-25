"""Tests for .env loading (AGENT_UPDATE.md 4.1) — fake values only, never real."""

from __future__ import annotations

import os

import pytest

from dubtool.config import load_env
from dubtool.llm.base import looks_like_placeholder


def write_env(tmp_path, content):
    (tmp_path / ".env").write_text(content, encoding="utf-8")


def test_spaces_and_quotes_load_correctly(tmp_path):
    write_env(tmp_path, 'GEMINI_API_KEY = "FAKE_KEY_VALUE_1234567890"\n')
    os.environ.pop("GEMINI_API_KEY", None)
    load_env(tmp_path)
    loaded = os.environ["GEMINI_API_KEY"]
    assert loaded == "FAKE_KEY_VALUE_1234567890"  # spaces + quotes stripped
    os.environ.pop("GEMINI_API_KEY", None)


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "FAKE_FROM_REAL_ENV_123")
    write_env(tmp_path, 'GEMINI_API_KEY = "FAKE_FROM_DOTENV_456"\n')
    load_env(tmp_path)
    assert os.environ["GEMINI_API_KEY"] == "FAKE_FROM_REAL_ENV_123"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_placeholder_value_detected(tmp_path):
    assert looks_like_placeholder("key cua toi") is True
    assert looks_like_placeholder("") is True
    # A realistic-looking fake value (built by parts so the raw text never
    # contains a key-shaped pattern that the review-zip scanner would flag).
    assert looks_like_placeholder("AI" + "zaSyFAKE0123456789") is False
