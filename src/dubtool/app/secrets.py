"""Gemini API key storage.

The key lives only in Windows Credential Manager via `keyring`
(service name "DubTool"). If no usable keyring backend exists, we fall back
to reading the environment variable GEMINI_API_KEY (read-only). The key is
NEVER written to a file; settings.json stays secret-free (AGU4 8).
"""

from __future__ import annotations

import os

SERVICE = "DubTool"
ACCOUNT = "gemini_api_key"


def backend_name(fail_silently: bool = True) -> str | None:
    try:
        import keyring

        kr = keyring.get_keyring()
        name = getattr(kr, "priority", None)
        module = getattr(kr, "__module__", "") or str(kr)
        # a null/fail backend is unusable
        lowered = module.lower()
        if "fail" in lowered or "null" in lowered:
            return None
        return module if name is None else module
    except Exception:
        return None


def get_gemini_key() -> str | None:
    """Key from the keyring; if unavailable, the env var (read-only)."""
    key = None
    try:
        import keyring

        key = keyring.get_password(SERVICE, ACCOUNT)
    except Exception:
        key = None
    if isinstance(key, str) and key.strip():
        return key.strip()
    return _env_key()


def _env_key() -> str | None:
    value = os.environ.get("GEMINI_API_KEY")
    if value:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].strip()
        return value or None
    return None


def has_key() -> bool:
    return bool(get_gemini_key())


def key_source() -> str:
    """'keyring' or 'env' — used for honest messages (never the key)."""
    try:
        import keyring

        value = keyring.get_password(SERVICE, ACCOUNT)
        if isinstance(value, str) and value.strip():
            return "keyring"
    except Exception:
        pass
    if os.environ.get("GEMINI_API_KEY"):
        return "env"
    return "none"


def save_key(key: str) -> None:
    if not key or not key.strip():
        raise ValueError("key is empty")
    import keyring

    keyring.set_password(SERVICE, ACCOUNT, key.strip())


def delete_key() -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE, ACCOUNT)
    except Exception:
        pass  # absent key == already deleted
