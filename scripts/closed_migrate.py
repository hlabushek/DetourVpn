"""Explicit migrations using vpn_migrator; never run automatically from API startup."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402

from vpn_api.config import Settings  # noqa: E402
from vpn_api.db.connection import create_database_engine  # noqa: E402


def main() -> int:
    engine = None
    try:
        # The migration environment contains only the owner URL, no bot secrets.
        settings = Settings(_env_file=None)
        engine = create_database_engine(settings)
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_user")) != "vpn_migrator":
                raise ValueError("migration_role_required")
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["database_engine"] = engine
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text("REVOKE ALL ON alembic_version FROM vpn_api"))
            connection.execute(text("GRANT SELECT ON alembic_version TO vpn_api"))
            connection.execute(text("REVOKE UPDATE, DELETE ON quota_ledger FROM vpn_api"))
        command.check(config)
        print("closed_migration_passed")
        return 0
    except Exception:
        # Raw exceptions can include libpq/SQL secrets. Diagnose locally without printing them.
        print("closed_migration_failed", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
