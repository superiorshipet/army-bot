from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_SENSITIVE_PARTS = ("token", "password", "secret", "api_key", "private_key")


def redact_text(value: str, credentials: Mapping[str, Mapping[str, Any]]) -> str:
    redacted = value
    for provider in credentials.values():
        for key, secret in provider.items():
            if not any(part in key.lower() for part in _SENSITIVE_PARTS):
                continue
            if isinstance(secret, str) and len(secret) >= 4:
                redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def redact_lines(values: list[str], credentials: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [redact_text(value, credentials) for value in values]
