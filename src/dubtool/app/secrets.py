"""Per-provider API key storage (multi-provider, Windows Credential Manager).

Each provider keeps its keys as an ordered JSON list in ONE Credential
Manager entry (service `DubTool`): `gemini_api_keys`, `openai_api_keys`,
`anthropic_api_keys` — so the order (the fallback priority) is preserved.
Windows caps a stored credential, so lists are capped at 10 keys with an
encoded-size check (exceeding is refused with a clear message, mixed). The
legacy single-key account (`gemini_api_key`) is migrated into the list on
first load and deleted.

Environment fallback is read-only and never written into a file,
settings.json, logs, or the process environment; values are never logged —
positions ("key #2") only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVICE = "DubTool"

ACCOUNTS = {
    "gemini": "gemini_api_keys",
    "openai": "openai_api_keys",
    "anthropic": "anthropic_api_keys",
}
LEGACY_ACCOUNT = "gemini_api_key"

MAX_KEYS = 10
MAX_ENCODED_BYTES = 2000

# The read-only environment names per provider, each also available with
# _2, _3, … suffixes. GPT_API_KEY is the user-chosen name for OpenAI.
ENV_PREFIXES = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("OPENAI_API_KEY", "GPT_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
}


def normalize_provider(provider: str | None) -> str:
    pid = (provider or "gemini").lower()
    if pid == "claude":
        pid = "anthropic"
    elif pid == "gpt":
        pid = "openai"
    return pid


def account_for(provider: str | None) -> str:
    pid = normalize_provider(provider)
    return ACCOUNTS.get(pid, f"{pid}_api_keys")


class KeyLimitError(ValueError):
    """Adding these keys would exceed the store's cap or size limit."""


def _backend():
    import keyring

    return keyring


def _read_entry(account: str) -> str | None:
    try:
        keyring = _backend()
        value = keyring.get_password(SERVICE, account)
    except Exception:
        return None
    if isinstance(value, str) and value.strip():
        return value
    return None


def _write_entry(account: str, value: str) -> None:
    keyring = _backend()
    keyring.set_password(SERVICE, account, value)


def _delete_entry(account: str) -> None:
    try:
        keyring = _backend()
        keyring.delete_password(SERVICE, account)
    except Exception:
        pass  # absent entry == already deleted


def backend_name(fail_silently: bool = True) -> str | None:
    try:
        keyring = _backend()
        backend = getattr(keyring, "get_keyring", lambda: None)()
        name = type(backend).__module__ + "." + type(backend).__name__
        return name
    except Exception:
        if fail_silently:
            return None
        raise


# ------------------------------------------------------- stored list I/O
def _stored_list(provider: str) -> list[str]:
    raw = _read_entry(account_for(provider))
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(k) for k in data if isinstance(k, str) and k.strip()]
        except ValueError:
            pass
    return []


def _save_list(provider: str, keys: list[str]) -> None:
    encoded = json.dumps(keys, ensure_ascii=False).encode("utf-8")
    if len(keys) > MAX_KEYS:
        raise KeyLimitError(
            f"too many keys: {len(keys)} (the limit is {MAX_KEYS})"
        )
    if len(encoded) > MAX_ENCODED_BYTES:
        raise KeyLimitError(
            f"stored keys too large ({len(encoded)} bytes; limit {MAX_ENCODED_BYTES})"
        )
    _write_entry(account_for(provider), encoded.decode("utf-8"))


def _migrate_legacy(provider: str = "gemini") -> None:
    """The old Gemini-only layouts (single key, interim three-slot)."""
    extra_values: list[str] = []

    legacy = _read_entry(LEGACY_ACCOUNT)
    if legacy:
        value = clean_key(legacy)
        if value and not looks_like_placeholder(value):
            extra_values.insert(0, value)  # the legacy key was the primary
        _delete_entry(LEGACY_ACCOUNT)
    for suffix in ("_2", "_3"):
        extra = _read_entry(LEGACY_ACCOUNT + suffix)
        if extra:
            value = clean_key(extra)
            if value and not looks_like_placeholder(value) and value not in extra_values:
                extra_values.append(value)
            _delete_entry(LEGACY_ACCOUNT + suffix)

    if not extra_values or normalize_provider(provider) != "gemini":
        return
    stored = _stored_list("gemini")
    for value in extra_values:
        if value not in stored:
            stored.append(value)
    try:
        _save_list("gemini", stored)
    except KeyLimitError:
        # never lose migrated keys silently; store what fits
        _save_list("gemini", stored[:MAX_KEYS])


