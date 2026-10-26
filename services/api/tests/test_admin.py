"""Real PostgreSQL admin/MFA integration; all credentials and accounts are synthetic."""

import base64
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness as UserHarness
from test_authentication import harness, headers, login
from test_migrations import migration_config
from test_operations import binaries, key

from vpn_api import admin as admin_module
from vpn_api import operations as ops
from vpn_api.admin import AdminChange, AdminLogin, AdminService, otp, password_hash
from vpn_api.auth.security import AuthError, beta_invite_digest
from vpn_api.auth.service import database_now
from vpn_api.config import Settings
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    AdminAction,
    AdminIdentity,
    AdminSession,
    AuthIdentity,
    BetaInvite,
    Device,
    PeerCommand,
    QuotaLedger,
    SessionFamily,
    Tunnel,
    User,
    VPNNode,
)
from vpn_api.main import create_app
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService

__all__ = ["harness", "binaries", "key"]


@dataclass
class Harness:
    engine: Engine
    client: TestClient
    service: AdminService
    identity_id: UUID
    user_id: UUID
    secret: bytes
    password: str


@pytest.fixture
def admin(engine: Engine) -> Iterator[Harness]:
    command.upgrade(migration_config(engine), "head")
    encryption_key = Fernet.generate_key()
    settings = Settings(
        _env_file=None,
        environment="test",
        enable_admin=True,
        database_url=engine.url.render_as_string(False),
        admin_encryption_key=encryption_key.decode(),
    )
    secret, password = secrets.token_bytes(20), secrets.token_urlsafe(32)
    with transaction(engine) as session:
        identity = AdminIdentity(
            username="test-admin",
            role="admin",
            password_hash=password_hash(password),
            totp_ciphertext=Fernet(encryption_key).encrypt(secret).decode(),
            created_at=database_now(session),
        )
        user = User(status="active")
        session.add_all([identity, user])
        session.flush()
        identity_id, user_id = identity.id, user.id
    app = create_app(settings, database_engine=engine)
    with TestClient(app) as client:
        yield Harness(engine, client, app.state.admin, identity_id, user_id, secret, password)


def credentials(h: Harness) -> dict[str, str]:
    with transaction(h.engine) as session:
        now = database_now(session)
    return {
        "username": "test-admin",
        "password": h.password,
        "code": otp(h.secret).generate(int(now.timestamp())).decode(),
    }


def authorization(h: Harness) -> dict[str, str]:
    response = h.client.post("/api/admin/v1/login", json=credentials(h))
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def change(h: Harness, auth: dict[str, str], **fields: object) -> dict[str, object]:
    response = h.client.post(
        "/api/admin/v1/actions",
        headers=auth,
        json={"idempotency_key": str(uuid4()), "reason": "Synthetic acceptance action", **fields},
    )
    assert response.status_code == 200
    return dict(response.json())


def test_mfa_required_replay_denied_and_logout_revokes(admin: Harness) -> None:
    h = admin
    body = credentials(h)
    assert h.client.post("/api/admin/v1/login", json={**body, "code": "badotp"}).status_code == 401
    auth = authorization(h)
    assert h.client.post("/api/admin/v1/login", json=body).status_code == 401
    assert h.client.get("/api/admin/v1/users", headers=auth).status_code == 200
    assert h.client.post("/api/admin/v1/logout", headers=auth).status_code == 200
    assert h.client.get("/api/admin/v1/users", headers=auth).status_code == 401
    with transaction(h.engine) as session:
        saved = session.scalar(select(AdminSession.token_digest))
        assert saved is not None and saved not in auth["Authorization"]


def test_closed_setup_keeps_mfa_and_disables_telegram_user_endpoints(admin: Harness) -> None:
    config = Settings(
        _env_file=None,
        environment="closed",
        public_origin="https://vpn.example.invalid",
        database_url="postgresql+psycopg://vpn_api:test-only@127.0.0.1/test",
        enable_admin=True,
        closed_admin_only=True,
        admin_encryption_key=admin.service.key,
    )
    # Inject the isolated real test database; the configured URL is never opened.
    with TestClient(create_app(config, database_engine=admin.engine)) as client:
        body = credentials(admin)
        assert client.get("/api/v1/me").status_code == 503
        assert client.get("/api/admin/v1/users").status_code == 401
        assert (
            client.post("/api/admin/v1/login", json={**body, "code": "badotp"}).status_code == 401
        )
        login_response = client.post("/api/admin/v1/login", json=body)
        assert login_response.status_code == 200
        headers = {"Authorization": "Bearer " + login_response.json()["access_token"]}
        assert client.get("/api/admin/v1/users", headers=headers).status_code == 200
        assert client.get("/api/v1/me", headers=headers).status_code == 503


