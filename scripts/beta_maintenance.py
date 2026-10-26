"""One-shot local beta maintenance: retained auth cleanup and quota reconciliation."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from vpn_api.auth.service import cleanup_retained_auth_rows  # noqa: E402
from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine  # noqa: E402
from vpn_api.maintenance import run_maintenance  # noqa: E402
from vpn_api.quota.contracts import QuotaError  # noqa: E402


def main() -> int:
    # An explicit systemd EnvironmentFile supplies runtime config; do not read a copied .env.
    settings = Settings(_env_file=None)
    if settings.database_url is None:
        print('{"code":"database_not_configured"}')
        return 2
    engine = create_database_engine(settings)
    try:
        result = run_maintenance(engine, lambda: cleanup_retained_auth_rows(engine))
        print(json.dumps(result, sort_keys=True))
        if result["status"] == "already_running":
            return 3
        return int(result["inconsistent_accounts"] > 0)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SQLAlchemyError, QuotaError, OSError, ValueError):
        print('{"code":"maintenance_failed"}')
        raise SystemExit(2) from None
