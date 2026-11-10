"""Model catalog loader (packaging/models.yaml).

One entry per model: provider, id, label, paid, structured, reasoning,
and per-model defaults (translate concurrency, request timeout, chunk
size, output budget). The shipped file is the baseline; entries are
keyed by the model id so a user can extend the catalog by adding an
entry with the same keys. No price is ever stored here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("dubtool")

PROVIDERS = ("gemini", "openai", "anthropic", "ollama")
PAID_PROVIDERS = ("openai", "anthropic")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: str
    label: str = ""
    paid: bool = False
    structured: str = "none"      # json_schema | json_object | none
    reasoning: bool = False
    reasoning_effort: str = "low"
    translate_concurrency: int = 2
    request_timeout_s: float = 180.0
    chunk_max_chars: int = 1200
    max_output_tokens: int = 8000
    verified: str = ""

    @property
    def display_label(self) -> str:
        return self.label or self.id


def catalog_path(root: Path | None = None) -> Path:
    from dubtool.app.paths import bundle_root

    base = Path(root) if root is not None else bundle_root()
    return base / "packaging" / "models.yaml"


def _shipped() -> dict[str, dict]:
    """Baseline entries that must exist even when the file is missing
    from a bundle (resilience, mirroring the shipped defaults)."""
    return {
        "gemini-3.5-flash-lite": dict(
            provider="gemini", label="Gemini 3.5 Flash-Lite", paid=False,
            structured="json_schema", reasoning=False,
            translate_concurrency=3, request_timeout_s=90.0,
            chunk_max_chars=1200, max_output_tokens=8000,
            verified="shipped default",
        ),
        "gemini-3.8-flash": dict(
            provider="gemini", label="Gemini 3.8 Flash", paid=False,
            structured="json_schema", reasoning=False,
            translate_concurrency=2, request_timeout_s=180.0,
            chunk_max_chars=1200, max_output_tokens=8000,
            verified="shipped default",
        ),
    }


def load(path: Path | None = None) -> dict[str, ModelInfo]:
    """Model id -> ModelInfo, in file order (the file is also the
    dropdown order). Unknown/invalid entries are skipped with a warning."""
    p = Path(path) if path is not None else catalog_path()
    data: dict = {}
    if p.exists():
        try:
            from dubtool.config import parse_simple_yaml

            parsed = parse_simple_yaml(p)
            if isinstance(parsed, dict):
                data = parsed
        except Exception as exc:
            log.warning("model catalog could not be read (%s): %s", p, exc)
    catalog: dict[str, ModelInfo] = {}
    for model_id, raw in list(data.items()):
        if not isinstance(raw, dict):
            data.pop(model_id, None)
            continue
        info = _build(str(model_id), raw)
        if info is not None:
            catalog[info.id] = info
    for model_id, raw in _shipped().items():
        if model_id not in catalog:
            catalog[model_id] = _build(model_id, raw)
    return catalog


def _build(model_id: str, raw: dict) -> ModelInfo | None:
    provider = str(raw.get("provider") or "").lower()
    if provider not in PROVIDERS:
        provider = infer_provider(model_id)
    if provider not in PROVIDERS:
        return None

    def _num(key, default, cast):
        try:
            return cast(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    try:
        return ModelInfo(
            id=model_id,
            provider=provider,
            label=str(raw.get("label") or ""),
            paid=bool(raw.get("paid", False)),
            structured=str(raw.get("structured") or "none").lower(),
            reasoning=bool(raw.get("reasoning", False)),
            reasoning_effort=str(raw.get("reasoning_effort") or "low"),
            translate_concurrency=_num("translate_concurrency", 2, int),
            request_timeout_s=float(_num("request_timeout_s", 180.0, float)),
            chunk_max_chars=_num("chunk_max_chars", 1200, int),
            max_output_tokens=_num("max_output_tokens", 8000, int),
            verified=str(raw.get("verified") or ""),
        )
    except Exception as exc:  # a malformed entry never breaks the app
        log.warning("model catalog entry %s ignored (%s)", model_id, exc)
        return None


_DEFAULT_PROVIDER_BY_PREFIX = (
    ("claude", "anthropic"),
    ("gpt-", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini", "gemini"),
)


def infer_provider(model_id: str | None) -> str:
    """Provider from the model id's prefix (--model without a catalog
    entry); an unknown id falls back to gemini (the app's default)."""
    for prefix, provider in _DEFAULT_PROVIDER_BY_PREFIX:
        if model_id and str(model_id).lower().startswith(prefix):
            return provider
    return "gemini"


def get(model_id: str, path: Path | None = None) -> ModelInfo | None:
    return load(path).get(model_id)


def require(model_id: str, path: Path | None = None) -> ModelInfo:
    info = get(model_id, path)
    if info is None:
        # An unknown id still drives a run: infer the provider and keep
        # safe defaults (never a hard failure for a user-added model).
        provider = infer_provider(model_id)
        return ModelInfo(
            id=model_id or "gemini-3.5-flash-lite",
            provider=provider,
            paid=provider in PAID_PROVIDERS,
            structured="json_schema" if provider in ("openai", "anthropic") else "json_object",
        )
    return info


def entries_by_provider(path: Path | None = None) -> dict[str, list[ModelInfo]]:
    """Catalog entries grouped by provider in the canonical order
    (gemini, openai, anthropic, ollama) — the dropdown's grouping."""
    out: dict[str, list[ModelInfo]] = {pid: [] for pid in PROVIDERS}
    for info in load(path).values():
        out.setdefault(info.provider, []).append(info)
    return out


def grouped_order(path: Path | None = None) -> list[tuple[str, list[ModelInfo]]]:
    groups = entries_by_provider(path)
    return [(pid, groups.get(pid, [])) for pid in ("gemini", "openai", "anthropic", "ollama")]


DEFAULT_MODEL_ID = "gemini-3.5-flash-lite"
