"""Gemini provider via the official google-genai SDK (default, free tier).

Uses GEMINI_API_KEY. JSON output is requested via the SDK response option;
the shared parser still validates everything.
"""

from __future__ import annotations

import os

from dubtool.llm.base import LLMError, LLMResult, MissingKeyError, RateLimitError, looks_like_placeholder, read_env_key


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        if api_key is None:
            # Optional extra keys let the user spread the free-tier daily
            # quota: the client rotates on rate limits (GEMINI_API_KEY_2 …).
            self.keys = [k for k in self._keys_from_env() if not looks_like_placeholder(k)]
            if not self.keys:
                raise MissingKeyError(
                    "GEMINI_API_KEY in .env is empty or still a placeholder. "
                    "Get a key at Google AI Studio (https://aistudio.google.com/apikey) "
                    "and put it in .env as GEMINI_API_KEY = \"...\""
                )
        else:
            self.keys = [api_key]
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise MissingKeyError(
                "google-genai SDK not installed — run: pip install google-genai"
            ) from exc
        self._genai = genai
        self._key_index = 0
        self.client = genai.Client(api_key=self.keys[0])

    @staticmethod
    def _keys_from_env() -> list[str]:
        keys = [read_env_key("GEMINI_API_KEY")]
        i = 2
        while True:
            value = read_env_key(f"GEMINI_API_KEY_{i}")
            if not value:
                break
            keys.append(value)
            i += 1
        return keys

    @property
    def api_key(self) -> str:
        """Current key — callers only ever see the object, not the value."""
        return self.keys[self._key_index]

    def rotate_key(self) -> bool:
        """Switch to the next key. True if another key is available."""
        if self._key_index + 1 >= len(self.keys):
            return False
        self._key_index += 1
        self.client = self._genai.Client(api_key=self.keys[self._key_index])
        return True

    @property
    def _types(self):
        from google.genai import types

        return types

    def complete(
        self, system: str, user: str, *, json_mode: bool = True, max_tokens: int = 8000,
        thinking_level: str | None = None,
    ) -> LLMResult:
        """thinking_level, when given, is passed as the official thinking
        control (ai.google.dev); None keeps the model default."""
        types = self._types
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.3,
            max_output_tokens=max_tokens,
        )
        if thinking_level:
            config.thinking_config = types.ThinkingConfig(thinking_level=thinking_level)
        if json_mode:
            config.response_mime_type = "application/json"
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[user],
                config=config,
            )
        except Exception as exc:
            raise self._map_error(exc) from exc

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage else None
        thinking_tokens = getattr(usage, "thoughts_token_count", None) if usage else None

        text = None
        try:
            text = response.text
        except (ValueError, AttributeError):
            text = None
        if text is None or not text:
            feedback = getattr(response, "prompt_feedback", None) or getattr(
                response, "candidates", None
            )
            raise LLMError(
                f"gemini returned no text (model={self.model}, usage={usage}); "
                f"response signals: {feedback}"
            )
        return LLMResult(
            text=text,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            thinking_tokens=thinking_tokens if isinstance(thinking_tokens, int) else None,
        )

    def _map_error(self, exc: Exception) -> Exception:
        code = getattr(exc, "code", None)
        if code == 429:
            return RateLimitError(self.name, str(exc))
        text = str(exc)
        if "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
            return RateLimitError(self.name, text)
        if code in (401, 403):
            return LLMError(
                "gemini rejected the API key (401/403). Check GEMINI_API_KEY in .env "
                "at Google AI Studio"
            )
        return LLMError(f"gemini API error: {text}")
