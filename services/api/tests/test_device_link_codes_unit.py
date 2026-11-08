"""Pure checks for ADR-093 (no database): code format, input validation, guest permission set."""

import re
from uuid import uuid4

import pytest
from pydantic import ValidationError

from vpn_api.auth.security import Principal, require_permission
from vpn_api.auth.service import GUEST_PERMISSIONS
from vpn_api.devices import (
    CODE_ALPHABET,
    DevicePatch,
    LinkCodeCreate,
    LinkRedeemInput,
    display_link_code,
    new_link_code,
    normalize_link_code,
)


def test_generated_codes_are_crockford_base32_of_eight_characters() -> None:
    assert len(CODE_ALPHABET) == 32 and len(set(CODE_ALPHABET)) == 32
    assert not set("ILOU") & set(CODE_ALPHABET)
    for _ in range(200):
        code = new_link_code()
        assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{8}", code)
        assert normalize_link_code(display_link_code(code)) == code


def test_user_input_is_normalised_but_never_guessed() -> None:
    assert normalize_link_code("abcd-2345") == "ABCD2345"
    assert normalize_link_code(" abcd 2345 ") == "ABCD2345"
    assert normalize_link_code("oiL0-1111") == "01101111"  # Look-alikes fold to 0 / 1.
    for bad in ("", "ABCD234", "ABCD23456", "ABCD-234U", "ABCD_2345", "ÀBCD2345", "ABCD2345;"):
        assert normalize_link_code(bad) is None


def test_inputs_reject_unknown_fields_and_bad_values() -> None:
    ok = {"idempotency_key": str(uuid4())}
    assert LinkCodeCreate.model_validate(ok).label is None
    assert LinkCodeCreate.model_validate(ok | {"label": "Phone", "traffic_cap_bytes": 10**9})
    for bad in (
        {"label": ""},
        {"label": "x" * 41},
        {"label": "bad\nlabel"},
        {"traffic_cap_bytes": 0},
        {"traffic_cap_bytes": -1},
        {"traffic_cap_bytes": 2**51},
        {"role": "owner"},
    ):
        with pytest.raises(ValidationError):
            LinkCodeCreate.model_validate(ok | bad)
    with pytest.raises(ValidationError):
        LinkRedeemInput.model_validate({"code": "x" * 33})
    assert "ABCD" not in repr(LinkRedeemInput.model_validate({"code": "ABCD-2345"}))


def test_patch_distinguishes_missing_from_explicit_null() -> None:
    assert DevicePatch.model_validate({}).model_fields_set == set()
    cleared = DevicePatch.model_validate({"label": None, "traffic_cap_bytes": None})
    assert cleared.model_fields_set == {"label", "traffic_cap_bytes"}
    with pytest.raises(ValidationError):
        DevicePatch.model_validate({"role": "owner"})


def test_guest_permissions_exclude_every_owner_only_action() -> None:
    owner_only = {"rewards:claim", "referrals:manage", "account:delete"}
    assert not owner_only & GUEST_PERMISSIONS
    assert not any(permission.startswith("admin:") for permission in GUEST_PERMISSIONS)
    # Every guest permission is something a plain user already has: guests add no new power.
    user = Principal(uuid4(), uuid4(), "user", 1)
    for permission in GUEST_PERMISSIONS:
        require_permission(user, permission)
