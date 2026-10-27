"""AGENT_UPDATE_7.md task 3 tests: the Gemini client factory contract with
the REAL google-genai SDK (no network), and type-based error classification
with real exception instances (httpx, pydantic, socket).

Contract tests fail hard when google-genai is not installed (as required:
never silently skip — the dev environment has the SDK).
"""

from __future__ import annotations

import socket

import httpx
import pytest

# Real SDK required — errors out (does not skip) when missing.
from dubtool.llm.base import InternalLLMError, LLMError
from dubtool.llm.gemini import build_gemini_client

REAL_TIMEOUT_MS = 180_000  # the value the update-6 build sent as "180000ms"


class TestClientContract:
    """Same constructor arguments and option objects as production."""

    def test_timeout_sanitizes_to_int_ms(self):
        client = build_gemini_client("fixture-fake-key", timeout_ms=REAL_TIMEOUT_MS)
        opts = client._api_client.get_read_only_http_options()
        assert opts["timeout"] == REAL_TIMEOUT_MS
        assert isinstance(opts["timeout"], int)  # pydantic would reject "180000ms"

    def test_no_timeout_path_constructs(self):
        client = build_gemini_client("fixture-fake-key", timeout_ms=None)
        assert client._api_client.get_read_only_http_options().get("timeout") in (
            None, 0,
        )

    def test_generation_config_validates(self):
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction="system prompt",
            temperature=0.3,
            max_output_tokens=8000,
            response_mime_type="application/json",
        )
        assert config.max_output_tokens == 8000

    def test_provider_client_for_builds_real_client(self):
        from dubtool.llm.gemini import GeminiProvider

        provider = GeminiProvider(
            "gemini-fake-model", api_keys=["fixture-fake-key"], timeout_s=180.0
        )
        client = provider.client_for("fixture-fake-key")
        opts = client._api_client.get_read_only_http_options()
        assert opts["timeout"] == 180_000

    def test_http_options_rejects_the_old_string_format(self):
        """The update-6 bug, pinned forever: HttpOptions.timeout must be an
        int in ms — a string like "180000ms" must raise (proving the fix
        was real and every client goes through the int factory)."""
        from google.genai import types
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            types.HttpOptions(timeout="180000ms")


class TestErrorClassification:
    """AGENT_UPDATE_7.md 4.3: by exception type along the cause chain; the
    unknown path must NEVER produce the network message."""

    def test_connect_timeout_is_timeout(self):
        from dubtool import api

        code, _ = api._classify_error(httpx.ConnectTimeout("timed out connecting"))
        assert code == "timeout"

    def test_connect_error_is_network(self):
        from dubtool import api

        code, _ = api._classify_error(httpx.ConnectError("no route to host"))
        assert code == "network"

    def test_dns_failure_is_network(self):
        from dubtool import api

        code, _ = api._classify_error(socket.gaierror(11001, "getaddrinfo failed"))
        assert code == "network"

    def test_wrapped_transport_error_is_still_classified(self):
        from dubtool import api

        try:
            raise LLMError("provider failed") from httpx.ConnectTimeout("t")
        except LLMError as wrapped:
            code, _ = api._classify_error(wrapped)
        assert code == "timeout"

    def test_update6_validation_error_is_internal_not_network(self):
        """The exact reported failure text contains the word 'timeout' — it
        must classify as internal, and the user message must not claim a
        network problem."""
        from dubtool import api
        from dubtool.app import errors as errors_mod
        from dubtool.app import i18n

        text = (
            "an internal error prevented the request (not a connection problem): "
            "1 validation error for HttpOptions"
            "\ntimeout\n  Input should be a valid integer, unable to parse "
            "string as an integer [type=int_parsing, input_value='180000ms', "
            "input_type=str]"
        )

        # In production this exact text arrives as InternalLLMError (the
        # provider mapping types it); assert that path, and that even a
        # plain LLMError carrying the text is never called 'network'.
        code, _ = api._classify_error(InternalLLMError(text))
        assert code == "internal"
        plain, _ = api._classify_error(LLMError(text))
        assert plain != "network"
        assert "network" not in errors_mod.message_for_code(code).lower()
        _ = i18n

    def test_real_pydantic_validation_error_is_internal(self):
        import pydantic

        from dubtool import api

        class Small(pydantic.BaseModel):
            n: int

        try:
            Small(n="not an int")  # type: ignore[arg-type]
        except pydantic.ValidationError as exc:
            mapped = _wrap(exc)  # the production mapping for SDK exceptions
            assert isinstance(mapped, InternalLLMError)
            code, _ = api._classify_error(mapped)
        assert code == "internal"

    def _wrap(self, exc: Exception):
        from dubtool.llm.gemini import GeminiProvider

        provider = GeminiProvider.__new__(GeminiProvider)
        provider.name = "gemini"
        return provider._map_error(exc, "fixture-fake-key")

    def test_rate_limit_error_is_quota_and_key_errors(self):
        from dubtool import api
        from dubtool.llm.base import InvalidKeyError, RateLimitError

        assert api._classify_error(RateLimitError("gemini", "RESOURCE_EXHAUSTED"))[0] == "quota"
        assert api._classify_error(InvalidKeyError("401"))[0] == "invalid_key"

    def test_real_genai_404_is_model_not_found(self):
        from google.genai import errors as genai_errors

        from dubtool import api

        exc = genai_errors.APIError(
            404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "no model"}}
        )
        code, _ = api._classify_error(exc)
        assert code == "model_not_found"

    def test_message_keys_for_new_codes(self):
        from dubtool.app import errors as errors_mod

        assert errors_mod.message_for_code("timeout") == errors_mod.i18n.tr("error.timeout")
        assert errors_mod.message_for_code("internal") == errors_mod.i18n.tr("error.internal")


def _wrap(exc: Exception):
    """Route a raw SDK exception through the production mapping (same
    private entry GeminiProvider.complete uses)."""
    from dubtool.llm.gemini import GeminiProvider

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.name = "gemini"
    return provider._map_error(exc, "fixture-fake-key")
