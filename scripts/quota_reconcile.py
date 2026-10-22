"""Read-only local quota audit. Prints aggregate counts, never identities or credentials."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine, transaction  # noqa: E402
from vpn_api.db.models import User  # noqa: E402
from vpn_api.quota.contracts import QuotaError  # noqa: E402
from vpn_api.quota.service import LedgerService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-id", type=UUID, help="Optional local account UUID (not Telegram ID)"
    )
    args = parser.parse_args()
    engine = create_database_engine(Settings())
    counts = {
        "accounts": 0,
        "inconsistent_accounts": 0,
        "violations": 0,
        "ledger_entries": 0,
        "receipts": 0,
    }
    service = LedgerService(engine)
    try:
        with Session(engine) as listing:
            statement = select(User.id).order_by(User.id).execution_options(yield_per=100)
            if args.user_id is not None:
                statement = statement.where(User.id == args.user_id)
            for user_id in listing.scalars(statement):
                with transaction(engine) as session:
                    result = service.reconcile(session, user_id)
                counts["accounts"] += 1
                counts["inconsistent_accounts"] += int(not result.consistent)
                counts["violations"] += result.violations
                counts["ledger_entries"] += result.ledger_entries
                counts["receipts"] += result.receipts
        if args.user_id is not None and counts["accounts"] == 0:
            print("quota_account_not_found")
            return 2
        print(json.dumps(counts, sort_keys=True))
        return 1 if counts["inconsistent_accounts"] else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SQLAlchemyError, QuotaError, ValueError):
        print("quota_reconciliation_unavailable: check local configuration and migrations")
        raise SystemExit(2) from None
