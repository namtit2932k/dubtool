"""Anthropic provider (paid). Behavior carried over unchanged from the
original llm.py complete(); wrapper handles retries/validation."""

from __future__ import annotations

from dubtool.llm.base import LLMError, LLMResult, MissingKeyError, RateLimitError, looks_like_placeholder, read_env_key


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None):
        self.model = model
        self.api_key = api_key if api_key is not None else read_env_key("ANTHROPIC_API_KEY")
        if looks_like_placeholder(self.api_key):
            raise MissingKeyError(
                "ANTHROPIC_API_KEY is not set — add it to .env (see .env.example) "
                "or your shell environment"
            )
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover
            raise MissingKeyError(
                "anthropic SDK not installed — run: pip install anthropic"
            ) from exc
        self.client = Anthropic(timeout=120.0)

    def complete(
        self, system: str, user: str, *, json_mode: bool = True, max_tokens: int = 8000
    ) -> LLMResult:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=min(max_tokens, 8000),
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            if code == 429 or "rate" in str(exc).lower():
                raise RateLimitError(self.name, str(exc)) from exc
            raise LLMError(f"anthropic API error: {exc}") from exc

        usage = getattr(response, "usage", None)
        parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        text = "\n".join(parts)
        if not text.strip():
            raise LLMError(f"anthropic returned no text (model={self.model})")
        return LLMResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )
