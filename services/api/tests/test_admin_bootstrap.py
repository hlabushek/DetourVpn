"""TEST ONLY enrollment URI parser cases; no real accounts, files or credentials."""

import base64
import secrets
from collections.abc import Callable
from importlib import import_module
from typing import cast
from urllib.parse import urlencode

import pytest

pending_secret = cast(
    Callable[[str, str], bytes], import_module("scripts.admin_bootstrap").pending_secret
)


def enrollment(**changes: str) -> tuple[str, bytes]:
    secret = secrets.token_bytes(20)
    values = {
        "secret": base64.b32encode(secret).decode(),
        "issuer": "DetourVpn Admin",
        "algorithm": "SHA1",
        "digits": "6",
        "period": "30",
        **changes,
    }
    return "otpauth://totp/DetourVpn%20Admin:owner?" + urlencode(values) + "\n", secret


def test_pending_enrollment_accepts_generated_format() -> None:
    uri, secret = enrollment()
    assert pending_secret(uri, "owner") == secret


@pytest.mark.parametrize(
    "changes",
    [
        {"issuer": "other"},
        {"algorithm": "SHA256"},
        {"digits": "8"},
        {"period": "60"},
        {"extra": "unexpected"},
        {"secret": "invalid"},
        {"secret": base64.b32encode(b"test-only-wrong-length").decode()},
    ],
)
def test_pending_enrollment_rejects_changed_parameters(changes: dict[str, str]) -> None:
    uri, _ = enrollment(**changes)
    with pytest.raises(ValueError):
        pending_secret(uri, "owner")


def test_pending_enrollment_rejects_wrong_account_duplicates_and_foreign_uri() -> None:
    uri, _ = enrollment()
    for changed in (
        uri.replace(":owner?", ":another?"),
        uri.replace("otpauth://", "https://"),
        uri.replace("://totp/", "://hotp/"),
        uri.replace("?secret=", "?issuer=DetourVpn+Admin&secret="),
        uri.rstrip() + "#fragment",
        uri.rstrip() + "\nsecret=not-allowed",
        "x" * 2049,
    ):
        with pytest.raises(ValueError):
            pending_secret(changed, "owner")
