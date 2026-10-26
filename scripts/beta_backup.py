"""Create one encrypted beta backup on the selected beta target."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from vpn_api.beta_backup import create_beta_backup, create_latest_beta_backup  # noqa: E402
from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine  # noqa: E402
from vpn_api.operations import OperationsError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--postgres-bin", type=Path, required=True)
    parser.add_argument("--latest-slot", action="store_true")
    args = parser.parse_args()
    settings = Settings(_env_file=None)
    if settings.database_url is None:
        print('{"code":"database_not_configured"}')
        return 2
    engine = create_database_engine(settings)
    try:
        if args.latest_slot:
            archive = create_latest_beta_backup(
                engine, args.postgres_bin, args.archive_dir, args.key_file
            )
        else:
            archive = create_beta_backup(engine, args.postgres_bin, args.archive_dir, args.key_file)
        print(json.dumps({"code": "backup_created", "archive": archive.name}))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OperationsError as exc:
        reason = exc.args[0] if exc.args and isinstance(exc.args[0], str) else "unknown"
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) is None:
            reason = "unknown"
        print(json.dumps({"code": "backup_failed", "reason": reason}))
        raise SystemExit(2) from None
    except SQLAlchemyError:
        print('{"code":"backup_failed","reason":"database_error"}')
        raise SystemExit(2) from None
    except OSError:
        print('{"code":"backup_failed","reason":"filesystem_error"}')
        raise SystemExit(2) from None
    except ValueError:
        print('{"code":"backup_failed","reason":"configuration_error"}')
        raise SystemExit(2) from None
    except subprocess.SubprocessError:
        print('{"code":"backup_failed","reason":"subprocess_error"}')
        raise SystemExit(2) from None
