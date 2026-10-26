"""Separate admin authentication and allowlisted transactional actions. No remote shell."""

import secrets
from datetime import timedelta
from threading import BoundedSemaphore
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidKey
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken as InvalidCiphertext
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.twofactor import InvalidToken as InvalidOTP
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vpn_api.auth.routes import Access
from vpn_api.auth.security import AuthError, beta_invite_digest, digest, keyed_digest, token
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
    PeerConnection,
    PlanRevision,
    RateBucket,
    SessionFamily,
    Subscription,
    Tunnel,
    User,
    VPNNode,
)
from vpn_api.quota.contracts import MAX_BYTES, LedgerCommand, QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.schemas import ErrorResponse
from vpn_api.subscriptions import EntitlementService, PlanInput, PlanView
from vpn_api.tunnels.service import revoke_device_tunnels


def password_hash(password: str) -> str:
    if not 16 <= len(password) <= 256:
        raise ValueError("Use a unique password of 16-256 characters")
    return Argon2id(
        salt=secrets.token_bytes(16), length=32, iterations=3, lanes=1, memory_cost=65536
    ).derive_phc_encoded(password.encode())


def otp(secret: bytes) -> TOTP:
    # HMAC-SHA1 is the interoperable RFC 6238 TOTP mode, not a password hash.
    return TOTP(secret, 6, hashes.SHA1(), 30)  # noqa: S303


class AdminLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    password: SecretStr = Field(min_length=1, max_length=256)
    code: SecretStr = Field(min_length=6, max_length=6)


