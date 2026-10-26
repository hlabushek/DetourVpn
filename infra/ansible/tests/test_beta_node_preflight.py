"""Deterministic TEST ONLY checks for beta slot key validation."""

import sys
from pathlib import Path

NODE = Path(__file__).resolve().parents[2] / ".." / "services" / "node-agent"
sys.path.insert(0, str(NODE.resolve()))

import beta_node_preflight as preflight  # noqa: E402


def test_preflight_accepts_canonical_nonzero_256_bit_key() -> None:
    import base64

    assert preflight._key(base64.b64encode(bytes([7]) * 32).decode())


def test_preflight_rejects_invalid_key_without_echoing_value() -> None:
    assert not preflight._key("not-a-key")
    assert not preflight._key("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    assert not preflight._key("AQ==")
