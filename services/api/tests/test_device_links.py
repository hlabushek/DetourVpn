"""One-time link codes and guest devices against real PostgreSQL (ADR-093).

No native keys or tunnels are claimed; public keys are synthetic. Run with VPN_TEST_DATABASE_URL.
"""

import base64
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import select, update
from test_authentication import Harness, harness, headers, login
from test_migrations import migration_config

from vpn_api.auth.contracts import TokenResponse
from vpn_api.db.connection import transaction
from vpn_api.db.models import Device, DeviceLinkCode, SessionFamily

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def public_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def register(h: Harness, tokens: TokenResponse) -> dict[str, object]:
    response = h.client.post(
        "/api/v1/devices",
        headers=headers(tokens),
        json={"platform": "android", "key_type": "x25519", "public_key": public_key()},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_code(h: Harness, tokens: TokenResponse, **extra: object) -> dict[str, object]:
    response = h.client.post(
        "/api/v1/devices/link-codes",
        headers=headers(tokens),
        json={"idempotency_key": str(uuid4()), **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def redeem(h: Harness, code: str) -> object:
    return h.client.post("/api/v1/auth/link/redeem", json={"code": code})


def guest_login(h: Harness, owner: TokenResponse, **extra: object) -> TokenResponse:
    created = create_code(h, owner, **extra)
    response = redeem(h, str(created["code"]))
    assert response.status_code == 200, response.text
    return TokenResponse.model_validate(response.json())


def error_code(response: object) -> str:
    return response.json()["error"]["code"]  # type: ignore[attr-defined,no-any-return]


def test_code_is_shown_once_and_only_its_digest_is_stored(harness: Harness) -> None:
    h = harness
    owner = login(h)
    created = create_code(h, owner, label="Phone", traffic_cap_bytes=10**9)
    assert len(str(created["code"])) == 9 and str(created["code"])[4] == "-"
    raw = str(created["code"]).replace("-", "")
    with transaction(h.engine) as session:
        row = session.get(DeviceLinkCode, UUID(str(created["id"])))
        assert row is not None and raw not in row.code_digest and len(row.code_digest) == 64
        assert row.expires_at - row.created_at == timedelta(minutes=10)
    listed = h.client.get("/api/v1/devices/link-codes", headers=headers(owner)).json()
    assert [item["id"] for item in listed] == [created["id"]]
    assert "code" not in listed[0]


def test_redeem_register_inherits_label_and_cap_and_code_is_single_use(harness: Harness) -> None:
    h = harness
    owner = login(h)
    register(h, owner)
    created = create_code(h, owner, label="Phone", traffic_cap_bytes=5_000_000)
    # Lower case, no dash and a look-alike all still redeem; the digest is of the canonical form.
    sloppy = str(created["code"]).replace("-", "").lower()
    guest = TokenResponse.model_validate(redeem(h, sloppy).json())  # type: ignore[attr-defined]
    device = register(h, guest)
    assert (device["role"], device["label"], device["traffic_cap_bytes"]) == (
        "guest",
        "Phone",
        5_000_000,
    )
    again = redeem(h, str(created["code"]))
    assert again.status_code == 404 and error_code(again) == "invalid_link_code"  # type: ignore[attr-defined]
    with transaction(h.engine) as session:
        row = session.get(DeviceLinkCode, UUID(str(created["id"])))
        assert row is not None and str(row.consumed_device_id) == device["id"]
    assert h.client.get("/api/v1/devices/link-codes", headers=headers(owner)).json() == []


def test_unknown_expired_and_cancelled_codes_are_indistinguishable(harness: Harness) -> None:
    h = harness
    owner = login(h)
    expired = create_code(h, owner)
    cancelled = create_code(h, owner)
    with transaction(h.engine) as session:
        past = session.scalar(select(DeviceLinkCode.created_at).limit(1))
        assert past is not None
        session.execute(
            update(DeviceLinkCode)
            .where(DeviceLinkCode.id == UUID(str(expired["id"])))
            .values(created_at=past - timedelta(hours=1), expires_at=past - timedelta(minutes=1))
        )
    assert (
        h.client.delete(
            f"/api/v1/devices/link-codes/{cancelled['id']}", headers=headers(owner)
        ).status_code
        == 204
    )

    def stable(response: object) -> str:
        # request_id is random per request by design; everything else must be identical.
        error = dict(response.json()["error"])  # type: ignore[attr-defined]
        error.pop("request_id")
        return json.dumps(error, sort_keys=True)

    bodies = {
        stable(redeem(h, str(expired["code"]))),
        stable(redeem(h, str(cancelled["code"]))),
        stable(redeem(h, "ZZZZ-ZZZZ")),
        stable(redeem(h, "not a code")),
    }
    assert len(bodies) == 1 and "invalid_link_code" in bodies.pop()


def test_guest_is_limited_to_its_own_device_and_cannot_manage_the_account(
    harness: Harness,
) -> None:
    h = harness
    owner = login(h)
    owner_device = register(h, owner)
    guest = guest_login(h, owner)
    own = register(h, guest)
    listed = h.client.get("/api/v1/devices", headers=headers(guest)).json()
    assert [item["id"] for item in listed] == [own["id"]]
    me = h.client.get("/api/v1/me", headers=headers(guest)).json()
    assert me["telegram_subject"] == 0
    assert me["guest"] is True and me["device_id"] == own["id"]
    owner_me = h.client.get("/api/v1/me", headers=headers(owner)).json()
    assert owner_me["guest"] is False and owner_me["telegram_subject"] == 2**40
    forbidden = [
        h.client.post(
            "/api/v1/devices/link-codes",
            headers=headers(guest),
            json={"idempotency_key": str(uuid4())},
        ),
        h.client.get("/api/v1/devices/link-codes", headers=headers(guest)),
        h.client.get(f"/api/v1/devices/{owner_device['id']}", headers=headers(guest)),
        h.client.patch(f"/api/v1/devices/{own['id']}", headers=headers(guest), json={"label": "x"}),
        h.client.delete(f"/api/v1/devices/{owner_device['id']}", headers=headers(guest)),
        h.client.post("/api/v1/auth/logout-all", headers=headers(guest)),
        h.client.post(
            "/api/v1/me/delete", headers=headers(guest), json={"confirm": "delete-my-account"}
        ),
        h.client.post("/api/v1/referrals/code", headers=headers(guest), json={}),
    ]
    assert [response.status_code for response in forbidden] == [403] * len(forbidden)
    assert (
        h.client.get(f"/api/v1/devices/{owner_device['id']}", headers=headers(owner)).status_code
        == 200
    )


def test_guest_can_leave_and_owner_can_remove_a_guest(harness: Harness) -> None:
    h = harness
    owner = login(h)
    register(h, owner)
    leaving, removed = guest_login(h, owner), guest_login(h, owner)
    leaving_device = register(h, leaving)
    removed_device = register(h, removed)
    assert (
        h.client.delete(
            f"/api/v1/devices/{leaving_device['id']}", headers=headers(leaving)
        ).status_code
        == 204
    )
    assert h.client.get("/api/v1/devices", headers=headers(leaving)).status_code == 401
    assert (
        h.client.delete(
            f"/api/v1/devices/{removed_device['id']}", headers=headers(owner)
        ).status_code
        == 204
    )
    assert h.client.get("/api/v1/devices", headers=headers(removed)).status_code == 401
    with transaction(h.engine) as session:
        assert all(
            family.revoked_at is not None
            for family in session.scalars(
                select(SessionFamily).where(SessionFamily.role == "guest")
            )
        )


def test_owner_patches_label_and_cap_and_can_clear_them(harness: Harness) -> None:
    h = harness
    owner = login(h)
    device = register(h, owner)
    url = f"/api/v1/devices/{device['id']}"
    changed = h.client.patch(
        url, headers=headers(owner), json={"label": "Tablet", "traffic_cap_bytes": 123}
    ).json()
    assert (changed["label"], changed["traffic_cap_bytes"]) == ("Tablet", 123)
    assert changed["used_bytes_total"] == 0 and changed["used_bytes_30d"] == 0
    only_label = h.client.patch(url, headers=headers(owner), json={"label": "Pad"}).json()
    assert (only_label["label"], only_label["traffic_cap_bytes"]) == ("Pad", 123)
    cleared = h.client.patch(
        url, headers=headers(owner), json={"label": None, "traffic_cap_bytes": None}
    ).json()
    assert (cleared["label"], cleared["traffic_cap_bytes"]) == (None, None)
    assert h.client.patch(url, headers=headers(owner), json={"role": "guest"}).status_code == 422


def test_pending_codes_count_against_the_device_limit(harness: Harness) -> None:
    h = harness
    h.auth.settings.device_limit = 2
    owner = login(h)
    register(h, owner)
    first = create_code(h, owner)
    refused = h.client.post(
        "/api/v1/devices/link-codes",
        headers=headers(owner),
        json={"idempotency_key": str(uuid4())},
    )
    assert refused.status_code == 409 and error_code(refused) == "device_limit_reached"
    h.client.delete(f"/api/v1/devices/link-codes/{first['id']}", headers=headers(owner))
    create_code(h, owner)


def test_full_account_blocks_redeem_without_burning_the_code(harness: Harness) -> None:
    h = harness
    h.auth.settings.device_limit = 2
    owner = login(h)
    register(h, owner)
    created = create_code(h, owner)
    register(h, login(h, 2**40 + 1))  # Different account: must not count.
    h.auth.settings.device_limit = 1
    full = redeem(h, str(created["code"]))
    assert full.status_code == 409 and error_code(full) == "device_limit_reached"  # type: ignore[attr-defined]
    h.auth.settings.device_limit = 2
    assert redeem(h, str(created["code"])).status_code == 200  # type: ignore[attr-defined]


def test_idempotency_key_replay_does_not_reveal_a_second_code(harness: Harness) -> None:
    h = harness
    owner = login(h)
    key = str(uuid4())
    body = {"idempotency_key": key}
    assert (
        h.client.post("/api/v1/devices/link-codes", headers=headers(owner), json=body).status_code
        == 201
    )
    replay = h.client.post("/api/v1/devices/link-codes", headers=headers(owner), json=body)
    assert replay.status_code == 409 and error_code(replay) == "link_code_exists"
    assert "code" not in replay.json().get("error", {}).get("details", {})


def test_concurrent_redeem_of_one_code_succeeds_exactly_once(harness: Harness) -> None:
    h = harness
    owner = login(h)
    code = str(create_code(h, owner)["code"])
    barrier = Barrier(4, timeout=10)

    def attempt(_: int) -> int:
        barrier.wait()
        return redeem(h, code).status_code  # type: ignore[attr-defined,no-any-return]

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = sorted(pool.map(attempt, range(4)))
    assert statuses == [200, 404, 404, 404]
    with transaction(h.engine) as session:
        assert (
            len(list(session.scalars(select(SessionFamily).where(SessionFamily.role == "guest"))))
            == 1
        )


def test_guest_session_ends_when_the_owner_deletes_the_account(harness: Harness) -> None:
    h = harness
    owner = login(h)
    register(h, owner)
    guest = guest_login(h, owner)
    pending = create_code(h, owner)
    deleted = h.client.post(
        "/api/v1/me/delete", headers=headers(owner), json={"confirm": "delete-my-account"}
    )
    assert deleted.status_code == 204
    assert h.client.get("/api/v1/devices", headers=headers(guest)).status_code == 401
    assert redeem(h, str(pending["code"])).status_code == 404  # type: ignore[attr-defined]


def test_downgrade_refuses_while_guest_devices_exist(harness: Harness) -> None:
    h = harness
    owner = login(h)
    guest = guest_login(h, owner)
    register(h, guest)
    with pytest.raises(RuntimeError, match="guest"):
        command.downgrade(migration_config(h.engine), "0020_vpn_node_country")
    with transaction(h.engine) as session:
        assert session.scalar(select(Device.id).where(Device.role == "guest")) is not None
