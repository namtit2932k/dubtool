"""Provider factory tests (AGENT_UPDATE.md 5): offline, fake keys only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dubtool import llm as llm_pkg
from dubtool.config import Config
from dubtool.llm import get_provider, require_provider_ready
from dubtool.llm.base import LLMError, MissingKeyError, looks_like_placeholder


class TestFactory:
    def test_gemini_provider_selected(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "FAKEKEY1234567890")
        config = Config(llm_provider="gemini")
        provider = get_provider(config, "gemini")
        assert provider.name == "gemini"
        assert provider.model == "gemini-3.5-flash-lite"  # from config defaults

    def test_ollama_provider_selected(self, monkeypatch):
        config = Config(llm_provider="ollama")
        provider = get_provider(config, "ollama")
        assert provider.name == "ollama"
        assert provider.host == "http://localhost:11434"

    def test_anthropic_provider_selected(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "FAKEKEY987654321")
        config = Config(llm_provider="anthropic")
        provider = get_provider(config, "anthropic")
        assert provider.name == "anthropic"
        assert provider.model == "claude-sonnet-5-5"

    def test_unknown_provider_errors_clearly(self):
        with pytest.raises(LLMError, match="unknown llm provider"):
            get_provider(SimpleNamespace(llm_provider="madeup"), "madeup")

    def test_missing_gemini_key_fails_early(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(MissingKeyError):
            get_provider(Config(llm_provider="gemini"), "gemini")


class TestReadiness:
    def test_only_selected_provider_key_required(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "FAKEKEY1234567890")
        # No exception even though ANTHROPIC_API_KEY is unset:
        require_provider_ready(Config(llm_provider="gemini"), "gemini")

    def test_placeholder_detected(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "key cua toi")
        with pytest.raises(Exception, match="placeholder"):
            require_provider_ready(Config(llm_provider="gemini"), "gemini")


class TestPlaceholderDetection:
    @pytest.mark.parametrize(
        "value", ["", "  ", "key cua toi", "put your key here", "abc def"]
    )
    def test_placeholders(self, value):
        assert looks_like_placeholder(value) is True

    @pytest.mark.parametrize(
        "value",
        ["AI" + "zaSyFAKEKEY0123456789", "sk-ant-" + "api03-fake"],
    )
    def test_fake_real_looking_keys_are_not_placeholders(self, value):
        # look-alike test values, never real keys
        assert looks_like_placeholder(value) is False
