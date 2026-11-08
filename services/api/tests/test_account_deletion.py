"""Account deletion tombstone behaviour against real PostgreSQL (ADR-091)."""

import base64
import secrets
from uuid import UUID

import pytest
from sqlalchemy import func, select
from test_authentication import Harness, harness, headers, login

from vpn_api.db.connection import transaction
from vpn_api.db.models import AuthIdentity, Device, SessionFamily, User
from vpn_api.devices import DeviceInput, register_device

__all__ = ["harness"]
pytestmark = pytest.mark.postgres
CONFIRM = {"confirm": "delete-my-account"}


def new_device() -> DeviceInput:
    # Synthetic public-key-shaped bytes; no native keypair is claimed.
    return DeviceInput(
        platform="android",
        key_type="x25519",
        public_key=base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
    )


def user_state(h: Harness, user_id: UUID) -> tuple[str, bool, int, int, int]:
    with transaction(h.engine) as session:
        user = session.get(User, user_id)
        assert user is not None
        identities = session.scalar(
            select(func.count()).select_from(AuthIdentity).where(AuthIdentity.user_id == user_id)
        )
        open_devices = session.scalar(
            select(func.count())
            .select_from(Device)
            .where(Device.user_id == user_id, Device.revoked_at.is_(None))
        )
        open_sessions = session.scalar(
            select(func.count())
            .select_from(SessionFamily)
            .where(SessionFamily.user_id == user_id, SessionFamily.revoked_at.is_(None))
        )
        return (
            user.status,
            user.deleted_at is not None,
            identities or 0,
            open_devices or 0,
            open_sessions or 0,
        )


def test_delete_revokes_everything_and_keeps_the_tombstone(harness: Harness) -> None:
    h = harness
    tokens = login(h, 111)
    register_device(h.auth, tokens.access_token, new_device())
    me = h.client.get("/api/v1/me", headers=headers(tokens)).json()
    response = h.client.post("/api/v1/me/delete", headers=headers(tokens), json=CONFIRM)
    assert response.status_code == 204
    assert user_state(h, UUID(me["id"])) == ("deleted", True, 0, 0, 0)
    assert h.client.get("/api/v1/me", headers=headers(tokens)).status_code == 401
    refreshed = h.client.post("/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token})
    assert refreshed.status_code in (400, 401, 403)


@pytest.mark.parametrize("body", [{}, {"confirm": "yes"}, {"confirm": "delete-my-account", "x": 1}])
def test_missing_or_wrong_confirmation_changes_nothing(
    harness: Harness, body: dict[str, object]
) -> None:
    h = harness
    tokens = login(h, 111)
    user_id = UUID(h.client.get("/api/v1/me", headers=headers(tokens)).json()["id"])
    response = h.client.post("/api/v1/me/delete", headers=headers(tokens), json=body)
    assert response.status_code == 422
    assert user_state(h, user_id)[0] == "active"
    assert h.client.get("/api/v1/me", headers=headers(tokens)).status_code == 200


def test_unauthenticated_delete_is_rejected(harness: Harness) -> None:
    response = harness.client.post("/api/v1/me/delete", json=CONFIRM)
    assert response.status_code == 401


def test_deleting_one_account_does_not_touch_another(harness: Harness) -> None:
    h = harness
    owner, other = login(h, 111), login(h, 222)
    register_device(h.auth, other.access_token, new_device())
    other_id = UUID(h.client.get("/api/v1/me", headers=headers(other)).json()["id"])
    assert (
        h.client.post("/api/v1/me/delete", headers=headers(owner), json=CONFIRM).status_code == 204
    )
    status, tombstone, identities, devices, sessions = user_state(h, other_id)
    assert (status, tombstone, identities, devices) == ("active", False, 1, 1)
    assert sessions >= 1
    assert h.client.get("/api/v1/me", headers=headers(other)).status_code == 200


def test_repeating_the_request_is_unauthorized_not_an_error(harness: Harness) -> None:
    h = harness
    tokens = login(h, 111)
    assert (
        h.client.post("/api/v1/me/delete", headers=headers(tokens), json=CONFIRM).status_code == 204
    )
    assert (
        h.client.post("/api/v1/me/delete", headers=headers(tokens), json=CONFIRM).status_code == 401
    )


def test_returning_subject_gets_a_new_account_when_invites_are_off(harness: Harness) -> None:
    # Documented limit (ADR-091): without invite enforcement a fresh login is a new account.
    # The beta environment enforces invites, and deletion revokes the subject's invite.
    h = harness
    first = login(h, 111)
    first_id = UUID(h.client.get("/api/v1/me", headers=headers(first)).json()["id"])
    assert (
        h.client.post("/api/v1/me/delete", headers=headers(first), json=CONFIRM).status_code == 204
    )
    second = login(h, 111)
    second_id = UUID(h.client.get("/api/v1/me", headers=headers(second)).json()["id"])
    assert second_id != first_id
    assert user_state(h, first_id)[0] == "deleted"


def test_deletion_revokes_the_beta_invite_of_the_subject(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from vpn_api.auth.security import beta_invite_digest
    from vpn_api.db.models import AdminIdentity, BetaInvite

    h = harness
    assert h.settings.auth_pepper is not None
    digest = beta_invite_digest(h.settings.auth_pepper.get_secret_value(), 111)
    now = datetime.now(UTC)
    with transaction(h.engine) as session:
        admin = AdminIdentity(
            username="test-admin",
            password_hash="x",  # noqa: S106 - TEST ONLY synthetic row.
            totp_ciphertext="x",
            role="admin",
            created_at=now,
        )  # TEST ONLY synthetic row.
        session.add(admin)
        session.flush()
        session.add(
            BetaInvite(subject_digest=digest, admin_id=admin.id, reason="test", created_at=now)
        )
    monkeypatch.setattr(h.auth.settings, "beta_invites_only", True)
    tokens = login(h, 111)
    assert (
        h.client.post("/api/v1/me/delete", headers=headers(tokens), json=CONFIRM).status_code == 204
    )
    with transaction(h.engine) as session:
        invite = session.scalar(select(BetaInvite).where(BetaInvite.subject_digest == digest))
        assert invite is not None and invite.revoked_at is not None