def clean_key(value: str) -> str:
    """Strip whitespace and surrounding quotes; the caller decides emptiness."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1].strip()
    return value


_PLACEHOLDER_PHRASES = (
    "placeholder", "cua toi", "your key", "your_key", "changeme", "xxx", "todo",
)


def looks_like_placeholder(value: str) -> bool:
    if not value:
        return True
    if any(ch.isspace() for ch in value):
        return True
    lowered = value.lower()
    return any(p in lowered for p in _PLACEHOLDER_PHRASES)


# -------------------------------------------------------- environment
def env_names(provider: str) -> list[str]:
    """Every name read for this provider, in order: base names, then _2…."""
    pid = normalize_provider(provider)
    names: list[str] = []
    for base in ENV_PREFIXES.get(pid, ()):
        names.append(base)
        i = 2
        while i <= MAX_KEYS:
            names.append(f"{base}_{i}")
            i += 1
    return names


def load_env_if_source() -> None:
    if getattr(sys, "frozen", False):
        return
    if "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("DUBTOOL_TEST"):
        return
    if getattr(load_env_if_source, "_loaded", False):
        return
    setattr(load_env_if_source, "_loaded", True)
    try:
        from dubtool.config import load_env

        cwd = Path.cwd()
        for p in [cwd, *cwd.parents[:3]]:
            env_file = p / ".env"
            if env_file.is_file():
                load_env(p)
                break
    except Exception:
        pass


def _load_env_if_source() -> None:
    load_env_if_source()


def _env_value(name: str) -> str | None:
    _load_env_if_source()
    value = clean_key(os.environ.get(name, ""))
    return value or None


def env_keys(provider: str) -> list[str]:
    """Read-only fallback keys from the environment/.env, in order."""
    pid = normalize_provider(provider)
    keys: list[str] = []
    for name in env_names(pid):
        value = _env_value(name)
        if value and not looks_like_placeholder(value) and value not in keys:
            keys.append(value)
    return keys


def env_key_sources(provider: str) -> list[tuple[str, str]]:
    """(variable name, masked value) pairs for the read-only '.env' rows."""
    pid = normalize_provider(provider)
    out: list[tuple[str, str]] = []
    for name in ENV_PREFIXES.get(pid, ()):
        value = _env_value(name)
        if not value or looks_like_placeholder(value):
            continue
        out.append((name, mask(value)))
        i = 2
        while i <= MAX_KEYS:
            value_n = _env_value(f"{name}_{i}")
            if value_n and not looks_like_placeholder(value_n):
                out.append((f"{name}_{i}", mask(value_n)))
            i += 1
    return out


def mask(value: str) -> str:
    """Position plus the last 4 characters — never the whole key."""
    value = clean_key(value)
    if len(value) <= 4:
        return "••••" + value if value else ""
    return "••••" + value[-4:]


def detect_provider(key: str) -> str | None:
    """Provider guess by prefix: anthropic keys start `sk-ant-` (checked
    before the openai `sk-` shapes), Gemini keys start `AIza` or `AQ.`."""
    k = clean_key(key)
    if k.startswith("sk-ant-"):
        return "anthropic"
    if k.startswith(("AIza", "AQ.")):
        return "gemini"
    if k.startswith("sk-"):
        return "openai"
    return None


# ------------------------------------------------------------- public API
def get_keys(provider: str = "gemini") -> list[str]:
    """All usable keys in priority order: the stored list first; the env
    list is appended when the store is empty or has no usable backend."""
    pid = normalize_provider(provider)
    if pid == "gemini":
        sec_mod = sys.modules.get("dubtool.app.secrets")
        if sec_mod is not None:
            fn = getattr(sec_mod, "get_gemini_keys", None)
            if callable(fn) and fn is not get_gemini_keys:
                return fn()
        _migrate_legacy("gemini")
    stored = _stored_list(pid)
    keys = [k for k in stored if not looks_like_placeholder(k)]
    if not keys:
        keys = env_keys(pid)
    return keys


def has_key(provider: str = "gemini") -> bool:
    return bool(get_keys(provider))


def has_any_key() -> bool:
    return any(has_key(pid) for pid in ("gemini", "openai", "anthropic"))


def key_source(provider: str = "gemini") -> str:
    """'keyring' | 'env' | 'none' — honest status, never the key."""
    pid = normalize_provider(provider)
    if pid == "gemini":
        _migrate_legacy("gemini")
    if _stored_list(pid):
        return "keyring"
    if env_keys(pid):
        return "env"
    return "none"


def keyring_unavailable(provider: str = "gemini") -> bool:
    try:
        import keyring

        keyring.get_password(SERVICE, account_for(provider))
        return False
    except Exception:
        return True


def add_keys(provider: str | list | tuple, new_keys=None, index: int | None = None) -> list[str]:
    """Insert cleaned, de-duplicated keys at `index` (end by default);
    placeholders are rejected; the final list is returned."""
    if isinstance(provider, (list, tuple, set)):
        index = new_keys if isinstance(new_keys, int) else None
        new_keys = provider
        provider = "gemini"
    elif new_keys is None:
        new_keys = []
    pid = normalize_provider(str(provider))
    if pid == "gemini":
        _migrate_legacy("gemini")
    if isinstance(new_keys, str):
        new_keys = [new_keys]
    stored = _stored_list(pid)
    cleaned: list[str] = []
    for value in new_keys:
        key = clean_key(value or "")
        if not key:
            continue
        if looks_like_placeholder(key):
            raise ValueError("that looks like a placeholder, not an API key")
        if key in cleaned or key in stored:
            continue
        cleaned.append(key)
    if not cleaned:
        return get_keys(pid)
    position = len(stored) if index is None else max(0, min(index, len(stored)))
    merged = stored[:position] + cleaned + stored[position:]
    try:
        _save_list(pid, merged)
    except KeyLimitError:
        raise
    return get_keys(pid)


def remove_key(provider: str | int, index: int | None = None) -> list[str]:
    if isinstance(provider, int) and index is None:
        index = provider
        provider = "gemini"
    pid = normalize_provider(str(provider))
    if pid == "gemini":
        _migrate_legacy("gemini")
    stored = _stored_list(pid)
    if not 0 <= index < len(stored):
        raise IndexError(f"no {pid} key at position {index + 1}")
    stored.pop(index)
    _save_list(pid, stored)
    return get_keys(pid)


def move_key(provider: str | int, index: int, delta: int | None = None) -> list[str]:
    """Reorder the 0-based key by `delta` (negative = up); order = priority."""
    if delta is None:
        delta = index
        index = int(provider)
        provider = "gemini"
    pid = normalize_provider(str(provider))
    if pid == "gemini":
        _migrate_legacy("gemini")
    stored = _stored_list(pid)
    target = index + delta
    if not 0 <= index < len(stored) or not 0 <= target < len(stored):
        return get_keys(pid)
    stored.insert(target, stored.pop(index))
    _save_list(pid, stored)
    return get_keys(pid)


# ------------------------------------------------- compatibility wrappers
def get_gemini_keys() -> list[str]:
    return get_keys("gemini")


def get_gemini_key() -> str | None:
    keys = get_gemini_keys()
    return keys[0] if keys else None


def env_key_sources_gemini() -> list[tuple[str, str]]:
    return env_key_sources("gemini")


def save_key(key: str, index: int = 1) -> None:
    """Compatibility for the old single-field UI/tests: sets Gemini key
    `index` (1-based) in the ordered list."""
    _migrate_legacy("gemini")
    value = clean_key(key)
    if not value:
        raise ValueError("key is empty")
    if looks_like_placeholder(value):
        raise ValueError("that looks like a placeholder, not an API key")
    stored = _stored_list("gemini")
    while len(stored) < index:
        stored.append("")
    stored[index - 1] = value
    _save_list("gemini", stored)


def delete_key(index: int | None = None) -> None:
    """Delete stored Gemini key(s); None deletes the whole entry."""
    _migrate_legacy("gemini")
    if index is None:
        _delete_entry(account_for("gemini"))
        _delete_entry(LEGACY_ACCOUNT)
        return
    stored = _stored_list("gemini")
    pos = index - 1
    if 0 <= pos < len(stored):
        stored.pop(pos)
        _save_list("gemini", stored)
