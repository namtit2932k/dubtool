"""Gemini API key storage (AGENT_UPDATE_5.md 2.1).

The keys live as an ordered JSON list in ONE Windows Credential Manager
entry (service `DubTool`, account `gemini_api_keys`), so their order — the
fallback priority — is preserved. Windows caps a stored credential, so the
list is capped at 10 keys and the encoded size is checked; exceeding it is
refused with a clear message. The legacy single-key account
(`gemini_api_key`) is migrated into the list on first load and deleted.
Environment fallback (GEMINI_API_KEY, GEMINI_API_KEY_2, …) is read-only:
the app never writes key material to any file or the environment. Values
are never logged; positions ("key #2") only.
"""

from __future__ import annotations

import json
import os

SERVICE = "DubTool"
LIST_ACCOUNT = "gemini_api_keys"
LEGACY_ACCOUNT = "gemini_api_key"

MAX_KEYS = 10
# Windows limits the stored credential size; refuse to exceed this instead
# of relying on the backend failing mid-write.
MAX_ENCODED_BYTES = 2000


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
def _stored_list() -> list[str]:
    raw = _read_entry(LIST_ACCOUNT)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(k) for k in data if isinstance(k, str) and k.strip()]
        except ValueError:
            pass
    return []


def _save_list(keys: list[str]) -> None:
    encoded = json.dumps(keys, ensure_ascii=False).encode("utf-8")
    if len(keys) > MAX_KEYS:
        raise KeyLimitError(
            f"too many keys: {len(keys)} (the limit is {MAX_KEYS})"
        )
    if len(encoded) > MAX_ENCODED_BYTES:
        raise KeyLimitError(
            f"stored keys too large ({len(encoded)} bytes; limit {MAX_ENCODED_BYTES})"
        )
    _write_entry(LIST_ACCOUNT, encoded.decode("utf-8"))


def _migrate_legacy() -> None:
    """Old layouts: single account gemini_api_key, and (an interim build's)
    three-slot gemini_api_key_2/_3. Everything moves into the ordered list
    (the single legacy key first — it was the primary), then the old entries
    are deleted. A migration only writes when there is something to move."""
    extra_values: list[str] = []

    legacy = _read_entry(LEGACY_ACCOUNT)
    if legacy:
        value = clean_key(legacy)
        if value and not looks_like_placeholder(value):
            extra_values.insert(0, value)  # the legacy key was the primary
        _delete_entry(LEGACY_ACCOUNT)
    for suffix in ("_2", "_3"):  # interim three-slot build (pre-list storage)
        extra = _read_entry(LEGACY_ACCOUNT + suffix)
        if extra:
            value = clean_key(extra)
            if value and not looks_like_placeholder(value) and value not in extra_values:
                extra_values.append(value)
            _delete_entry(LEGACY_ACCOUNT + suffix)

    if not extra_values:
        return
    stored = _stored_list()
    for value in extra_values:
        if value not in stored:
            stored.append(value)
    try:
        _save_list(stored)
    except KeyLimitError:
        # never lose migrated keys silently; store what fits
        _save_list(stored[:MAX_KEYS])


