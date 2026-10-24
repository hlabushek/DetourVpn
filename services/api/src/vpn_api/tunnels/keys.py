"""Canonical public key validation shared by control and node protocol code."""

import base64


def canonical_key(value: str) -> str:
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode() != value:
        raise ValueError("Invalid canonical public key")
    return value
