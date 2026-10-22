"""Trusted LOCAL accounting CLI, not a node authentication or telemetry transport."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from pydantic import ValidationError  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine, transaction  # noqa: E402
from vpn_api.db.models import User  # noqa: E402
from vpn_api.quota.contracts import QuotaError  # noqa: E402
from vpn_api.usage.contracts import UsageError, UsageSampleInput  # noqa: E402
from vpn_api.usage.service import UsageService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bind = commands.add_parser("bind", help="Provision a trusted local accounting binding")
    epoch = commands.add_parser("open-epoch", help="Confirm a zero-based counter lifecycle/reset")
    ingest = commands.add_parser("ingest", help="Read one <=16 KiB report from a local file")
    for sub in (bind, epoch, ingest):
        sub.add_argument("--node-id", required=True, type=UUID)
    for sub in (bind, epoch):
        sub.add_argument("--peer-id", required=True, type=UUID)
    bind.add_argument("--device-id", required=True, type=UUID)
    epoch.add_argument("--epoch-id", required=True, type=UUID)
    epoch.add_argument("--expected-generation", required=True, type=int)
    epoch.add_argument("--started-at", required=True, type=datetime.fromisoformat)
    ingest.add_argument("--sample-file", required=True, type=Path)
    commands.add_parser("audit", help="Audit all local accounts; print aggregate counts only")
    args = parser.parse_args()
    engine = create_database_engine(Settings())
    service = UsageService(engine)
    try:
        if args.command == "bind":
            service.bind(args.node_id, args.peer_id, args.device_id)
            print("usage_binding_ready")
        elif args.command == "open-epoch":
            service.open_epoch(
                args.node_id, args.peer_id, args.epoch_id, args.expected_generation, args.started_at
            )
            print("usage_epoch_ready")
        elif args.command == "ingest":
            with args.sample_file.open("rb") as source:
                data = source.read(16385)
            if len(data) > 16384:
                raise UsageError("usage_sample_too_large")
            sample = UsageSampleInput.model_validate_json(data)
            result = service.ingest(args.node_id, sample)
            print(result.model_dump_json())
        else:
            totals = {
                "accounts": 0,
                "streams": 0,
                "epochs": 0,
                "samples": 0,
                "pending_bytes": 0,
                "violations": 0,
            }
            with Session(engine) as listing:
                for user_id in listing.scalars(
                    select(User.id).order_by(User.id).execution_options(yield_per=100)
                ):
                    with transaction(engine) as session:
                        result_audit = service.audit_user(session, user_id)
                    totals["accounts"] += 1
                    for key in ("streams", "epochs", "samples", "pending_bytes", "violations"):
                        totals[key] += int(getattr(result_audit, key))
            print(json.dumps(totals, sort_keys=True))
            return 1 if totals["violations"] else 0
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UsageError as error:
        print(error.code)
        raise SystemExit(2) from None
    except (QuotaError, SQLAlchemyError, ValidationError, ValueError, OSError):
        print("usage_operation_unavailable: check local input, configuration and migrations")
        raise SystemExit(2) from None
