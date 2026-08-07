from __future__ import annotations

import os
from dataclasses import dataclass

KEYRING_SERVICE = "civic_metrics"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def _keyring_module() -> object | None:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:
        return None
    return keyring


def get_secret(name: str, explicit_value: str | None = None) -> str | None:
    """Resolve a secret from explicit settings, environment, then the OS keyring."""
    if explicit_value:
        return explicit_value
    env_name = f"CIVIC_METRICS_{name.upper()}"
    if value := os.getenv(env_name):
        return value
    keyring = _keyring_module()
    if keyring is None:
        return None
    return keyring.get_password(KEYRING_SERVICE, name)  # type: ignore[attr-defined,no-any-return]


def set_secret(name: str, value: str) -> None:
    keyring = _keyring_module()
    if keyring is None:
        raise RuntimeError("The optional 'keyring' package is not installed")
    keyring.set_password(KEYRING_SERVICE, name, value)  # type: ignore[attr-defined]


def get_datacomex_credentials(
    username: str | None = None,
    password: str | None = None,
) -> Credentials | None:
    resolved_username = get_secret("datacomex_username", username)
    resolved_password = get_secret("datacomex_password", password)
    if not resolved_username or not resolved_password:
        return None
    return Credentials(resolved_username, resolved_password)
