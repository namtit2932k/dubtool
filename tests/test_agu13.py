"""AGENT_UPDATE_13 acceptance tests:
- Model catalog (packaging/models.yaml) loading, grouping, and inference
- Multi-provider API keys storage, autodetection, and environment fallback
- Unified Tạo button and Model dropdown UI with paid notices
- OpenAI and Anthropic provider contracts (Responses API, schemas, max_retries=0)
- Unified error classification across providers
- Packaging & version 1.0.13 consistency
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt

import dubtool
from dubtool.app import i18n, paths, secrets, settings
from dubtool.llm import catalog
from dubtool.llm.base import (
    InvalidKeyError,
    QuotaExhaustedError,
    RateLimitError,
    ServerErrorLLMError,
)


# ------------------------------------------------------------------ 1. Catalog
def test_models_catalog_shipped_file():
    items = catalog.load()
    assert len(items) >= 9
    assert "gemini-3.5-flash-lite" in items
    lite = items["gemini-3.5-flash-lite"]
    assert lite.provider == "gemini"
    assert lite.paid is False
    assert lite.structured == "json_schema"

    # OpenAI models
    assert "gpt-5.4-nano" in items
    nano = items["gpt-5.4-nano"]
    assert nano.provider == "openai"
    assert nano.paid is True
    assert nano.reasoning is True

    # Anthropic models
    assert "claude-haiku-5-5" in items
    haiku = items["claude-haiku-5-5"]
    assert haiku.provider == "anthropic"
    assert haiku.paid is True

    # Ensure no actual prices or currency symbols appear in the catalog
    path = catalog.catalog_path()
    lines = [
        ln.split("#")[0].strip() for ln in path.read_text(encoding="utf-8").splitlines()
    ]
    code_text = " ".join(lines).lower()
    for forbidden in ("$", "usd", "vnd"):
        assert forbidden not in code_text


def test_catalog_inference_and_grouping():
    assert catalog.infer_provider("gemini-3.5-flash-lite") == "gemini"
    assert catalog.infer_provider("gpt-5.4-nano") == "openai"
    assert catalog.infer_provider("claude-haiku-5-5") == "anthropic"

    grouped = dict(catalog.grouped_order())
    assert "gemini" in grouped
    assert "openai" in grouped
    assert "anthropic" in grouped
    assert any(m.id == "gemini-3.5-flash-lite" for m in grouped["gemini"])
    assert any(m.id == "gpt-5.4-nano" for m in grouped["openai"])
    assert any(m.id == "claude-haiku-5-5" for m in grouped["anthropic"])


# ------------------------------------------------------------------ 2. Multi-provider Secrets
def test_secrets_provider_autodetection():
    detect = secrets.detect_provider
    assert detect("AIzaSyDummyGeminiKey123456789012345678") == "gemini"  # fixture-fake-key
    assert detect("AQ.DummyGeminiStudioKey12345678901234567890") == "gemini"  # fixture-fake-key
    assert detect("sk-ant-api03-DummyAnthropicKey1234567890123456") == "anthropic"  # fixture-fake-key
    assert detect("sk-proj-DummyOpenAIKey1234567890123456789012345") == "openai"  # fixture-fake-key
    assert detect("sk-DummyStandardOpenAIKey12345678901234567890") == "openai"  # fixture-fake-key


def test_secrets_multi_provider_env_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GPT_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)

    monkeypatch.setattr(secrets, "_stored_list", lambda _p: [])

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-1")  # fixture-fake-key
    monkeypatch.setenv("GPT_API_KEY_2", "sk-test-openai-2")  # fixture-fake-key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-anthropic-1")  # fixture-fake-key

    assert secrets.get_keys("openai") == ["sk-test-openai-1", "sk-test-openai-2"]
    assert secrets.get_keys("anthropic") == ["sk-ant-test-anthropic-1"]  # fixture-fake-key
    assert secrets.has_key("openai") is True
    assert secrets.has_key("anthropic") is True
    assert secrets.has_key("gemini") is False


# ------------------------------------------------------------------ 3. UI Unified Generate & Models Dropdown
@pytest.fixture()
def app_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(paths, "jobs_dir", lambda: tmp_path / "jobs")
    monkeypatch.setattr(secrets, "_stored_list", lambda _p: [])
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTestKey")  # fixture-fake-key

    from dubtool.app.service import PipelineService
    from dubtool.app.ui.main_window import MainWindow

    win = MainWindow(PipelineService())
    qtbot.addWidget(win)
    win.show()
    yield win
    win.hide()


def test_main_window_has_unified_generate_and_model_dropdown(app_window):
    assert hasattr(app_window, "generate_btn")
    assert hasattr(app_window, "model_combo")
    assert app_window.generate_btn.text() == i18n.tr("run.generate")

    combo = app_window.model_combo
    assert combo.count() > 0
    model = combo.model()
    gemini_found = False
    openai_disabled = False
    for row in range(model.rowCount()):
        item = model.item(row)
        model_id = item.data(Qt.ItemDataRole.UserRole)
        if model_id == "gemini-3.5-flash-lite":
            assert item.isEnabled() is True
            gemini_found = True
        elif model_id == "gpt-5.4-nano":
            assert item.isEnabled() is False
            openai_disabled = True
    assert gemini_found and openai_disabled


def test_paid_notice_dialog(qtbot):
    from PySide6.QtWidgets import QLabel
    from dubtool.app.ui.paid_notice import PaidProviderConfirmDialog

    dlg = PaidProviderConfirmDialog(provider_label="OpenAI")
    qtbot.addWidget(dlg)
    dlg.show()
    labels = [w.text() for w in dlg.findChildren(QLabel)]
    assert any("OpenAI" in txt for txt in labels)
    assert hasattr(dlg, "dont_ask")


# ------------------------------------------------------------------ 4. OpenAI Provider Contract
def test_openai_provider_complete_structured():
    from dubtool.llm.openai import OpenAIProvider

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = '{"translation": "Xin chào thế giới"}'
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.responses.create.return_value = mock_response

    provider = OpenAIProvider(
        model="gpt-5.4-mini",
        api_keys=["sk-test-dummy"],
        reasoning=True,
    )
    provider._client_for = lambda _key: mock_client

    schema = {
        "name": "DubUnits",
        "strict": True,
        "schema": {"type": "object", "properties": {"translation": {"type": "string"}}},
    }
    result = provider.complete("System prompt", "User prompt", schema=schema)
    assert result.text == '{"translation": "Xin chào thế giới"}'

    # Verify Responses API call
    mock_client.responses.create.assert_called_once()
    kwargs = mock_client.responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.4-mini"
    assert kwargs["instructions"] == "System prompt"
    assert kwargs["input"] == "User prompt"
    assert "reasoning" in kwargs  # Reasoning model sends reasoning dict
    assert "temperature" not in kwargs


# ------------------------------------------------------------------ 5. Anthropic Provider Contract
def test_anthropic_provider_complete_mock():
    from dubtool.llm.anthropic import AnthropicProvider

    mock_client = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = '{"units": []}'
    mock_response = MagicMock()
    mock_response.content = [mock_block]
    mock_response.stop_reason = "end_turn"
    mock_response.usage = MagicMock(input_tokens=5, output_tokens=15)
    mock_client.messages.create.return_value = mock_response

    provider = AnthropicProvider(
        model="claude-haiku-5-5",
        api_keys=["sk-ant-dummy"],  # fixture-fake-key
    )
    provider._client_for = lambda _key: mock_client

    result = provider.complete("System prompt", "User prompt")
    assert result.text == '{"units": []}'
    mock_client.messages.create.assert_called_once()


# ------------------------------------------------------------------ 6. Error Classification
def test_openai_error_mapping():
    from dubtool.llm.openai import OpenAIProvider
    import openai

    mock_client = MagicMock()
    provider = OpenAIProvider(model="gpt-5.4-nano", api_keys=["sk-dummy"])
    provider._client_for = lambda _key: mock_client

    # 429 RateLimit
    mock_client.responses.create.side_effect = openai.RateLimitError(
        "Rate limit exceeded", response=MagicMock(status_code=429, headers={"retry-after": "5"}), body={}
    )
    with pytest.raises(RateLimitError) as exc:
        provider.complete("s", "u")
    assert exc.value.retry_after == 5.0

    # 401 Invalid Key
    mock_client.responses.create.side_effect = openai.AuthenticationError(
        "Incorrect API key", response=MagicMock(status_code=401, headers={}), body={}
    )
    with pytest.raises(InvalidKeyError):
        provider.complete("s", "u")

    # 503 Server Error
    mock_client.responses.create.side_effect = openai.InternalServerError(
        "Service unavailable", response=MagicMock(status_code=503, headers={}), body={}
    )
    with pytest.raises(ServerErrorLLMError):
        provider.complete("s", "u")


def test_anthropic_error_mapping():
    from dubtool.llm.anthropic import AnthropicProvider
    import anthropic

    mock_client = MagicMock()
    provider = AnthropicProvider(model="claude-haiku-5-5", api_keys=["sk-ant-dummy"])  # fixture-fake-key
    provider._client_for = lambda _key: mock_client

    # 401 Authentication
    mock_client.messages.create.side_effect = anthropic.AuthenticationError(
        "Invalid API key", response=MagicMock(status_code=401, headers={}), body={}
    )
    with pytest.raises(InvalidKeyError):
        provider.complete("s", "u")

    # Credit balance too low -> QuotaExhaustedError
    mock_client.messages.create.side_effect = anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API",
        response=MagicMock(status_code=400, headers={}), body={"error": {"type": "credit_balance_too_low"}}
    )
    with pytest.raises(QuotaExhaustedError):
        provider.complete("s", "u")


# ------------------------------------------------------------------ 7. Packaging & Version Consistency
def test_version_1_0_13_consistency():
    assert dubtool.__version__ == "1.0.13"
    update_num = (Path(__file__).resolve().parents[1] / "packaging" / "update_number.txt").read_text().strip()
    assert update_num == "13"
