"""Ollama provider: local HTTP API at POST {host}/api/chat (no extra deps).

Private and free; Vietnamese quality varies strongly by model.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from dubtool.llm.base import LLMError, LLMResult

NOT_RUNNING_MESSAGE = (
    "Ollama is not running. Start it and pull the model: `ollama pull <model>`."
)


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str, host: str = "http://localhost:11434", timeout_sec: int = 300):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout_sec = timeout_sec

    def complete(
        self, system: str, user: str, *, json_mode: bool = True, max_tokens: int = 8000
    ) -> LLMResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.3},
        }
        if json_mode:
            payload["format"] = "json"
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise LLMError(NOT_RUNNING_MESSAGE) if self._looks_down(exc) else LLMError(
                f"ollama request failed: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"ollama returned non-JSON output: {exc}") from exc

        message = body.get("message") or {}
        text = (message.get("content") or body.get("response") or "").strip()
        if not text:
            error = body.get("error")
            raise LLMError(
                f"ollama returned no text (model={self.model})"
                + (f" — error: {error}" if error else "")
            )
        return LLMResult(
            text=text,
            input_tokens=body.get("prompt_eval_count"),
            output_tokens=body.get("eval_count"),
        )

    @staticmethod
    def _looks_down(exc: Exception) -> bool:
        text = str(exc).lower()
        return "refused" in text or "connection" in text or "10061" in text