def test_parallel_mfa_code_allows_one_session(admin: Harness) -> None:
    h = admin
    body = AdminLogin.model_validate(credentials(h))
    barrier = Barrier(2, timeout=15)

    def run(_: int) -> str:
        barrier.wait()
        try:
            h.service.login(body, "test-network")
            return "accepted"
        except AuthError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ["accepted", "admin_unauthorized"]
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(AdminSession)) == 1


def test_ten_parallel_credit_retries_have_one_audit_and_ledger(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    body = {
        "operation": "credit",
        "user_id": str(h.user_id),
        "amount_bytes": 2**53 + 1,
        "idempotency_key": str(uuid4()),
        "reason": "Concurrency test adjustment",
    }
    barrier = Barrier(10, timeout=20)

    def run(_: int) -> int:
        barrier.wait()
        return h.client.post("/api/admin/v1/actions", headers=auth, json=body).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert list(pool.map(run, range(10))) == [200] * 10
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(AdminAction)) == 1
        assert session.scalar(select(func.count()).select_from(QuotaLedger)) == 1
        assert LedgerService(h.engine).quota(session, h.user_id).available_bytes == 2**53 + 1
        assert LedgerService(h.engine).reconcile(session, h.user_id).consistent
    response = h.client.post(
        "/api/admin/v1/actions", headers=auth, json={**body, "amount_bytes": 10}
    )
    assert response.status_code == 409


