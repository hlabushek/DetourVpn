"""Local I-016 operations: encrypted backups, isolated restore drills and alert codes."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from cryptography.fernet import Fernet  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from vpn_api.operations import (  # noqa: E402
    OperationsError,
    exclusive_write,
    local_engine,
    make_backup,
    monitor,
    restore_drill,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["keygen", "backup", "drill", "check"])
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--postgres-bin", type=Path)
    parser.add_argument("--disk", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.action == "keygen":
        exclusive_write(args.key_file, Fernet.generate_key())
        print('{"code":"key_created"}')
        return 0
    if args.archive is None or (args.action in {"backup", "drill"} and args.postgres_bin is None):
        parser.error("--archive required; backup/drill also require --postgres-bin")
    # Deliberately no dotenv fallback and no database URL argument/process-list exposure.
    engine = local_engine(os.environ["VPN_OPERATIONS_DATABASE_URL"])
    try:
        if args.action == "backup":
            value = make_backup(engine, args.postgres_bin, args.archive, args.key_file)
            print(json.dumps({"code": "backup_created", "tables": len(value.tables)}))
            return 0
        if args.action == "drill":
            result = restore_drill(engine, args.postgres_bin, args.archive, args.key_file)
            print(json.dumps({"code": "drill_passed", **result}))
            return 0
        alerts = monitor(engine, args.archive, args.key_file, args.disk)
        print(
            json.dumps(
                {
                    "checked_at": datetime.now(UTC).isoformat(),
                    "alerts": alerts,
                    "scope": "local_control_plane",
                },
                sort_keys=True,
            )
        )
        return int(bool(alerts))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        ValueError,
        KeyError,
        SQLAlchemyError,
        OperationsError,
        subprocess.SubprocessError,
    ):
        # Never expose libpq/SQL/subprocess exceptions or paths to secret material.
        print('{"code":"operations_failed"}')
        raise SystemExit(2) from None