class AdminChange(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    operation: Literal[
        "credit",
        "debit",
        "grant_subscription",
        "revoke_subscription",
        "revoke_device",
        "revise_plan",
        "seed_demo_catalog",
        "grant_beta_invite",
        "revoke_beta_invite",
    ]
    idempotency_key: UUID
    reason: str = Field(min_length=3, max_length=500)
    user_id: UUID | None = None
    resource_id: UUID | None = None
    amount_bytes: int | None = Field(default=None, strict=True, gt=0, le=MAX_BYTES)
    plan: PlanInput | None = None
    telegram_subject: int | None = Field(default=None, strict=True, ge=1, lt=2**52)


class AdminService:
    def __init__(self, engine: Engine, settings: Settings) -> None:
        if settings.admin_encryption_key is None:
            raise ValueError("admin_key_required")
        self.engine = engine
        self.settings = settings
        self.key = settings.admin_encryption_key.get_secret_value()
        self.cipher = Fernet(self.key.encode("ascii"))
        # The single API process must not allocate 64 MiB per concurrent password
        # guess on small hosts. Queue only the CPU/memory-bound KDF, not network I/O.
        self.kdf_slot = BoundedSemaphore(1)
        # Equal KDF work for an unknown username, with a process-local random secret.
        self.dummy_hash = password_hash(token())

    def rate(self, username: str, network: str) -> None:
        exceeded = False
        with transaction(self.engine) as session:
            now = database_now(session)
            window = int(now.timestamp()) // 60
            for category, subject, limit in (
                ("global", "all", 60),
                ("network", network, 10),
                ("identity", username, 5),
            ):
                key = keyed_digest(self.key, "admin-login", category, subject, window)
                stmt = insert(RateBucket).values(
                    key=key, count=1, expires_at=now + timedelta(minutes=2)
                )
                count = session.scalar(
                    stmt.on_conflict_do_update(
                        index_elements=[RateBucket.key], set_={"count": RateBucket.count + 1}
                    ).returning(RateBucket.count)
                )
                exceeded = exceeded or (count is not None and count > limit)
        if exceeded:
            raise AuthError("rate_limited", 429)

    def login(self, body: AdminLogin, network: str) -> dict[str, object]:
        self.rate(body.username, network)
        with transaction(self.engine) as session:
            identity = session.scalar(
                select(AdminIdentity)
                .where(AdminIdentity.username == body.username)
                .with_for_update()
            )
            try:
                with self.kdf_slot:
                    Argon2id.verify_phc_encoded(
                        body.password.get_secret_value().encode(),
                        identity.password_hash if identity is not None else self.dummy_hash,
                    )
            except (InvalidKey, ValueError):
                raise AuthError("admin_unauthorized") from None
            if identity is None or identity.disabled_at is not None:
                raise AuthError("admin_unauthorized")
            now = database_now(session)
            try:
                secret = self.cipher.decrypt(identity.totp_ciphertext.encode())
            except InvalidCiphertext:
                raise AuthError("admin_unavailable", 503) from None
            step = int(now.timestamp()) // 30
            accepted = None
            code = body.code.get_secret_value().encode("utf-8")
            for candidate in (step - 1, step, step + 1):
                try:
                    otp(secret).verify(code, candidate * 30)
                    accepted = candidate
                except InvalidOTP:
                    pass
            if accepted is None or accepted <= identity.last_totp_step:
                raise AuthError("admin_unauthorized")
            identity.last_totp_step = accepted
            access = token()
            expires = now + timedelta(minutes=10)
            session.add(
                AdminSession(
                    admin_id=identity.id,
                    token_digest=digest(access),
                    created_at=now,
                    expires_at=expires,
                )
            )
            return {
                "access_token": access,
                "expires_at": expires.isoformat(),
                "role": identity.role,
            }

    @staticmethod
    def authenticate(session: Session, access: str, *, write: bool = False) -> AdminIdentity:
        now = database_now(session)
        identity = session.scalar(
            select(AdminIdentity)
            .join(AdminSession, AdminSession.admin_id == AdminIdentity.id)
            .where(
                AdminSession.token_digest == digest(access),
                AdminSession.revoked_at.is_(None),
                AdminSession.expires_at > now,
                AdminIdentity.disabled_at.is_(None),
            )
            .with_for_update(of=AdminIdentity)
        )
        if identity is None:
            raise AuthError("admin_unauthorized")
        if write and identity.role != "admin":
            raise AuthError("forbidden", 403)
        return identity

    def change(
        self, session: Session, identity: AdminIdentity, body: AdminChange
    ) -> dict[str, object]:
        if identity.role != "admin":
            raise AuthError("forbidden", 403)
        EntitlementService.audit(str(identity.id), body.reason)
        # Serialize retries before business locks. Audit and business effects commit together.
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": body.idempotency_key.int % (2**63)}
        )
        fingerprint_body = body.model_dump_json(exclude={"telegram_subject"})
        if body.telegram_subject is not None:
            if self.settings.auth_pepper is None:
                raise QuotaError("beta_invites_disabled", 409)
            # Do not persist an enumerable SHA-256 of a Telegram numeric identifier.
            fingerprint_body += ":" + beta_invite_digest(
                self.settings.auth_pepper.get_secret_value(), body.telegram_subject
            )
        fingerprint = digest(str(identity.id) + ":" + fingerprint_body)
        prior = session.scalar(
            select(AdminAction).where(AdminAction.idempotency_key == body.idempotency_key)
        )
        if prior is not None:
            if prior.request_digest != fingerprint:
                raise QuotaError("admin_idempotency_conflict")
            return prior.result
        op = body.operation
        required = {
            "credit": (True, False, True, False, False),
            "debit": (True, False, True, False, False),
            "grant_subscription": (True, True, False, False, False),
            "revoke_subscription": (True, True, False, False, False),
            "revoke_device": (True, True, False, False, False),
            "revise_plan": (False, False, False, True, False),
            "seed_demo_catalog": (False, False, False, False, False),
            "grant_beta_invite": (False, False, False, False, True),
            "revoke_beta_invite": (False, False, False, False, True),
        }[op]
        if required != tuple(
            value is not None
            for value in (
                body.user_id,
                body.resource_id,
                body.amount_bytes,
                body.plan,
                body.telegram_subject,
            )
        ):
            raise QuotaError("invalid_admin_action", 422)
        result: dict[str, object] = {}
        action_user_id = body.user_id
        entitlements = EntitlementService(self.engine)
        actor = str(identity.id)
        if op in ("credit", "debit"):
            assert body.user_id is not None and body.amount_bytes is not None  # noqa: S101
            receipt = LedgerService(self.engine).apply_in_transaction(
                session,
                body.user_id,
                LedgerCommand(
                    kind="credit" if op == "credit" else "debit",
                    amount_bytes=body.amount_bytes,
                    source_id=body.idempotency_key,
                    idempotency_key=body.idempotency_key,
                ),
            )
            if receipt.status != "applied":
                raise QuotaError(receipt.error_code or "quota_adjustment_failed")
            result = {"operation_id": str(receipt.operation_id)}
        elif op == "grant_subscription":
            assert body.user_id is not None and body.resource_id is not None  # noqa: S101
            row = entitlements.grant(
                session,
                body.user_id,
                body.resource_id,
                body.idempotency_key,
                actor=actor,
                reason=body.reason,
            )
            result = {"subscription_id": str(row.id)}
        elif op == "revoke_subscription":
            assert body.user_id is not None and body.resource_id is not None  # noqa: S101
            entitlements.revoke(
                session,
                body.user_id,
                body.resource_id,
                body.idempotency_key,
                actor=actor,
                reason=body.reason,
            )
            result = {"subscription_id": str(body.resource_id)}
        elif op == "revoke_device":
            assert body.user_id is not None and body.resource_id is not None  # noqa: S101
            LedgerService._lock_owner(session, body.user_id, active=False)
            device = session.scalar(
                select(Device)
                .where(Device.id == body.resource_id, Device.user_id == body.user_id)
                .with_for_update()
            )
            if device is None:
                raise QuotaError("device_not_found", 404)
            now = database_now(session)
            if device.revoked_at is None:
                device.revoked_at = now
            revoke_device_tunnels(session, body.user_id, device.id)
            session.execute(
                update(SessionFamily)
                .where(SessionFamily.user_id == body.user_id, SessionFamily.device_id == device.id)
                .values(revoked_at=now)
            )
            result = {"device_id": str(device.id)}
        elif op == "revise_plan":
            assert body.plan is not None  # noqa: S101
            revision = entitlements.revise_plan(
                session, body.plan, body.idempotency_key, actor=actor, reason=body.reason
            )
            result = {"plan_revision_id": str(revision.id), "version": revision.version}
        elif op == "grant_beta_invite":
            if not self.settings.beta_invites_only or self.settings.auth_pepper is None:
                raise QuotaError("beta_invites_disabled", 409)
            assert body.telegram_subject is not None  # noqa: S101
            subject = body.telegram_subject
            if str(subject) in body.reason:
                raise QuotaError("beta_invite_reason_must_not_contain_subject", 422)
            pepper = self.settings.auth_pepper.get_secret_value()
            # Use the same subject/identity locks as the auth flows before mutating invite state.
            for domain in ("subject", "identity"):
                lock_key = int(keyed_digest(pepper, domain, subject)[:16], 16)
                if lock_key >= 2**63:
                    lock_key -= 2**64
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            subject_digest = beta_invite_digest(pepper, subject)
            active_invite = session.scalar(
                select(BetaInvite)
                .where(
                    BetaInvite.subject_digest == subject_digest,
                    BetaInvite.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if active_invite is not None:
                raise QuotaError("beta_invite_exists", 409)
            auth_identity = session.scalar(
                select(AuthIdentity)
                .where(
                    AuthIdentity.provider == "telegram_bot",
                    AuthIdentity.provider_subject == subject,
                )
                .with_for_update()
            )
            now = database_now(session)
            if auth_identity is not None:
                user = session.scalar(
                    select(User).where(User.id == auth_identity.user_id).with_for_update()
                )
                if user is None:
                    raise QuotaError("beta_invite_account_unavailable", 409)
                if user.status == "disabled":
                    unresolved_tunnel = session.scalar(
                        select(Tunnel.id)
                        .where(Tunnel.user_id == user.id, Tunnel.state != "revoked")
                        .limit(1)
                    )
                    unresolved_connection = session.scalar(
                        select(PeerConnection.id)
                        .where(
                            PeerConnection.user_id == user.id,
                            PeerConnection.state != "closed",
                        )
                        .limit(1)
                    )
                    if unresolved_tunnel is not None or unresolved_connection is not None:
                        raise QuotaError("beta_invite_revoke_pending", 409)
                    user.status = "active"
                    session.execute(
                        update(SessionFamily)
                        .where(SessionFamily.user_id == user.id)
                        .values(revoked_at=now)
                    )
                elif user.status != "active":
                    raise QuotaError("beta_invite_account_unavailable", 409)
                action_user_id = user.id
            invite = BetaInvite(
                subject_digest=subject_digest,
                admin_id=identity.id,
                reason=body.reason,
                created_at=now,
            )
            session.add(invite)
            session.flush()
            result = {"beta_invite_id": str(invite.id), "status": "active"}
        elif op == "revoke_beta_invite":
            if not self.settings.beta_invites_only or self.settings.auth_pepper is None:
                raise QuotaError("beta_invites_disabled", 409)
            assert body.telegram_subject is not None  # noqa: S101
            if str(body.telegram_subject) in body.reason:
                raise QuotaError("beta_invite_reason_must_not_contain_subject", 422)
            subject_digest = beta_invite_digest(
                self.settings.auth_pepper.get_secret_value(), body.telegram_subject
            )
            active_invite_to_revoke = session.scalar(
                select(BetaInvite)
                .where(
                    BetaInvite.subject_digest == subject_digest,
                    BetaInvite.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if active_invite_to_revoke is None:
                raise QuotaError("beta_invite_not_found", 404)
            now = database_now(session)
            active_invite_to_revoke.revoked_at = now
            auth_identity = session.scalar(
                select(AuthIdentity)
                .where(
                    AuthIdentity.provider == "telegram_bot",
                    AuthIdentity.provider_subject == body.telegram_subject,
                )
                .with_for_update()
            )
            if auth_identity is not None:
                LedgerService._lock_owner(session, auth_identity.user_id, active=False)
                user = session.scalar(
                    select(User).where(User.id == auth_identity.user_id).with_for_update()
                )
                if user is not None:
                    action_user_id = user.id
                    user.status = "disabled"
                    session.execute(
                        update(SessionFamily)
                        .where(
                            SessionFamily.user_id == user.id,
                            SessionFamily.revoked_at.is_(None),
                        )
                        .values(revoked_at=now)
                    )
                    from vpn_api.tunnels.service import enqueue_revoke
                    from vpn_api.tunnels.window_service import revoke_connections

                    revoke_connections(session, user.id)
                    for tunnel in session.scalars(
                        select(Tunnel)
                        .where(Tunnel.user_id == user.id, Tunnel.state != "revoked")
                        .order_by(Tunnel.id)
                        .with_for_update()
                    ):
                        enqueue_revoke(session, tunnel)
            result = {"beta_invite_id": str(active_invite_to_revoke.id), "status": "revoked"}
        else:
            entitlements.seed_demonstration_catalog(session, actor=actor)
            result = {"catalog": "illustrative", "payments_enabled": False}
        session.add(
            AdminAction(
                admin_id=identity.id,
                user_id=action_user_id,
                operation=op,
                reason=body.reason,
                idempotency_key=body.idempotency_key,
                request_digest=fingerprint,
                result=result,
                created_at=database_now(session),
            )
        )
        session.flush()
        return result


def admin_service(request: Request) -> AdminService:
    result: AdminService | None = request.app.state.admin
    if result is None:
        raise AuthError("admin_unavailable", 503)
    return result


Admin = Annotated[AdminService, Depends(admin_service)]
router = APIRouter(
    prefix="/api/admin/v1",
    tags=["administration"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 409, 422, 429, 503)},
)


@router.post("/login")
def login(body: AdminLogin, request: Request, admin: Admin) -> dict[str, object]:
    return admin.login(body, request.client.host if request.client else "unknown")


@router.post("/logout")
def logout(admin: Admin, access: Access) -> dict[str, bool]:
    with transaction(admin.engine) as session:
        admin.authenticate(session, access)
        session.execute(
            update(AdminSession)
            .where(AdminSession.token_digest == digest(access))
            .values(revoked_at=database_now(session))
        )
    return {"logged_out": True}


@router.post("/actions")
def change(body: AdminChange, admin: Admin, access: Access) -> dict[str, object]:
    with transaction(admin.engine) as session:
        identity = admin.authenticate(session, access, write=True)
        return admin.change(session, identity, body)


@router.get("/users")
def users(
    admin: Admin,
    access: Access,
    q: str = Query("", max_length=64),
    offset: int = Query(0, ge=0, le=100000),
) -> list[dict[str, object]]:
    with transaction(admin.engine) as session:
        admin.authenticate(session, access)
        stmt = select(User).order_by(User.created_at.desc(), User.id).offset(offset).limit(50)
        if q:
            try:
                id_ = UUID(q)
                stmt = stmt.where(User.id == id_)
            except ValueError:
                if not q.isascii() or not q.isdecimal() or not 0 < int(q) <= MAX_BYTES:
                    return []
                stmt = stmt.join(AuthIdentity, AuthIdentity.user_id == User.id).where(
                    AuthIdentity.provider_subject == int(q)
                )
        return [
            {"id": str(row.id), "status": row.status, "created_at": row.created_at.isoformat()}
            for row in session.scalars(stmt)
        ]


@router.get("/users/{user_id}")
def user(user_id: UUID, admin: Admin, access: Access) -> dict[str, object]:
    with transaction(admin.engine) as session:
        admin.authenticate(session, access)
        row = session.get(User, user_id)
        if row is None:
            raise QuotaError("user_not_found", 404)
        ledger = LedgerService(admin.engine)
        # Disabled user history is readable for support; quota writes stay restricted.
        LedgerService._lock_owner(session, user_id, active=False)
        return {
            "id": str(row.id),
            "status": row.status,
            "quota": ledger._state(session, user_id).model_dump(mode="json"),
            "history": [
                entry.model_dump(mode="json")
                for entry in ledger.history(session, user_id, limit=20, include_inactive=True).items
            ],
            "subscriptions": [
                EntitlementService.view(session, item).model_dump(mode="json")
                for item in session.scalars(
                    select(Subscription)
                    .where(Subscription.user_id == user_id)
                    .order_by(Subscription.created_at.desc())
                    .limit(100)
                )
            ],
            "devices": [
                {"id": str(d.id), "platform": d.platform, "revoked": d.revoked_at is not None}
                for d in session.scalars(select(Device).where(Device.user_id == user_id).limit(100))
            ],
        }


@router.get("/plans", response_model=list[PlanView])
def plans(admin: Admin, access: Access) -> list[PlanView]:
    with transaction(admin.engine) as session:
        admin.authenticate(session, access)
        return [
            PlanView.model_validate(row)
            for row in session.scalars(
                select(PlanRevision).order_by(PlanRevision.created_at.desc()).limit(100)
            )
        ]


@router.get("/nodes")
def nodes(admin: Admin, access: Access) -> list[dict[str, object]]:
    with transaction(admin.engine) as session:
        admin.authenticate(session, access)
        now = database_now(session)
        pending = (
            select(func.count())
            .select_from(PeerCommand)
            .join(Tunnel, Tunnel.id == PeerCommand.tunnel_id)
            .where(
                PeerCommand.acknowledged_at.is_(None),
                PeerCommand.peer_version == Tunnel.peer_version,
                Tunnel.state != "revoked",
            )
        )
        return [
            {
                "id": str(node.id),
                "name": node.name,
                "status": node.status,
                "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at else None,
                "peer_protocol_version": node.peer_protocol_version,
                "capacity_limit": 1 if node.peer_protocol_version == 2 else None,
                "occupied": session.scalar(
                    select(func.count())
                    .select_from(Tunnel)
                    .where(Tunnel.node_id == node.id, Tunnel.state != "revoked")
                ),
                "unacknowledged_commands": session.scalar(
                    pending.where(PeerCommand.node_id == node.id)
                ),
                "stalled_commands": session.scalar(
                    pending.where(
                        PeerCommand.node_id == node.id,
                        PeerCommand.created_at <= now - timedelta(seconds=90),
                    )
                ),
            }
            for node in session.scalars(select(VPNNode).order_by(VPNNode.id).limit(100))
        ]


@router.get("/audit")
def audit(
    admin: Admin, access: Access, offset: int = Query(0, ge=0, le=100000)
) -> list[dict[str, object]]:
    with transaction(admin.engine) as session:
        admin.authenticate(session, access)
        return [
            {
                "id": str(row.id),
                "actor": str(row.admin_id),
                "user_id": str(row.user_id) if row.user_id else None,
                "operation": row.operation,
                "reason": row.reason,
                "idempotency_key": str(row.idempotency_key),
                "time": row.created_at.isoformat(),
                "result": row.result,
            }
            for row in session.scalars(
                select(AdminAction)
                .order_by(AdminAction.created_at.desc(), AdminAction.id)
                .offset(offset)
                .limit(100)
            )
        ]
