"""Minimal paid-free key test (AGU4 8 'Test key').

One tiny request to the selected provider; maps the outcome to
ok | invalid | network | quota | unknown. The key itself is never in the
result.
"""

from __future__ import annotations


def run_key_test(key: str, model: str | None = None) -> str:
    try:
        from google import genai
        from google.genai import types
    except Exception:
        return "unknown"
    try:
        client = genai.Client(api_key=key)
        cfg = types.GenerateContentConfig(max_output_tokens=16, temperature=0.0)
        client.models.generate_content(
            model=model or "gemini-3.5-flash-lite",
            contents=["Reply with the single word OK."],
            config=cfg,
        )
        return "ok"
    except Exception as exc:
        text = str(exc)
        code = getattr(exc, "code", None)
        if code == 429 or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
            return "quota"
        if code in (400, 401, 403) or "API key" in text or "API_KEY" in text:
            return "invalid"
        if code is None and ("network" in text.lower() or "timeout" in text.lower() or "connection" in text.lower()):
            return "network"
        if code == 404 or "not found" in text.lower():
            return "invalid_model"
        return "unknown"
