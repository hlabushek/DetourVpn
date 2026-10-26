"""Serialized local maintenance primitives for a closed beta control plane."""

from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from vpn_api.db.connection import transaction
from vpn_api.db.models import User
from vpn_api.quota.service import LedgerService

# Dedicated project lock namespace; session-level so it spans cleanup and every account.
MAINTENANCE_LOCK_KEY = 21027001


def run_maintenance(engine: Engine, cleanup: Callable[[], dict[str, int]]) -> dict[str, Any]:
    """Run retained auth cleanup and ledger reconciliation without overlapping runs.

    Each operation commits independently. A failure can leave partial progress, but a
    retry is safe: cleanup uses retention cutoffs and ledger reconciliation is read-only.
    """
    with engine.connect() as lock_connection:
        acquired = bool(
            lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": MAINTENANCE_LOCK_KEY}
            )
        )
        if not acquired:
            return {"status": "already_running"}
        try:
            deleted = cleanup()
            accounts = 0
            inconsistent_accounts = 0
            violations = 0
            ledger_entries = 0
            receipts = 0
            service = LedgerService(engine)
            with Session(engine) as listing:
                statement = select(User.id).order_by(User.id).execution_options(yield_per=100)
                for user_id in listing.scalars(statement):
                    with transaction(engine) as session:
                        result = service.reconcile(session, user_id)
                    accounts += 1
                    inconsistent_accounts += int(not result.consistent)
                    violations += result.violations
                    ledger_entries += result.ledger_entries
                    receipts += result.receipts
            return {
                "status": "completed",
                "auth_rows_deleted": sum(deleted.values()),
                "accounts_reconciled": accounts,
                "inconsistent_accounts": inconsistent_accounts,
                "violations": violations,
                "ledger_entries": ledger_entries,
                "receipts": receipts,
            }
        finally:
            lock_connection.scalar(
                text("SELECT pg_advisory_unlock(:key)"), {"key": MAINTENANCE_LOCK_KEY}
            )
