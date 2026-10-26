from datetime import date, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'active', 'disabled')", name="ck_users_status"),
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
        Index("ix_users_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    role: Mapped[str] = mapped_column(String(16), server_default=text("'user'"))
    locale: Mapped[str] = mapped_column(String(16), server_default=text("'ru'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminIdentity(Base):
    __tablename__ = "admin_identities"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'operator')", name="ck_admin_identities_role"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    totp_ciphertext: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16))
    last_totp_step: Mapped[int] = mapped_column(BigInteger, server_default=text("-1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (Index("ix_admin_sessions_expiry", "expires_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    admin_id: Mapped[UUID] = mapped_column(ForeignKey("admin_identities.id", ondelete="RESTRICT"))
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminAction(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (Index("ix_admin_actions_created", "created_at", "id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    admin_id: Mapped[UUID] = mapped_column(ForeignKey("admin_identities.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    operation: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(500))
    idempotency_key: Mapped[UUID] = mapped_column(unique=True)
    request_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BetaInvite(Base):
    __tablename__ = "beta_invites"
    __table_args__ = (
        CheckConstraint("subject_digest ~ '^[0-9a-f]{64}$'", name="ck_beta_invites_subject_digest"),
        Index(
            "uq_beta_invites_active_subject",
            "subject_digest",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_beta_invites_admin_id_created", "admin_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    subject_digest: Mapped[str] = mapped_column(String(64))
    admin_id: Mapped[UUID] = mapped_column(ForeignKey("admin_identities.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        CheckConstraint("provider = 'telegram_bot'", name="ck_auth_identities_provider"),
        CheckConstraint("provider_subject > 0", name="ck_auth_identities_subject"),
        UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identities_provider_subject"
        ),
        UniqueConstraint("user_id", "provider", name="uq_auth_identities_user_provider"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", name="fk_auth_identities_user_id_users", ondelete="RESTRICT")
    )
    provider: Mapped[str] = mapped_column(String(32), server_default=text("'telegram_bot'"))
    provider_subject: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint(
            "checks BETWEEN 0 AND 5 AND generation >= 0", name="ck_login_attempts_budget"
        ),
        CheckConstraint("subject IS NULL OR subject > 0", name="ck_login_attempts_subject"),
        Index("ix_login_attempts_expires_at", "expires_at"),
        Index("ix_login_attempts_subject_issued_at", "subject", "issued_at"),
        CheckConstraint(
            "delivery_status IS NULL OR delivery_status IN ('pending', 'api_accepted', 'unknown')",
            name="ck_login_attempts_delivery_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    verifier_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    subject: Mapped[int | None] = mapped_column(BigInteger)
    code_digest: Mapped[str | None] = mapped_column(String(64))
    code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    checks: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str | None] = mapped_column(String(16))
    delivery_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionFamily(Base):
    __tablename__ = "session_families"
    __table_args__ = (
        CheckConstraint(
            "client_kind IN ('native', 'mini_app')", name="ck_session_families_client_kind"
        ),
        Index("ix_session_families_user_id", "user_id"),
        Index("ix_session_families_expires_at", "expires_at"),
        Index("ix_session_families_device_id", "device_id"),
        ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_session_families_device_owner",
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_id: Mapped[UUID | None] = mapped_column()
    client_kind: Mapped[str] = mapped_column(String(16), server_default=text("'native'"))


class MiniAppExchange(Base):
    __tablename__ = "mini_app_exchanges"
    __table_args__ = (Index("ix_mini_app_exchanges_expires_at", "expires_at"),)
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint("platform = 'android'", name="ck_devices_platform"),
        CheckConstraint("key_type = 'x25519'", name="ck_devices_key_type"),
        UniqueConstraint("public_key", name="uq_devices_public_key"),
        UniqueConstraint("id", "user_id", name="uq_devices_id_user_id"),
        Index("ix_devices_user_id_revoked_at", "user_id", "revoked_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    platform: Mapped[str] = mapped_column(String(16))
    key_type: Mapped[str] = mapped_column(String(16))
    public_key: Mapped[str] = mapped_column(String(44))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionCredential(Base):
    __tablename__ = "session_credentials"
    __table_args__ = (Index("ix_session_credentials_family_id", "family_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[UUID] = mapped_column(ForeignKey("session_families.id", ondelete="CASCADE"))
    access_digest: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotUpdate(Base):
    __tablename__ = "bot_updates"
    __table_args__ = (Index("ix_bot_updates_created_at", "created_at"),)
    bot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))


class CodeIssue(Base):
    __tablename__ = "code_issues"
    __table_args__ = (Index("ix_code_issues_subject_created_at", "subject", "created_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    subject: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    __table_args__ = (Index("ix_rate_buckets_expires_at", "expires_at"),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuotaOperation(Base):
    __tablename__ = "quota_operations"
    __table_args__ = (
        CheckConstraint(
            "reserved_before_bytes BETWEEN 0 AND balance_after_bytes",
            name="ck_quota_operations_reserved",
        ),
        UniqueConstraint("id", "user_id", name="uq_quota_operations_id_user"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_quota_operations_user_key"),
        UniqueConstraint("user_id", "source_type", "source_id", name="uq_quota_operations_source"),
        CheckConstraint("kind IN ('credit', 'debit')", name="ck_quota_operations_kind"),
        CheckConstraint(
            "source_type IN ('registration', 'internal', 'usage', 'tunnel_lease', "
            "'reward', 'peer_window')",
            name="ck_quota_operations_source",
        ),
        CheckConstraint(
            "source_type <> 'reward' OR kind = 'credit'",
            name="ck_quota_operations_reward",
        ),
        CheckConstraint(
            "amount_bytes >= 0 AND (amount_bytes > 0 OR "
            "(source_type = 'registration' AND kind = 'credit'))",
            name="ck_quota_operations_amount",
        ),
        CheckConstraint(
            "source_type <> 'registration' OR (kind = 'credit' AND "
            "source_id = user_id AND idempotency_key = user_id)",
            name="ck_quota_operations_registration",
        ),
        CheckConstraint(
            "(status = 'applied' AND error_code IS NULL) OR "
            "(status = 'rejected' AND error_code IS NOT NULL AND error_code IN "
            "('insufficient_quota', 'quota_capacity_exceeded'))",
            name="ck_quota_operations_outcome",
        ),
        CheckConstraint("balance_after_bytes >= 0", name="ck_quota_operations_balance"),
        CheckConstraint("revision_before >= 0", name="ck_quota_operations_revision"),
        CheckConstraint("unavailable_before_bytes >= 0", name="ck_quota_operations_unavailable"),
        CheckConstraint(
            "source_type NOT IN ('usage', 'tunnel_lease', 'peer_window') OR kind = 'debit'",
            name="ck_quota_operations_usage",
        ),
        Index("ix_quota_operations_created_at", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    amount_bytes: Mapped[int] = mapped_column(BigInteger)
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(32))
    balance_after_bytes: Mapped[int] = mapped_column(BigInteger)
    revision_before: Mapped[int] = mapped_column(BigInteger)
    reserved_before_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    unavailable_before_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuotaLedger(Base):
    __tablename__ = "quota_ledger"
    __table_args__ = (
        ForeignKeyConstraint(
            ["operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_quota_ledger_operation_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("operation_id", name="uq_quota_ledger_operation"),
        UniqueConstraint("user_id", "sequence", name="uq_quota_ledger_user_sequence"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_quota_ledger_user_key"),
        UniqueConstraint("user_id", "source_type", "source_id", name="uq_quota_ledger_source"),
        CheckConstraint("sequence > 0", name="ck_quota_ledger_sequence"),
        CheckConstraint(
            "delta_bytes >= -9223372036854775807 AND "
            "((kind = 'credit' AND delta_bytes >= 0) OR (kind = 'debit' AND delta_bytes < 0))",
            name="ck_quota_ledger_direction",
        ),
        CheckConstraint(
            "source_type IN ('registration', 'internal', 'usage', 'tunnel_lease', "
            "'reward', 'peer_window')",
            name="ck_quota_ledger_source",
        ),
        CheckConstraint(
            "source_type <> 'reward' OR kind = 'credit'",
            name="ck_quota_ledger_reward",
        ),
        CheckConstraint(
            "delta_bytes <> 0 OR source_type = 'registration'", name="ck_quota_ledger_zero"
        ),
        CheckConstraint(
            "source_type <> 'registration' OR (kind = 'credit' AND "
            "source_id = user_id AND idempotency_key = user_id)",
            name="ck_quota_ledger_registration",
        ),
        CheckConstraint("balance_after_bytes >= 0", name="ck_quota_ledger_balance"),
        CheckConstraint(
            "source_type NOT IN ('usage', 'tunnel_lease', 'peer_window') OR kind = 'debit'",
            name="ck_quota_ledger_usage",
        ),
        Index("ix_quota_ledger_created_at", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    operation_id: Mapped[UUID] = mapped_column()
    sequence: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    delta_bytes: Mapped[int] = mapped_column(BigInteger)
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[UUID] = mapped_column()
    balance_after_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuotaFundingLot(Base):
    __tablename__ = "quota_funding_lots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_quota_funding_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "user_id", name="uq_quota_funding_id_user"),
        CheckConstraint(
            "granted_bytes >= initial_remaining_bytes AND initial_remaining_bytes >= "
            "remaining_bytes AND remaining_bytes >= 0",
            name="ck_quota_funding_bytes",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > starts_at", name="ck_quota_funding_window"
        ),
        Index("ix_quota_funding_user_window", "user_id", "expires_at", "starts_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    granted_bytes: Mapped[int] = mapped_column(BigInteger)
    initial_remaining_bytes: Mapped[int] = mapped_column(BigInteger)
    remaining_bytes: Mapped[int] = mapped_column(BigInteger)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuotaFundingUse(Base):
    __tablename__ = "quota_funding_uses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_quota_funding_use_operation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lot_id", "user_id"],
            ["quota_funding_lots.id", "quota_funding_lots.user_id"],
            name="fk_quota_funding_use_lot",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_bytes > 0", name="ck_quota_funding_use_bytes"),
        Index("ix_quota_funding_uses_lot", "lot_id"),
    )
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount_bytes: Mapped[int] = mapped_column(BigInteger)


class PeerWindowHold(Base):
    """Immutable funding authority, not proof of node dispatch or admission."""

    __tablename__ = "peer_window_holds"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_peer_window_hold_owner"),
        UniqueConstraint("connection_id", "generation", name="uq_peer_window_generation"),
        CheckConstraint("amount_bytes BETWEEN 65536 AND 67108864", name="ck_peer_window_bytes"),
        CheckConstraint("generation > 0", name="ck_peer_window_generation"),
        CheckConstraint("not_after > created_at", name="ck_peer_window_deadline"),
        Index("ix_peer_window_hold_user", "user_id"),
        Index("ix_peer_window_hold_runtime", "node_id", "runtime_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    connection_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(BigInteger)
    node_id: Mapped[UUID] = mapped_column()
    runtime_id: Mapped[UUID] = mapped_column()
    slot_id: Mapped[int] = mapped_column(Integer)
    amount_bytes: Mapped[int] = mapped_column(BigInteger)
    grant: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PeerWindowAllocation(Base):
    __tablename__ = "peer_window_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["window_id", "user_id"],
            ["peer_window_holds.id", "peer_window_holds.user_id"],
            name="fk_peer_window_allocation_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["lot_id", "user_id"],
            ["quota_funding_lots.id", "quota_funding_lots.user_id"],
            name="fk_peer_window_allocation_lot",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("window_id", "position", name="uq_peer_window_allocation_position"),
        CheckConstraint("amount_bytes > 0 AND position >= 0", name="ck_peer_window_allocation"),
        Index("ix_peer_window_allocation_lot", "lot_id"),
    )
    window_id: Mapped[UUID] = mapped_column(primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer)
    amount_bytes: Mapped[int] = mapped_column(BigInteger)


class PeerWindowReceipt(Base):
    __tablename__ = "peer_window_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["window_id", "user_id"],
            ["peer_window_holds.id", "peer_window_holds.user_id"],
            name="fk_peer_window_receipt_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_peer_window_receipt_operation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("operation_id", name="uq_peer_window_receipt_operation"),
        CheckConstraint(
            "(observation = 'unknown' AND observed_bytes IS NULL AND operation_id IS NULL) OR "
            "(observation = 'measured' AND observed_bytes IS NOT NULL "
            "AND observed_bytes BETWEEN 0 AND 67108864 AND "
            "((observed_bytes = 0 AND operation_id IS NULL) OR "
            "(observed_bytes > 0 AND operation_id IS NOT NULL)))",
            name="ck_peer_window_receipt_evidence",
        ),
    )
    window_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    observation: Mapped[str] = mapped_column(String(16))
    observed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    operation_id: Mapped[UUID | None] = mapped_column()
    receipt: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PeerRuntimeEpoch(Base):
    __tablename__ = "peer_runtime_epochs"
    node_id: Mapped[UUID] = mapped_column(
        ForeignKey("vpn_nodes.id", ondelete="RESTRICT"), primary_key=True
    )
    runtime_id: Mapped[UUID] = mapped_column(primary_key=True)
    previous_runtime_id: Mapped[UUID | None] = mapped_column()
    manifest_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PeerConnection(Base):
    __tablename__ = "peer_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_peer_connection_device_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["window_id", "user_id"],
            ["peer_window_holds.id", "peer_window_holds.user_id"],
            name="fk_peer_connection_window_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["node_id", "runtime_id"],
            ["peer_runtime_epochs.node_id", "peer_runtime_epochs.runtime_id"],
            name="fk_peer_connection_epoch",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_peer_connection_key"),
        CheckConstraint(
            "slot_id BETWEEN 1 AND 16 AND generation > 0", name="ck_peer_connection_slot"
        ),
        CheckConstraint(
            "state IN ('pending','active','revoke_pending','closed')",
            name="ck_peer_connection_state",
        ),
        Index(
            "uq_peer_connection_live_slot",
            "node_id",
            "slot_id",
            unique=True,
            postgresql_where=text("state <> 'closed'"),
        ),
        Index(
            "uq_peer_connection_live_device",
            "device_id",
            unique=True,
            postgresql_where=text("state <> 'closed'"),
        ),
        Index("ix_peer_connection_owner", "user_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    device_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[UUID] = mapped_column()
    runtime_id: Mapped[UUID] = mapped_column()
    slot_id: Mapped[int] = mapped_column(Integer)
    window_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanRevision(Base):
    __tablename__ = "plan_revisions"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_plan_revisions_code_version"),
        UniqueConstraint("idempotency_key", name="uq_plan_revisions_key"),
        CheckConstraint(
            "version > 0 AND price_minor >= 0 AND quota_bytes > 0", name="ck_plan_revisions_amounts"
        ),
        CheckConstraint("period_days BETWEEN 1 AND 366", name="ck_plan_revisions_period"),
        CheckConstraint(
            "kind IN ('subscription', 'addon') AND currency = 'RUB'", name="ck_plan_revisions_kind"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), server_default=text("'RUB'"))
    quota_bytes: Mapped[int] = mapped_column(BigInteger)
    period_days: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[UUID] = mapped_column()
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["funding_id", "user_id"],
            ["quota_funding_lots.id", "quota_funding_lots.user_id"],
            name="fk_subscriptions_funding_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_subscriptions_user_key"),
        UniqueConstraint("funding_id", name="uq_subscriptions_funding"),
        CheckConstraint("ends_at > starts_at", name="ck_subscriptions_window"),
        CheckConstraint("basis = 'admin_grant'", name="ck_subscriptions_basis"),
        Index("ix_subscriptions_user_window", "user_id", "ends_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    plan_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("plan_revisions.id", ondelete="RESTRICT")
    )
    funding_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[UUID] = mapped_column()
    basis: Mapped[str] = mapped_column(String(32), server_default=text("'admin_grant'"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionRevocation(Base):
    __tablename__ = "subscription_revocations"
    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT"), primary_key=True
    )
    idempotency_key: Mapped[UUID] = mapped_column(unique=True)
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageStream(Base):
    __tablename__ = "usage_streams"
    __table_args__ = (
        UniqueConstraint("node_id", "peer_id", name="uq_usage_streams_node_peer"),
        UniqueConstraint("id", "user_id", name="uq_usage_streams_id_user"),
        UniqueConstraint("id", "node_id", name="uq_usage_streams_id_node"),
        ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_usage_streams_device_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("generation >= 0", name="ck_usage_streams_generation"),
        Index("ix_usage_streams_user_id", "user_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column()
    peer_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    device_id: Mapped[UUID] = mapped_column()
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageEpoch(Base):
    __tablename__ = "usage_epochs"
    __table_args__ = (
        UniqueConstraint("id", "stream_id", name="uq_usage_epochs_id_stream"),
        UniqueConstraint("stream_id", "generation", name="uq_usage_epochs_generation"),
        CheckConstraint("generation > 0 AND last_sequence >= 0", name="ck_usage_epochs_generation"),
        CheckConstraint("receipt_count >= 0", name="ck_usage_epochs_receipt_count"),
        CheckConstraint(
            "rx_bytes >= 0 AND tx_bytes >= 0 AND rx_bytes <= 9223372036854775807-tx_bytes "
            "AND charged_rx >= 0 AND charged_rx <= rx_bytes AND charged_tx >= 0 "
            "AND charged_tx <= tx_bytes",
            name="ck_usage_epochs_counters",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at > started_at", name="ck_usage_epochs_interval"
        ),
        CheckConstraint(
            "(last_sequence = 0 AND observed_at IS NULL AND rx_bytes = 0 AND tx_bytes = 0) "
            "OR (last_sequence > 0 AND observed_at IS NOT NULL)",
            name="ck_usage_epochs_observed",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    stream_id: Mapped[UUID] = mapped_column(ForeignKey("usage_streams.id", ondelete="RESTRICT"))
    generation: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    receipt_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    rx_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    tx_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    charged_rx: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    charged_tx: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageSample(Base):
    __tablename__ = "usage_samples"
    __table_args__ = (
        ForeignKeyConstraint(
            ["stream_id", "node_id"],
            ["usage_streams.id", "usage_streams.node_id"],
            name="fk_usage_samples_node",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["stream_id", "user_id"],
            ["usage_streams.id", "usage_streams.user_id"],
            name="fk_usage_samples_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["counter_epoch", "stream_id"],
            ["usage_epochs.id", "usage_epochs.stream_id"],
            name="fk_usage_samples_epoch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["quota_operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_usage_samples_quota_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("node_id", "idempotency_key", name="uq_usage_samples_node_key"),
        UniqueConstraint("counter_epoch", "sequence", name="uq_usage_samples_epoch_sequence"),
        UniqueConstraint("counter_epoch", "arrival_order", name="uq_usage_samples_epoch_arrival"),
        UniqueConstraint("quota_operation_id", name="uq_usage_samples_quota_operation"),
        CheckConstraint("arrival_order > 0", name="ck_usage_samples_arrival_order"),
        CheckConstraint(
            "schema_version = 1 AND sequence > 0", name="ck_usage_samples_version_sequence"
        ),
        CheckConstraint(
            "rx_total_bytes >= 0 AND tx_total_bytes >= 0 "
            "AND rx_total_bytes <= 9223372036854775807-tx_total_bytes "
            "AND delta_bytes >= 0 AND pending_bytes >= 0",
            name="ck_usage_samples_bytes",
        ),
        CheckConstraint(
            "(status = 'charged' AND reason IS NULL AND delta_bytes > 0 "
            "AND pending_bytes = 0 AND quota_operation_id IS NOT NULL) OR "
            "(status = 'blocked' AND reason IS NOT NULL AND reason IN "
            "('insufficient_quota', 'quota_capacity_exceeded') AND delta_bytes > 0 "
            "AND pending_bytes = delta_bytes AND quota_operation_id IS NOT NULL) OR "
            "(status IN ('no_change', 'stale') AND reason IS NULL AND delta_bytes = 0 "
            "AND quota_operation_id IS NULL AND (status <> 'no_change' OR pending_bytes = 0)) OR "
            "(status = 'rejected' AND reason IS NOT NULL AND reason IN "
            "('counter_regression', 'timestamp_regression', 'outside_epoch', 'future_sample', "
            "'stale_conflict') AND delta_bytes = 0 AND quota_operation_id IS NULL)",
            name="ck_usage_samples_outcome",
        ),
        Index("ix_usage_samples_received_at", "received_at"),
        Index("ix_usage_samples_user_id", "user_id"),
        Index("ix_usage_samples_stream_received", "stream_id", "received_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column()
    stream_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column()
    counter_epoch: Mapped[UUID] = mapped_column()
    schema_version: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(BigInteger)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    epoch_end_at_receipt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrival_order: Mapped[int] = mapped_column(BigInteger)
    rx_total_bytes: Mapped[int] = mapped_column(BigInteger)
    tx_total_bytes: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32))
    delta_bytes: Mapped[int] = mapped_column(BigInteger)
    pending_bytes: Mapped[int] = mapped_column(BigInteger)
    quota_operation_id: Mapped[UUID | None] = mapped_column()
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VPNNode(Base):
    __tablename__ = "vpn_nodes"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_vpn_nodes_status"),
        CheckConstraint("contract_version = 1", name="ck_vpn_nodes_contract_version"),
        CheckConstraint("length(name) BETWEEN 1 AND 80", name="ck_vpn_nodes_name"),
        CheckConstraint("length(region) BETWEEN 1 AND 32", name="ck_vpn_nodes_region"),
        UniqueConstraint("provisioning_key", name="uq_vpn_nodes_provisioning_key"),
        UniqueConstraint("id", "secret_digest", name="uq_vpn_nodes_id_secret_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provisioning_key: Mapped[UUID] = mapped_column()
    name: Mapped[str] = mapped_column(String(80))
    region: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    contract_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    secret_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    peer_protocol_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    peer_runtime_id: Mapped[UUID | None] = mapped_column()


class AgentRequestNonce(Base):
    __tablename__ = "agent_request_nonces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id", "secret_digest"],
            ["vpn_nodes.id", "vpn_nodes.secret_digest"],
            name="fk_agent_request_nonces_node_secret",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("node_id", "nonce", name="uq_agent_request_nonces_node_nonce"),
        Index("ix_agent_request_nonces_created_at", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    node_id: Mapped[UUID] = mapped_column()
    secret_digest: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Tunnel(Base):
    __tablename__ = "tunnels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_tunnels_device_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_tunnels_user_key"),
        UniqueConstraint("id", "node_id", name="uq_tunnels_id_node"),
        UniqueConstraint("id", "user_id", name="uq_tunnels_id_user"),
        CheckConstraint("peer_version > 0", name="ck_tunnels_version"),
        CheckConstraint(
            "(state = 'admission_pending' AND node_id IS NULL AND revoked_at IS NULL) OR "
            "(state IN ('provision_pending', 'active') AND node_id IS NOT NULL "
            "AND revoked_at IS NULL AND lease_deadline IS NOT NULL AND budget_bytes > 0) OR "
            "(state = 'revoke_pending' AND node_id IS NOT NULL AND revoked_at IS NULL) OR "
            "(state = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_tunnels_state",
        ),
        Index("ix_tunnels_user_created", "user_id", "created_at", "id"),
        Index(
            "uq_tunnels_live_device",
            "device_id",
            unique=True,
            postgresql_where=text("state <> 'revoked'"),
        ),
        Index(
            "uq_tunnels_live_node",
            "node_id",
            unique=True,
            postgresql_where=text("state <> 'revoked' AND node_id IS NOT NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    device_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[UUID] = mapped_column()
    node_id: Mapped[UUID | None] = mapped_column(ForeignKey("vpn_nodes.id", ondelete="RESTRICT"))
    state: Mapped[str] = mapped_column(String(24))
    peer_version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_bytes: Mapped[int | None] = mapped_column(BigInteger)
    runtime_id: Mapped[UUID | None] = mapped_column()


class PeerCommand(Base):
    __tablename__ = "peer_commands"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tunnel_id", "node_id"],
            ["tunnels.id", "tunnels.node_id"],
            name="fk_peer_commands_tunnel_node",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tunnel_id", "peer_version", name="uq_peer_commands_peer_version"),
        CheckConstraint(
            "operation IN ('add_peer', 'remove_peer')", name="ck_peer_commands_operation"
        ),
        CheckConstraint("peer_version > 0", name="ck_peer_commands_version"),
        CheckConstraint(
            "acknowledged_at IS NULL OR acknowledged_at >= created_at",
            name="ck_peer_commands_ack_time",
        ),
        Index(
            "ix_peer_commands_pending",
            "node_id",
            "created_at",
            "id",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tunnel_id: Mapped[UUID] = mapped_column()
    node_id: Mapped[UUID] = mapped_column()
    operation: Mapped[str] = mapped_column(String(16))
    peer_version: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceKeyProof(Base):
    __tablename__ = "device_key_proofs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["device_id", "user_id"],
            ["devices.id", "devices.user_id"],
            name="fk_device_key_proofs_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("expires_at > created_at", name="ck_device_key_proofs_expiry"),
        Index("ix_device_key_proofs_expiry", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    device_id: Mapped[UUID] = mapped_column()
    family_id: Mapped[UUID] = mapped_column(ForeignKey("session_families.id", ondelete="RESTRICT"))
    response_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TunnelReservation(Base):
    __tablename__ = "tunnel_reservations"
    __table_args__ = (
        CheckConstraint(
            "observed_bytes IS NULL OR observed_bytes >= 0", name="ck_tunnel_reservations_observed"
        ),
        ForeignKeyConstraint(
            ["tunnel_id", "user_id"],
            ["tunnels.id", "tunnels.user_id"],
            name="fk_tunnel_reservations_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount_bytes > 0 AND amount_bytes <= 16777216", name="ck_tunnel_reservations_amount"
        ),
        CheckConstraint(
            "charged_bytes IS NULL OR charged_bytes BETWEEN 0 AND amount_bytes",
            name="ck_tunnel_reservations_charge",
        ),
        CheckConstraint(
            "(settled_at IS NULL) = (charged_bytes IS NULL)", name="ck_tunnel_reservations_settled"
        ),
        Index("ix_tunnel_reservations_owner", "user_id"),
    )
    tunnel_id: Mapped[UUID] = mapped_column(
        ForeignKey("tunnels.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount_bytes: Mapped[int] = mapped_column(BigInteger)
    charged_bytes: Mapped[int | None] = mapped_column(BigInteger)
    observed_bytes: Mapped[int | None] = mapped_column(BigInteger)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RewardRule(Base):
    __tablename__ = "reward_rules"
    __table_args__ = (
        UniqueConstraint("reward_type", name="uq_reward_rules_type"),
        CheckConstraint("reward_type IN ('daily', 'clicker')", name="ck_reward_rules_type"),
        CheckConstraint(
            "reward_bytes > 0 AND daily_cap_bytes >= reward_bytes", name="ck_reward_rules_bytes"
        ),
        CheckConstraint(
            "daily_cap_claims > 0 AND cooldown_seconds >= 1", name="ck_reward_rules_limits"
        ),
        CheckConstraint(
            "reward_type <> 'daily' OR daily_cap_claims = 1", name="ck_reward_rules_daily"
        ),
        CheckConstraint("ends_at > starts_at", name="ck_reward_rules_window"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    reward_type: Mapped[Literal["daily", "clicker"]] = mapped_column(String(16))
    reward_bytes: Mapped[int] = mapped_column(BigInteger)
    daily_cap_bytes: Mapped[int] = mapped_column(BigInteger)
    daily_cap_claims: Mapped[int] = mapped_column(Integer)
    cooldown_seconds: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RewardClaim(Base):
    __tablename__ = "reward_claims"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_reward_claims_user_key"),
        UniqueConstraint("operation_id", name="uq_reward_claims_operation"),
        ForeignKeyConstraint(
            ["operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_reward_claims_operation_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("reward_type IN ('daily', 'clicker')", name="ck_reward_claims_type"),
        CheckConstraint(
            "reward_bytes > 0 AND balance_after_bytes >= reward_bytes",
            name="ck_reward_claims_bytes",
        ),
        CheckConstraint(
            "reward_day = (created_at AT TIME ZONE 'UTC')::date", name="ck_reward_claims_day"
        ),
        Index(
            "ix_reward_claims_owner_type_day", "user_id", "reward_type", "reward_day", "created_at"
        ),
        Index("ix_reward_claims_owner_type_time", "user_id", "reward_type", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("reward_rules.id", ondelete="RESTRICT"))
    operation_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[UUID] = mapped_column()
    reward_type: Mapped[Literal["daily", "clicker"]] = mapped_column(String(16))
    reward_bytes: Mapped[int] = mapped_column(BigInteger)
    balance_after_bytes: Mapped[int] = mapped_column(BigInteger)
    reward_day: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReferralCampaign(Base):
    __tablename__ = "referral_campaigns"
    __table_args__ = (
        CheckConstraint(
            "reward_bytes > 0 AND budget_bytes >= reward_bytes", name="ck_referral_campaigns_bytes"
        ),
        CheckConstraint("inviter_cap > 0", name="ck_referral_campaigns_cap"),
        CheckConstraint("ends_at > starts_at", name="ck_referral_campaigns_window"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    reward_bytes: Mapped[int] = mapped_column(BigInteger)
    budget_bytes: Mapped[int] = mapped_column(BigInteger)
    inviter_cap: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReferralCode(Base):
    __tablename__ = "referral_codes"
    __table_args__ = (UniqueConstraint("code", name="uq_referral_codes_code"),)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(24))


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (
        UniqueConstraint("invitee_id", name="uq_referrals_invitee"),
        UniqueConstraint("id", "inviter_id", name="uq_referrals_id_inviter"),
        CheckConstraint("inviter_id <> invitee_id", name="ck_referrals_no_self"),
        CheckConstraint("reward_bytes > 0", name="ck_referrals_bytes"),
        Index("ix_referrals_inviter_created", "inviter_id", "created_at", "id"),
        Index("ix_referrals_campaign", "campaign_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    inviter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    invitee_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_campaigns.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[UUID] = mapped_column()
    reward_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReferralAward(Base):
    __tablename__ = "referral_awards"
    __table_args__ = (
        ForeignKeyConstraint(
            ["referral_id", "user_id"],
            ["referrals.id", "referrals.inviter_id"],
            name="fk_referral_awards_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_referral_awards_operation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("operation_id", name="uq_referral_awards_operation"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_referral_awards_user_key"),
        CheckConstraint(
            "reward_bytes > 0 AND balance_after_bytes >= reward_bytes",
            name="ck_referral_awards_bytes",
        ),
        Index("ix_referral_awards_campaign_user", "campaign_id", "user_id"),
    )
    referral_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_campaigns.id", ondelete="RESTRICT")
    )
    operation_id: Mapped[UUID] = mapped_column()
    idempotency_key: Mapped[UUID] = mapped_column()
    reward_bytes: Mapped[int] = mapped_column(BigInteger)
    balance_after_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
