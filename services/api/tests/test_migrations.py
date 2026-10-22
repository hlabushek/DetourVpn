"""Real PostgreSQL tests. Never substitute SQLite or a mock for this module."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from vpn_api.db.connection import transaction
from vpn_api.db.models import AuthIdentity, User

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
    }
    assert {item["name"] for item in inspect(engine).get_indexes("users")} == {
        "ix_users_created_at"
    }
    assert "uq_auth_identities_user_provider" in {
        item["name"] for item in inspect(engine).get_indexes("auth_identities")
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


def test_downgrade_with_data_fails_without_losing_rows_or_revision(engine: Engine) -> None:
    config = upgrade(engine)
    with transaction(engine) as session:
        session.add(User())
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(config, "base")
    with transaction(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0011_referrals"


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
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0011_referrals"


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
