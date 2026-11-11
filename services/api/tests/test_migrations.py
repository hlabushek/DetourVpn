"""Real PostgreSQL tests. Never substitute SQLite or a mock for this module."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, delete, func, inspect, select, text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

from vpn_api.db.connection import transaction
from vpn_api.db.models import AdminIdentity, AuthIdentity, BetaInvite, Device, User

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[3]


def migration_config(engine: Engine) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_engine"] = engine
    return config


def upgrade(engine: Engine) -> Config:
    config = migration_config(engine)
    command.upgrade(config, "head")
    return config


def test_empty_database_upgrade_repeat_and_no_schema_drift(engine: Engine) -> None:
    config = upgrade(engine)
    command.upgrade(config, "head")
    assert set(inspect(engine).get_table_names()) == {
        "users",
        "auth_identities",
        "alembic_version",
        "login_attempts",
        "session_families",
        "session_credentials",
        "bot_updates",
        "code_issues",
        "rate_buckets",
        "devices",
        "quota_operations",
        "quota_ledger",
        "usage_streams",
        "usage_epochs",
        "usage_samples",
        "vpn_nodes",
        "agent_request_nonces",
        "tunnels",
        "peer_commands",
        "device_key_proofs",
        "tunnel_reservations",
        "reward_rules",
        "reward_claims",
        "mini_app_exchanges",
        "referral_campaigns",
        "referral_codes",
        "referrals",
        "referral_awards",
        "quota_funding_lots",
        "quota_funding_uses",
        "plan_revisions",
        "subscriptions",
        "subscription_revocations",
        "admin_identities",
        "admin_sessions",
        "admin_actions",
        "beta_invites",
        "device_link_codes",
        "peer_window_holds",
        "peer_window_allocations",
        "peer_window_receipts",
        "peer_window_resolutions",
        "peer_runtime_epochs",
        "peer_connections",
        "panel_server_sources",
    }
    assert {item["name"] for item in inspect(engine).get_indexes("users")} == {
        "ix_users_created_at"
    }
    assert "uq_auth_identities_user_provider" in {
        item["name"] for item in inspect(engine).get_indexes("auth_identities")
    }
    login_columns = {item["name"] for item in inspect(engine).get_columns("login_attempts")}
    assert {"delivery_status", "delivery_recorded_at"} <= login_columns
    assert "ck_login_attempts_delivery_status" in {
        item["name"] for item in inspect(engine).get_check_constraints("login_attempts")
    }
    command.check(config)
    with transaction(engine) as session:
        user = User()
        session.add(user)
        session.flush()
        assert user.status == "pending"
        assert user.created_at.tzinfo is not None
        assert user.verified_at is None
        session.add(AuthIdentity(user_id=user.id, provider_subject=2**40))
    with transaction(engine) as session:
        assert session.scalar(select(func.count()).select_from(AuthIdentity)) == 1


def test_empty_downgrade_and_reupgrade(engine: Engine) -> None:
    config = upgrade(engine)
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    command.upgrade(config, "head")
    command.check(config)


def test_beta_invite_migration_refuses_downgrade_after_invites_exist(engine: Engine) -> None:
    config = upgrade(engine)
    test_password_hash = uuid4().hex
    with transaction(engine) as session:
        admin = AdminIdentity(
            username="migration-test",
            password_hash=test_password_hash,
            totp_ciphertext="TEST ONLY",
            role="admin",
            created_at=datetime.now(UTC),
        )
        session.add(admin)
        session.flush()
        session.add(
            BetaInvite(
                subject_digest="a" * 64,
                admin_id=admin.id,
                reason="TEST ONLY migration guard",
                created_at=datetime.now(UTC),
            )
        )
    with pytest.raises(RuntimeError, match="beta_invites_data_requires_reviewed_forward_migration"):
        command.downgrade(config, "0015_window_admission")
    assert "beta_invites" in inspect(engine).get_table_names()


def test_account_deletion_migration_constraints_and_rollback(engine: Engine) -> None:
    config = upgrade(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    assert "deleted_at" in columns
    # The tombstone state and its timestamp must agree in both directions.
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.execute(
            text("INSERT INTO users (id, status) VALUES (:id, 'deleted')"), {"id": uuid4()}
        )
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.execute(
            text("INSERT INTO users (id, status, deleted_at) VALUES (:id, 'active', now())"),
            {"id": uuid4()},
        )
    # A live account does not block the rollback; the old status set is restored.
    with transaction(engine) as session:
        session.execute(
            text("INSERT INTO users (id, status) VALUES (:id, 'active')"), {"id": uuid4()}
        )
    command.downgrade(config, "0018_peer_window_resolution")
    assert "deleted_at" not in {column["name"] for column in inspect(engine).get_columns("users")}
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.execute(
            text("INSERT INTO users (id, status) VALUES (:id, 'deleted')"), {"id": uuid4()}
        )
    command.upgrade(config, "head")
    # A real tombstone makes the rollback refuse and leaves the revision untouched.
    with transaction(engine) as session:
        session.execute(
            text("INSERT INTO users (id, status, deleted_at) VALUES (:id, 'deleted', now())"),
            {"id": uuid4()},
        )
    with pytest.raises(RuntimeError, match="deleted accounts exist"):
        command.downgrade(config, "0018_peer_window_resolution")
    with transaction(engine) as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0027_panel_server_sources"
        )
        assert session.scalar(text("SELECT count(*) FROM users WHERE status = 'deleted'")) == 1


def test_device_platform_migration_constraint_and_guarded_rollback(engine: Engine) -> None:
    config = upgrade(engine)

    def add_device(platform: str) -> None:
        with transaction(engine) as session:
            user = User()
            session.add(user)
            session.flush()
            session.add(
                Device(
                    user_id=user.id,
                    platform=platform,
                    key_type="x25519",
                    public_key="A" * 43 + "=",  # synthetic, not a real key
                    created_at=datetime.now(UTC),
                )
            )

    add_device("windows")
    with pytest.raises(IntegrityError):
        add_device("ios")
    # A Windows device blocks the rollback and leaves data and revision untouched.
    with pytest.raises(DBAPIError, match="Refusing downgrade: windows devices exist"):
        command.downgrade(config, "0021_device_link_codes")
    with transaction(engine) as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0027_panel_server_sources"
        )
        assert session.scalar(select(func.count()).select_from(Device)) == 1
        session.execute(delete(Device))
    # With only Android data (here: none) the rollback works and restores the old constraint.
    command.downgrade(config, "0021_device_link_codes")
    with pytest.raises(IntegrityError):
        add_device("windows")
    command.upgrade(config, "head")
    command.check(config)
    add_device("windows")


def test_downgrade_with_data_fails_without_losing_rows_or_revision(engine: Engine) -> None:
    config = upgrade(engine)
    with transaction(engine) as session:
        session.add(User())
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(config, "base")
    with transaction(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0027_panel_server_sources"
        )


def test_downgrade_cannot_delete_a_concurrently_committing_account(engine: Engine) -> None:
    config = upgrade(engine)
    with engine.connect() as writer:
        pending = writer.begin()
        writer.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": uuid4()})
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(command.downgrade, config, "base")
            waiting = False
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        waiting = bool(
                            observer.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND wait_event_type = 'Lock')"
                                )
                            )
                        )
                    if waiting:
                        break
                    time.sleep(0.02)
                assert waiting, "Downgrade did not wait for the pending writer"
            finally:
                pending.commit()
            with pytest.raises(DBAPIError, match="Refusing downgrade"):
                result.result(timeout=10)
    with transaction(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0027_panel_server_sources"
        )


@pytest.mark.parametrize("subject", [0, -1])
def test_invalid_telegram_subject_is_rejected(engine: Engine, subject: int) -> None:
    upgrade(engine)
    with transaction(engine) as session:
        user = User()
        session.add(user)
        session.flush()
        user_id = user.id
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.add(AuthIdentity(user_id=user_id, provider_subject=subject))


def test_foreign_key_and_delete_protection(engine: Engine) -> None:
    upgrade(engine)
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.add(AuthIdentity(user_id=uuid4(), provider_subject=1))
    with transaction(engine) as session:
        user = User()
        session.add(user)
        session.flush()
        user_id = user.id
        session.add(AuthIdentity(user_id=user_id, provider_subject=1))
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


def test_transaction_rolls_back_on_error(engine: Engine) -> None:
    upgrade(engine)
    with pytest.raises(RuntimeError, match="abort"), transaction(engine) as session:
        session.add(User())
        session.flush()
        raise RuntimeError("abort")
    with transaction(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_provider_status_and_single_identity_per_account(engine: Engine) -> None:
    upgrade(engine)
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.add(User(status="unknown"))
    with transaction(engine) as session:
        user = User()
        session.add(user)
        session.flush()
        user_id = user.id
        session.add(AuthIdentity(user_id=user_id, provider_subject=1))
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.add(AuthIdentity(user_id=user_id, provider="email", provider_subject=2))
    with pytest.raises(IntegrityError), transaction(engine) as session:
        session.add(AuthIdentity(user_id=user_id, provider_subject=2))


def test_loopback_connection_is_not_overridden_by_libpq_env(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    upgrade(engine)


def test_duplicate_identity_is_rejected_under_concurrency(engine: Engine) -> None:
    upgrade(engine)
    with transaction(engine) as session:
        users = [User(), User()]
        session.add_all(users)
        session.flush()
        ids = [user.id for user in users]
    barrier = Barrier(2, timeout=10)

    def insert(index: int) -> str:
        try:
            with transaction(engine) as session:
                barrier.wait()
                session.add(AuthIdentity(user_id=ids[index], provider_subject=123456789))
            return "inserted"
        except IntegrityError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insert, range(2))) == ["duplicate", "inserted"]
    with transaction(engine) as session:
        assert session.scalar(select(func.count()).select_from(AuthIdentity)) == 1


def test_telegram_username_constraint_and_rollback(engine: Engine) -> None:
    # ADR-110: only Telegram-format usernames or NULL; rollback drops the column.
    config = upgrade(engine)
    with transaction(engine) as session:
        user = User()
        session.add(user)
        session.flush()
        session.add(
            AuthIdentity(user_id=user.id, provider_subject=2**30, telegram_username="Synthetic_1")
        )
    # Too long fails on the column length (DataError), malformed on the check (IntegrityError).
    for bad in ("abc", "bad-name", "a" * 33):
        with pytest.raises((IntegrityError, DataError)), transaction(engine) as session:
            session.execute(
                text("UPDATE auth_identities SET telegram_username = :name"), {"name": bad}
            )
    command.downgrade(config, "0025_pool_peer_slots")
    columns = {column["name"] for column in inspect(engine).get_columns("auth_identities")}
    assert "telegram_username" not in columns
    command.upgrade(config, "head")
    command.check(config)


def test_panel_server_rollback_refuses_while_rows_exist(engine: Engine) -> None:
    # ADR-111: the ciphertext is the only copy of the imported link; never drop it silently.
    config = upgrade(engine)
    with transaction(engine) as session:
        admin = AdminIdentity(
            username="migration-test-admin",
            password_hash="synthetic-not-a-hash",  # noqa: S106 - test placeholder, no login
            totp_ciphertext="synthetic",
            role="admin",
            created_at=datetime.now(UTC),
        )
        session.add(admin)
        session.flush()
        session.execute(
            text(
                "INSERT INTO panel_server_sources (id, label, panel_kind, protocol,"
                " link_ciphertext, created_by, created_at) VALUES (:id, 'test', '3x-ui',"
                " 'vless_reality', 'synthetic-ciphertext', :admin, now())"
            ),
            {"id": uuid4(), "admin": admin.id},
        )
    for kind, protocol in (("other", "vless_reality"), ("3x-ui", "vmess")):
        with pytest.raises(IntegrityError), transaction(engine) as session:
            session.execute(
                text(
                    "INSERT INTO panel_server_sources (id, label, panel_kind, protocol,"
                    " link_ciphertext, created_by, created_at) VALUES (:id, 'x', :kind,"
                    " :protocol, 'c', :admin, now())"
                ),
                {"id": uuid4(), "kind": kind, "protocol": protocol, "admin": admin.id},
            )
    with pytest.raises(DBAPIError, match="Refusing downgrade: panel servers exist"):
        command.downgrade(config, "0026_telegram_username")
    with transaction(engine) as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0027_panel_server_sources"
        )
        session.execute(text("DELETE FROM panel_server_sources"))
    command.downgrade(config, "0026_telegram_username")
    assert "panel_server_sources" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
