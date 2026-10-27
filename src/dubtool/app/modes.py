"""Mode definitions (Fast / Accurate) loaded from packaging/modes.yaml,
overridable from settings.json (AGENT_UPDATE_4.md 6.4)."""

from __future__ import annotations

from pathlib import Path

from dubtool.app import paths

MODE_KEYS = ("fast", "accurate")


def _modes_file() -> Path:
    return paths.bundle_root() / "packaging" / "modes.yaml"


def load(settings: dict | None = None, parse=None) -> dict:
    """Return {mode_key: {provider, model, translate_concurrency, label_key}}.

    `settings["mode_models"]` (a dict of mode -> model id) overrides the
    shipped model ids. `parse` is a test seam for the YAML loader.
    """
    from dubtool.config import parse_simple_yaml

    p = _modes_file()
    data: dict = {}
    if p.exists():
        parsed = (parse or parse_simple_yaml)(p)
        data = parsed if isinstance(parsed, dict) else {}
    modes: dict = {}
    for key in MODE_KEYS:
        raw = dict(data.get(key) or {})
        if not raw:
            # shipped defaults mirroring modes.yaml ( resilience if the file
            # is missing from the bundle)
            raw = (
                {"label_key": "mode.fast", "provider": "gemini", "model": "gemini-3.5-flash-lite", "translate_concurrency": 1, "models": ["gemini-3.5-flash-lite", "gemini-3.6-flash"]}
                if key == "fast"
                else {"label_key": "mode.accurate", "provider": "gemini", "model": "gemini-3.8-flash", "translate_concurrency": 3, "models": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-argon"]}
            )
        modes[key] = raw
    overrides = ((settings or {}).get("mode_models") or {})
    for key, model in overrides.items():
        if key in modes and model:
            modes[key]["model"] = str(model)
    return modes


def get(mode: str, settings: dict | None = None) -> dict:
    modes = load(settings)
    if mode not in modes:
        raise KeyError(f"unknown mode: {mode}")
    return modes[mode]


def reset_to_defaults() -> None:
    """Settings -> Advanced 'Reset' restores the shipped model ids."""
    from dubtool.app import settings as settings_mod

    s = settings_mod.load()
    s.pop("mode_models", None)
    settings_mod.save(s)
