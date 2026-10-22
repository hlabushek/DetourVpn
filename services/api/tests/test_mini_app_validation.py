"""TEST ONLY signed payloads, generated with an ephemeral synthetic bot token."""

import hashlib
import hmac
import json
import secrets
from urllib.parse import urlencode

import pytest

from vpn_api.auth.mini_app import validate_init_data
from vpn_api.auth.security import AuthError


def signed(token: str, **changes: str) -> str:
    fields = {
        "auth_date": "1000",
        "user": json.dumps({"id": 123, "first_name": "Тест"}),
        "query_id": secrets.token_hex(16),
        **changes,
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    key = hmac.digest(b"WebAppData", token.encode(), hashlib.sha256)
    return urlencode({**fields, "hash": hmac.new(key, check.encode(), hashlib.sha256).hexdigest()})


def test_valid_and_all_fields_covered() -> None:
    token = secrets.token_urlsafe(32)
    raw = signed(token, signature="third_party_signature", start_param="hello")
    assert validate_init_data(raw, token, 1000)[0] == 123
    assert validate_init_data(raw, token, 1300)[0] == 123
    with pytest.raises(AuthError, match="invalid_init_data"):
        validate_init_data(raw.replace("hello", "changed"), token, 1000)


@pytest.mark.parametrize("age", [-1, 301, 1000000])
def test_time_window(age: int) -> None:
    token = secrets.token_urlsafe(32)
    with pytest.raises(AuthError):
        validate_init_data(signed(token), token, 1000 + age)


@pytest.mark.parametrize("raw", ["", "hash=x", "a=%GG", "x", "x=%FF", "x=" * 10000])
def test_malformed(raw: str) -> None:
    with pytest.raises(AuthError):
        validate_init_data(raw, secrets.token_urlsafe(32), 1000)


@pytest.mark.parametrize(
    "user",
    [
        "null",
        "[]",
        "{}",
        '{"id":true}',
        '{"id":1.0}',
        '{"id":0}',
        '{"id":-1}',
        '{"id":4503599627370496}',
        '{"id":1,"is_bot":true}',
    ],
)
def test_invalid_signed_subject(user: str) -> None:
    token = secrets.token_urlsafe(32)
    with pytest.raises(AuthError):
        validate_init_data(signed(token, user=user), token, 1000)


def test_duplicates_wrong_bot_tampering_and_bad_timestamp() -> None:
    token = secrets.token_urlsafe(32)
    raw = signed(token)
    for value in (
        raw + "&auth_date=1000",
        raw + "&%61uth_date=1000",
        raw.replace("1000", "1001"),
        signed(token, auth_date="1e3"),
    ):
        with pytest.raises(AuthError):
            validate_init_data(value, token, 1000)
    with pytest.raises(AuthError):
        validate_init_data(raw, secrets.token_urlsafe(32), 1000)
