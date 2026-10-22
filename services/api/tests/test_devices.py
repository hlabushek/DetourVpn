"""Ownership and races against real PostgreSQL; no native keys/tunnels claimed."""

import base64
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_authentication import Harness, harness, headers, login
from test_migrations import migration_config

from vpn_api.auth.security import AuthError
from vpn_api.db.connection import transaction
from vpn_api.db.models import Device, SessionFamily
from vpn_api.devices import DeviceInput, register_device

__all__ = ["harness"]  # Shared real-DB/auth fixture, explicitly fake Telegram sender.
pytestmark = pytest.mark.postgres


def key_input() -> DeviceInput:
    # Synthetic random public-key-shaped bytes, not proof of a valid native keypair.
    return DeviceInput(
        platform="android",
        key_type="x25519",
        public_key=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
    )


def test_identity_device_logout_scenario(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    body = key_input()
    created = h.client.post("/api/v1/devices", headers=headers(tokens), json=body.model_dump())
    assert created.status_code == 200
    device_id = created.json()["id"]
    repeated = h.client.post("/api/v1/devices", headers=headers(tokens), json=body.model_dump())
    assert repeated.status_code == 200 and repeated.json()["id"] == device_id
    own = h.client.get("/api/v1/devices", headers=headers(tokens))
    assert [item["id"] for item in own.json()] == [device_id]
    assert h.client.get("/api/v1/devices/" + device_id, headers=headers(tokens)).status_code == 200
    assert h.client.post("/api/v1/auth/logout", headers=headers(tokens)).status_code == 204
    assert h.client.get("/api/v1/devices", headers=headers(tokens)).status_code == 401


def test_other_account_cannot_list_read_revoke_or_claim_key(harness: Harness) -> None:
    h = harness
    owner, other = login(h, 111), login(h, 222)
    body = key_input()
    own = register_device(h.auth, owner.access_token, body)
    assert h.client.get("/api/v1/devices", headers=headers(other)).json() == []
    for method in ("GET", "DELETE"):
        for device_id in (own.id, uuid4()):
            response = h.client.request(
                method, f"/api/v1/devices/{device_id}", headers=headers(other)
            )
            assert response.status_code == 404
            assert body.public_key not in response.text
    assert (
        h.client.post("/api/v1/devices", headers=headers(other), json=body.model_dump()).status_code
        == 409
    )
    assert (
        h.client.get(f"/api/v1/devices/{own.id}", headers=headers(owner)).json()["revoked_at"]
        is None
    )


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "not-base64",
        " " * 44,
        base64.b64encode(bytes(32)).decode(),
        base64.b64encode(bytes(31)).decode(),
        base64.b64encode(bytes(33)).decode(),
    ],
)
def test_invalid_keys_rejected(harness: Harness, bad_key: str) -> None:
    h = harness
    tokens = login(h)
    response = h.client.post(
        "/api/v1/devices",
        headers=headers(tokens),
        json={
            "platform": "android",
            "key_type": "x25519",
            "public_key": bad_key,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_unknown_fields_cannot_submit_private_key_or_owner(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    for extra in (
        {"private_key": secrets.token_urlsafe(32)},
        {"user_id": str(uuid4())},
        {"revoked_at": None},
        {"platform": "ios"},
        {"key_type": "unknown"},
    ):
        body = key_input().model_dump() | extra
        assert (
            h.client.post("/api/v1/devices", headers=headers(tokens), json=body).status_code == 422
        )


def test_bound_session_cannot_change_device(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    register_device(h.auth, tokens.access_token, key_input())
    with pytest.raises(AuthError, match="session_device_mismatch"):
        register_device(h.auth, tokens.access_token, key_input())
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(Device)) == 1


def test_device_revoke_invalidates_all_bound_sessions_not_other_devices(harness: Harness) -> None:
    h = harness
    first, same_device, another_device = login(h), login(h), login(h)
    body = key_input()
    device = register_device(h.auth, first.access_token, body)
    assert register_device(h.auth, same_device.access_token, body).id == device.id
    register_device(h.auth, another_device.access_token, key_input())
    # Revocation from another device remains usable and repeatable.
    for _ in range(2):
        assert (
            h.client.delete(
                f"/api/v1/devices/{device.id}", headers=headers(another_device)
            ).status_code
            == 204
        )
    for revoked in (first, same_device):
        assert h.client.get("/api/v1/me", headers=headers(revoked)).status_code == 401
        with pytest.raises(AuthError, match="invalid_refresh"):
            h.auth.refresh(revoked.refresh_token, "device-revoke")
    assert h.client.get("/api/v1/me", headers=headers(another_device)).status_code == 200
    with pytest.raises(AuthError, match="device_conflict"):
        register_device(h.auth, another_device.access_token, body)
    assert len(h.client.get("/api/v1/devices", headers=headers(another_device)).json()) == 1
    assert (
        len(
            h.client.get(
                "/api/v1/devices?include_revoked=true", headers=headers(another_device)
            ).json()
        )
        == 2
    )


def test_revoke_own_device_closes_current_session(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    device = register_device(h.auth, tokens.access_token, key_input())
    assert (
        h.client.delete(f"/api/v1/devices/{device.id}", headers=headers(tokens)).status_code == 204
    )
    assert h.client.get("/api/v1/me", headers=headers(tokens)).status_code == 401


def test_device_limit_is_atomic_under_concurrent_registration(harness: Harness) -> None:
    h = harness
    tokens = [login(h) for _ in range(4)]
    for issued in tokens[:2]:
        register_device(h.auth, issued.access_token, key_input())
    barrier = Barrier(2, timeout=10)

    def register(index: int) -> str:
        body = key_input()
        barrier.wait()
        try:
            register_device(h.auth, tokens[index + 2].access_token, body)
            return "created"
        except AuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(register, range(2))) == ["created", "device_limit_reached"]
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(Device)) == 3


def test_same_key_registration_is_idempotent_under_concurrency(harness: Harness) -> None:
    h = harness
    tokens = [login(h), login(h)]
    body = key_input()
    barrier = Barrier(2, timeout=10)

    def register(index: int) -> UUID:
        barrier.wait()
        return register_device(h.auth, tokens[index].access_token, body).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(register, range(2)))) == 1
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(Device)) == 1