def clean_key(value: str) -> str:
    """Strip whitespace and surrounding quotes; the caller decides emptiness."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    return value


_PLACEHOLDER_PHRASES = (
    "placeholder", "cua toi", "your key", "your_key", "changeme", "xxx", "todo",
)


def looks_like_placeholder(value: str) -> bool:
    """Existing placeholder check (AGENT_UPDATE.md 4.1), module-local copy."""
    if not value:
        return True
    if any(ch.isspace() for ch in value):
        return True
    lowered = value.lower()
    return any(p in lowered for p in _PLACEHOLDER_PHRASES)


# -------------------------------------------------------- environment
def _env_names():
    yield "GEMINI_API_KEY"
    i = 2
    while i <= MAX_KEYS:
        yield f"GEMINI_API_KEY_{i}"
        i += 1


def _env_value(name: str) -> str | None:
    value = clean_key(os.environ.get(name, ""))
    return value or None


def env_keys() -> list[str]:
    """Fallback keys from the environment/.env, in order. Read-only."""
    keys: list[str] = []
    for name in _env_names():
        value = _env_value(name)
        if value and not looks_like_placeholder(value) and value not in keys:
            keys.append(value)
    return keys


# ------------------------------------------------------------- public API
def get_gemini_keys() -> list[str]:
    """All usable keys in priority order: the stored list first; the env list
    is appended when the store is empty or has no usable backend."""
    _migrate_legacy()
    stored = _stored_list()
    keys = [k for k in stored if not looks_like_placeholder(k)]
    if not keys:
        keys = env_keys()
    return keys


def get_gemini_key() -> str | None:
    """First key (compatibility; single-key call sites)."""
    keys = get_gemini_keys()
    return keys[0] if keys else None


def has_key() -> bool:
    return bool(get_gemini_keys())


def key_source() -> str:
    """'keyring' | 'env' | 'none' — honest messaging, never the key."""
    _migrate_legacy()
    if _stored_list():
        return "keyring"
    if env_keys():
        return "env"
    return "none"


def keyring_unavailable() -> bool:
    """True when no usable Credential Manager backend exists."""
    try:
        import keyring

        keyring.get_password(SERVICE, LIST_ACCOUNT)
        return False
    except Exception:
        return True


def add_keys(new_keys: list[str], index: int | None = None) -> list[str]:
    """Insert cleaned, de-duplicated keys at `index` (end by default).
    Returns the final list so callers can show counts. Raises KeyError-like
    KeyLimitError on the cap/size; placeholders are rejected."""
    _migrate_legacy()
    stored = _stored_list()
    cleaned: list[str] = []
    for value in new_keys:
        key = clean_key(value or "")
        if not key:
            continue
        if looks_like_placeholder(key):
            raise ValueError("that looks like a placeholder, not an API key")
        if key in cleaned or key in stored:
            continue  # duplicates keep order, first occurrence wins
        cleaned.append(key)
    if not cleaned:
        return get_gemini_keys()
    position = len(stored) if index is None else max(0, min(index, len(stored)))
    merged = stored[:position] + cleaned + stored[position:]
    if len(merged) > MAX_KEYS:
        raise KeyLimitError(
            f"too many keys: {len(merged)} (the limit is {MAX_KEYS})"
        )
    _save_list(merged)
    return get_gemini_keys()


def remove_key(index: int) -> list[str]:
    """Remove the 0-based key; raises IndexError when absent."""
    _migrate_legacy()
    stored = _stored_list()
    if not 0 <= index < len(stored):
        raise IndexError(f"no key at position {index + 1}")
    stored.pop(index)
    _save_list(stored)
    return get_gemini_keys()


def move_key(index: int, delta: int) -> list[str]:
    """Reorder the 0-based key by `delta` (negative = up); order is priority."""
    _migrate_legacy()
    stored = _stored_list()
    target = index + delta
    if not 0 <= index < len(stored) or not 0 <= target < len(stored):
        return get_gemini_keys()
    stored.insert(target, stored.pop(index))
    _save_list(stored)
    return get_gemini_keys()


# ------------------------------------------------- legacy single-key API
def save_key(key: str, index: int = 1) -> None:
    """Compatibility for the old single-field UI/tests: sets key `index`
    (1-based) in the ordered list."""
    _migrate_legacy()
    value = clean_key(key)
    if not value:
        raise ValueError("key is empty")
    if looks_like_placeholder(value):
        raise ValueError("that looks like a placeholder, not an API key")
    stored = _stored_list()
    while len(stored) < index:
        stored.append("")
    stored[index - 1] = value
    _save_list(stored)


def delete_key(index: int | None = None) -> None:
    """Delete one stored key (1-based); None deletes every stored key
    (the env fallback, if any, keeps working afterwards)."""
    _migrate_legacy()
    if index is None:
        _delete_entry(LIST_ACCOUNT)
        _delete_entry(LEGACY_ACCOUNT)
        return
    stored = _stored_list()
    pos = index - 1
    if 0 <= pos < len(stored):
        stored.pop(pos)
        _save_list(stored)