def test_grant_plan_subscription_and_user_read_are_real(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    change(h, auth, operation="seed_demo_catalog")
    plans = h.client.get("/api/admin/v1/plans", headers=auth).json()
    start = next(plan for plan in plans if plan["code"] == "start")
    assert start["price_minor"] == 9900 and not start["payments_enabled"]
    granted = change(
        h, auth, operation="grant_subscription", user_id=str(h.user_id), resource_id=start["id"]
    )
    state = h.client.get(f"/api/admin/v1/users/{h.user_id}", headers=auth).json()
    assert state["quota"]["available_bytes"] == 107374182400
    assert state["subscriptions"][0]["id"] == granted["subscription_id"]
    change(
        h,
        auth,
        operation="revoke_subscription",
        user_id=str(h.user_id),
        resource_id=granted["subscription_id"],
    )
    state = h.client.get(f"/api/admin/v1/users/{h.user_id}", headers=auth).json()
    assert state["quota"]["available_bytes"] == 0
    assert state["quota"]["used_bytes"] == 0
    assert state["subscriptions"][0]["state"] == "revoked"


def test_read_only_operator_cannot_write_and_user_token_never_authenticates(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    with transaction(h.engine) as session:
        identity = session.get(AdminIdentity, h.identity_id)
        assert identity is not None
        identity.role = "operator"
    assert h.client.get("/api/admin/v1/users", headers=auth).status_code == 200
    response = h.client.post(
        "/api/admin/v1/actions",
        headers=auth,
        json={
            "operation": "credit",
            "user_id": str(h.user_id),
            "amount_bytes": 100,
            "idempotency_key": str(uuid4()),
            "reason": "Operator forbidden action",
        },
    )
    assert response.status_code == 403
    assert (
        h.client.get(
            "/api/admin/v1/users", headers={"Authorization": "Bearer synthetic-user-token"}
        ).status_code
        == 401
    )


def test_audit_rollback_and_immutability(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    bad = h.client.post(
        "/api/admin/v1/actions",
        headers=auth,
        json={
            "operation": "debit",
            "user_id": str(h.user_id),
            "amount_bytes": 100,
            "idempotency_key": str(uuid4()),
            "reason": "Insufficient quota negative test",
        },
    )
    assert bad.status_code == 409
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(AdminAction)) == 0
        assert session.scalar(select(func.count()).select_from(QuotaLedger)) == 0
    change(h, auth, operation="credit", user_id=str(h.user_id), amount_bytes=100)
    with pytest.raises(DBAPIError), h.engine.begin() as connection:
        connection.execute(text("DELETE FROM admin_actions"))


def test_login_rate_limit_survives_service_recreation(
    admin: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = admin
    with transaction(h.engine) as session:
        now = database_now(session)
    # Synthetic fixed window; avoid a flaky assertion exactly across a minute boundary.
    monkeypatch.setattr(admin_module, "database_now", lambda _: now)
    body = credentials(h)
    body["password"] = secrets.token_urlsafe(32)
    for _ in range(5):
        assert h.client.post("/api/admin/v1/login", json=body).status_code == 401
    assert h.client.post("/api/admin/v1/login", json=body).status_code == 429
    recreated = AdminService(
        h.engine,
        Settings(
            _env_file=None,
            database_url=h.engine.url.render_as_string(False),
            enable_admin=True,
            admin_encryption_key=h.service.key,
        ),
    )
    with pytest.raises(AuthError, match="rate_limited"):
        recreated.login(AdminLogin.model_validate(body), "testclient")
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(AdminSession)) == 0


def test_static_admin_has_csp_and_no_secret_in_assets(admin: Harness) -> None:
    h = admin
    page = h.client.get("/admin/")
    assert page.status_code == 200
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    js = h.client.get("/admin/admin.js").text
    assert "innerHTML" not in js
    assert h.password not in page.text + js
    assert "localStorage.setItem" not in js
    assert (
        h.client.get("/admin/exact-json.js")
        .headers["content-type"]
        .startswith("application/javascript")
    )


def test_disabled_by_default_has_no_panel_or_login() -> None:
    with TestClient(create_app(Settings(_env_file=None, environment="test"))) as client:
        assert client.get("/admin/").status_code == 404
        assert (
            client.post(
                "/api/admin/v1/login",
                json={"username": "test-admin", "password": "unused", "code": "123456"},
            ).status_code
            == 503
        )


def test_real_native_session_has_no_admin_access(harness: UserHarness) -> None:
    h = harness
    tokens = login(h)
    key = Fernet.generate_key().decode()
    values = h.settings.model_dump()
    values.update(enable_admin=True, admin_encryption_key=key)
    settings = Settings.model_validate(values)
    with TestClient(
        create_app(settings, database_engine=h.engine, telegram_sender=h.sender)
    ) as client:
        assert client.get("/api/v1/me", headers=headers(tokens)).status_code == 200
        assert client.get("/api/admin/v1/users", headers=headers(tokens)).status_code == 401


def test_disabled_identity_and_expired_admin_session_fail_closed(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    with transaction(h.engine) as session:
        identity = session.get(AdminIdentity, h.identity_id)
        assert identity is not None
        identity.disabled_at = database_now(session)
    assert h.client.get("/api/admin/v1/users", headers=auth).status_code == 401
    with transaction(h.engine) as session:
        identity = session.get(AdminIdentity, h.identity_id)
        assert identity is not None
        identity.disabled_at = None  # TEST ONLY: exercise the independent expiry check.
        saved = session.scalar(select(AdminSession))
        assert saved is not None
        saved.expires_at = database_now(session) - timedelta(seconds=1)
    assert h.client.get("/api/admin/v1/users", headers=auth).status_code == 401


def test_device_revoke_is_owner_scoped_and_revokes_family_and_intent(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    with transaction(h.engine) as session:
        now = database_now(session)
        device = Device(
            user_id=h.user_id,
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(secrets.token_bytes(32)).decode(),
            created_at=now,
        )
        session.add(device)
        session.flush()
        family = SessionFamily(
            user_id=h.user_id,
            device_id=device.id,
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
        tunnel = Tunnel(
            user_id=h.user_id,
            device_id=device.id,
            idempotency_key=uuid4(),
            state="admission_pending",
            peer_version=1,
            created_at=now,
        )
        session.add_all([family, tunnel])
        session.flush()
        device_id, family_id, tunnel_id = device.id, family.id, tunnel.id
    wrong = h.client.post(
        "/api/admin/v1/actions",
        headers=auth,
        json={
            "operation": "revoke_device",
            "user_id": str(uuid4()),
            "resource_id": str(device_id),
            "reason": "Wrong owner negative test",
            "idempotency_key": str(uuid4()),
        },
    )
    assert wrong.status_code == 409
    change(h, auth, operation="revoke_device", user_id=str(h.user_id), resource_id=str(device_id))
    with transaction(h.engine) as session:
        saved_device = session.get(Device, device_id)
        saved_family = session.get(SessionFamily, family_id)
        saved_tunnel = session.get(Tunnel, tunnel_id)
        assert saved_device is not None and saved_device.revoked_at is not None
        assert saved_family is not None and saved_family.revoked_at is not None
        assert saved_tunnel is not None and saved_tunnel.state == "revoked"


def test_admin_reads_disabled_user_history(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    change(h, auth, operation="credit", user_id=str(h.user_id), amount_bytes=100)
    with transaction(h.engine) as session:
        user = session.get(User, h.user_id)
        assert user is not None
        user.status = "disabled"
    response = h.client.get(f"/api/admin/v1/users/{h.user_id}", headers=auth)
    assert response.status_code == 200
    assert len(response.json()["history"]) == 1


def test_nodes_show_current_capacity_and_stalled_commands_only(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    with transaction(h.engine) as session:
        now = database_now(session)
        node = VPNNode(
            provisioning_key=uuid4(),
            name="TEST ONLY node",
            region="test",
            secret_digest=secrets.token_hex(32),
            peer_protocol_version=2,
        )
        device = Device(
            user_id=h.user_id,
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(secrets.token_bytes(32)).decode(),
            created_at=now,
        )
        session.add_all([node, device])
        session.flush()
        tunnel = Tunnel(
            user_id=h.user_id,
            device_id=device.id,
            idempotency_key=uuid4(),
            node_id=node.id,
            state="revoke_pending",
            peer_version=2,
            created_at=now,
        )
        session.add(tunnel)
        session.flush()
        for version in (1, 2):
            session.add(
                PeerCommand(
                    tunnel_id=tunnel.id,
                    node_id=node.id,
                    peer_version=version,
                    operation="remove_peer",
                    created_at=now - timedelta(minutes=2),
                )
            )
    response = h.client.get("/api/admin/v1/nodes", headers=auth)
    assert response.status_code == 200
    row = response.json()[0]
    assert row["occupied"] == row["capacity_limit"] == 1
    assert row["unacknowledged_commands"] == row["stalled_commands"] == 1


def test_admin_encrypted_backup_restores_identity_audit_and_quota(
    admin: Harness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
) -> None:
    h = admin
    change(h, authorization(h), operation="credit", user_id=str(h.user_id), amount_bytes=100)
    archive = tmp_path / "admin.fernet"
    backup = ops.make_backup(h.engine, binaries, archive, key)
    assert backup.tables["admin_identities"].rows == 1
    assert backup.tables["admin_actions"].rows == 1
    assert ops.restore_drill(h.engine, binaries, archive, key)["accounts_reconciled"] == 1


def test_beta_invite_grant_revokes_sessions_and_allows_safe_reinvite(
    admin: Harness, harness: UserHarness
) -> None:
    h = admin
    user = harness
    subject = 987654321
    h.service.settings.beta_invites_only = True
    h.service.settings.auth_pepper = user.settings.auth_pepper
    user.auth.settings.beta_invites_only = True
    admin_headers = authorization(h)
    body = {
        "operation": "grant_beta_invite",
        "idempotency_key": str(uuid4()),
        "reason": "TEST ONLY closed beta invite",
        "telegram_subject": subject,
    }
    granted = h.client.post("/api/admin/v1/actions", headers=admin_headers, json=body)
    assert granted.status_code == 200
    assert str(subject) not in granted.text
    granted_result = granted.json()
    assert granted_result["status"] == "active"
    assert h.client.post("/api/admin/v1/actions", headers=admin_headers, json=body).json() == (
        granted_result
    )

    tokens = login(user, subject)
    assert user.client.get("/api/v1/me", headers=headers(tokens)).status_code == 200
    with transaction(h.engine) as session:
        invite = session.get(BetaInvite, UUID(granted_result["beta_invite_id"]))
        assert invite is not None
        assert invite.subject_digest == beta_invite_digest(user.auth.pepper, subject)
        assert str(subject) not in invite.subject_digest

    revoked = change(
        h,
        admin_headers,
        operation="revoke_beta_invite",
        telegram_subject=subject,
    )
    assert revoked["status"] == "revoked"
    assert user.client.get("/api/v1/me", headers=headers(tokens)).status_code == 401
    assert (
        user.client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token}
        ).status_code
        == 401
    )
    with transaction(h.engine) as session:
        identity = session.scalar(
            select(AuthIdentity).where(AuthIdentity.provider_subject == subject)
        )
        assert identity is not None
        account = session.get(User, identity.user_id)
        assert account is not None and account.status == "disabled"

    reinvited = change(
        h,
        admin_headers,
        operation="grant_beta_invite",
        telegram_subject=subject,
    )
    assert reinvited["status"] == "active"
    replacement = login(user, subject)
    assert user.client.get("/api/v1/me", headers=headers(replacement)).status_code == 200
    assert user.client.get("/api/v1/me", headers=headers(tokens)).status_code == 401


def test_beta_invite_concurrent_grants_are_serialized(admin: Harness) -> None:
    h = admin
    h.service.settings.beta_invites_only = True
    h.service.settings.auth_pepper = SecretStr("P" * 43)
    subject = 987654322
    barrier = Barrier(2, timeout=15)

    def grant(_: int) -> str:
        body = AdminChange(
            operation="grant_beta_invite",
            idempotency_key=uuid4(),
            reason="TEST ONLY concurrent invite",
            telegram_subject=subject,
        )
        barrier.wait()
        try:
            with transaction(h.engine) as session:
                identity = session.get(AdminIdentity, h.identity_id)
                assert identity is not None
                h.service.change(session, identity, body)
            return "created"
        except QuotaError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(grant, range(2)))
    assert outcomes == ["beta_invite_exists", "created"]
    with transaction(h.engine) as session:
        rows = session.scalars(
            select(BetaInvite).where(
                BetaInvite.subject_digest == beta_invite_digest("P" * 43, subject),
                BetaInvite.revoked_at.is_(None),
            )
        )
        assert len(list(rows)) == 1