def test_two_accounts_cannot_register_same_key_race(harness: Harness) -> None:
    h = harness
    tokens = [login(h, 111), login(h, 222)]
    body = key_input()
    barrier = Barrier(2, timeout=10)

    def register(index: int) -> str:
        barrier.wait()
        try:
            register_device(h.auth, tokens[index].access_token, body)
            return "created"
        except AuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(register, range(2))) == ["created", "device_conflict"]


def test_database_rejects_cross_owner_session_binding(harness: Harness) -> None:
    h = harness
    owner, other = login(h, 111), login(h, 222)
    device = register_device(h.auth, owner.access_token, key_input())
    other_id = h.client.get("/api/v1/me", headers=headers(other)).json()["id"]
    with pytest.raises(IntegrityError), transaction(h.engine) as session:
        session.execute(
            update(SessionFamily)
            .where(SessionFamily.user_id == UUID(other_id))
            .values(device_id=device.id)
        )


def test_refresh_and_revoke_race_cannot_leave_valid_session(harness: Harness) -> None:
    h = harness
    tokens, operator = login(h), login(h)
    device = register_device(h.auth, tokens.access_token, key_input())
    barrier = Barrier(2, timeout=10)

    def refresh() -> str:
        barrier.wait()
        try:
            h.auth.refresh(tokens.refresh_token, "refresh-revoke")
            return "rotated"
        except AuthError as exc:
            return exc.code

    def revoke() -> int:
        barrier.wait()
        return h.client.delete(
            f"/api/v1/devices/{device.id}", headers=headers(operator)
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        renewed, removed = pool.submit(refresh), pool.submit(revoke)
        assert renewed.result(timeout=10) in {"rotated", "invalid_refresh"}
        assert removed.result(timeout=10) == 204
    with transaction(h.engine) as session:
        families = list(
            session.scalars(select(SessionFamily).where(SessionFamily.device_id == device.id))
        )
        assert families and all(family.revoked_at is not None for family in families)


def test_device_downgrade_preserves_populated_schema(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    register_device(h.auth, tokens.access_token, key_input())
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0002_authentication")
    assert h.client.get("/api/v1/devices", headers=headers(tokens)).status_code == 200


def test_list_is_bounded_and_owned(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    register_device(h.auth, tokens.access_token, key_input())
    assert h.client.get("/api/v1/devices?limit=101", headers=headers(tokens)).status_code == 422
    assert h.client.get("/api/v1/devices?offset=1", headers=headers(tokens)).json() == []
