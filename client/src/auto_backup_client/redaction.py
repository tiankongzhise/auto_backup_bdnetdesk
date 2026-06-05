from __future__ import annotations

from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"

SENSITIVE_FIELD_NAMES = {
    "access_token",
    "authorization",
    "baidu_app_secret",
    "ciphertext",
    "client_secret",
    "device_token",
    "encrypted_token_json",
    "password",
    "postgres_dsn",
    "private_key",
    "refresh_token",
    "secret",
    "token",
    "wrapped_key",
    "wrapping_key",
    "wrapping_key_base64",
}


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in SENSITIVE_FIELD_NAMES:
        return True
    return normalized.endswith("_token") or normalized.endswith("_secret")
