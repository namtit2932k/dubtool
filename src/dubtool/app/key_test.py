"""Minimal key check (AGENT_UPDATE_5.md 2.3 'Kiểm tra khóa').

One tiny request per key with a short timeout; the dialog runs it off the
UI thread. Outcomes: `ok`, `invalid`, `network`, `quota`, `invalid_model`,
`unknown`. `invalid` is decided from the API's error status or reason
(401/403/UNAUTHENTICATED/PERMISSION_DENIED or an API-key-specific 400) —
never from "any HTTP 400". The key itself is never part of the result or
of any message.
"""

from __future__ import annotations

REQUEST_TIMEOUT_MS = 10_000  # short so Test all keys stays responsive


def _genai_module():
    from google import genai
    from google.genai import errors as genai_errors

    return genai, genai_errors


def _is_api_error(exc: Exception) -> bool:
    try:
        from google.genai import errors as genai_errors

        return isinstance(exc, genai_errors.APIError)
    except ImportError:
        return False


def _is_network(exc: Exception) -> bool:
    """Transport failures (no/\"bad network\"), not API-level errors."""
    name = type(exc).__name__.lower()
    text = f"{type(exc).__module__}.{type(exc).__name__}: {exc}".lower()
    marker_words = (
        "connectionerror", "connecttimeout", "readtimeout", "timeout",
        "connection", "proxyerror", "ssl", "getaddrinfo", "nameresolution",
        "network", "temporarily unavailable",
    )
    return any(w in name for w in marker_words[:4]) or any(w in text for w in marker_words)


def classify_request_error(exc: Exception) -> str:
    """Map one failed request to an outcome keyword (AGU5 2.3)."""
    status = str(getattr(exc, "status", "") or "").upper()
    code = getattr(exc, "code", None)
    message = str(getattr(exc, "message", "") or exc)
    details = str(getattr(exc, "details", "") or "")
    text = f"{message}\n{details}".lower()
    api_error = _is_api_error(exc)

    if code == 429 or "RESOURCE_EXHAUSTED" in status or "resource_exhausted" in text or "quota" in text:
        return "quota"
    if code in (401, 403) or any(s in status for s in ("UNAUTHENTICATED", "PERMISSION_DENIED")):
        return "invalid"
    if code == 404 or "NOT_FOUND" in status:
        return "invalid_model"
    if code == 400:
        # An invalid key 400 names the API key explicitly; a wrong-model 400
        # mentions the model. Anything else is not an "invalid key" verdict
        # (AGU5 2.3: never "any HTTP 400" -> invalid).
        if "api key" in text or "api_key" in text or "api-key" in text:
            return "invalid"
        if "model" in text:
            return "invalid_model"
        return "unknown"
    if api_error and ("api key" in text or "api_key" in text):
        return "invalid"
    if not api_error:
        if _is_network(exc):
            return "network"
        # e.g. google.genai.ClientConfigurationError for a malformed key
        if "api key" in text or "api_key" in text:
            return "invalid"
        if "timeout" in text or "connection" in text:
            return "network"
    return "unknown"


def _request_ok(key: str, model: str) -> None:
    """Raise on any failure; return quietly when the tiny request succeeds."""
    genai, types = _imports()
    client = genai.Client(
        api_key=key,
        http_options={"timeout": REQUEST_TIMEOUT_MS},
    )
    cfg = types.GenerateContentConfig(max_output_tokens=16, temperature=0.0)
    client.models.generate_content(
        model=model,
        contents=["Reply with the single word OK."],
        config=cfg,
    )


def _imports():
    from google import genai
    from google.genai import types as genai_types

    return genai, genai_types


def run_key_test(key: str, model: str | None = None) -> str:
    """Test one key with one tiny request. The model defaults to the shipped
    Fast model; the Settings dialog passes the user-selected Fast model."""
    if not key or not key.strip():
        return "invalid"
    try:
        _request_ok(key.strip(), model or "gemini-3.5-flash-lite")
        return "ok"
    except Exception as exc:
        return classify_request_error(exc)
