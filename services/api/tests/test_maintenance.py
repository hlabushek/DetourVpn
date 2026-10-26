"""I-027 maintenance runner tests use synthetic accounts and disposable PostgreSQL."""

from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from test_migrations import migration_config

from vpn_api.db.connection import transaction
from vpn_api.db.models import User
from vpn_api.maintenance import MAINTENANCE_LOCK_KEY, run_maintenance

pytestmark = pytest.mark.postgres


def test_maintenance_reconciles_accounts_and_reports_aggregates(engine: Any) -> None:
    command.upgrade(migration_config(engine), "head")
    with transaction(engine) as session:
        session.add_all([User(status="active"), User(status="active")])

    deleted = {"login_attempts": 2, "rate_buckets": 1}
    result = run_maintenance(engine, lambda: deleted)

    assert result == {
        "status": "completed",
        "auth_rows_deleted": 3,
        "accounts_reconciled": 2,
        "inconsistent_accounts": 0,
        "violations": 0,
        "ledger_entries": 0,
        "receipts": 0,
    }


def test_maintenance_skips_when_another_instance_holds_lock(engine: Any) -> None:
    command.upgrade(migration_config(engine), "head")
    cleanup_calls: list[bool] = []
    with engine.connect() as holder:
        assert holder.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK_KEY}
        )

        def cleanup() -> dict[str, int]:
            cleanup_calls.append(True)
            return {}

        result = run_maintenance(engine, cleanup)
        assert result == {"status": "already_running"}
        assert cleanup_calls == []
        assert holder.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK_KEY})


def test_maintenance_releases_lock_after_failure(engine: Any) -> None:
    command.upgrade(migration_config(engine), "head")

    def cleanup() -> dict[str, int]:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError, match="fail"):
        run_maintenance(engine, cleanup)
    with engine.connect() as check:
        assert check.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK_KEY}
        )
        check.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK_KEY})
