"""Host-side database step for the managed-database API deploy (ADR-197).

Run only by scripts/deploy_beta_api_latest.py --database managed, on the beta host, under
systemd-run with the API's own environment file and from the API's checkout directory. The engine
comes from the API's own Settings and create_database_engine (TLS verify-full, ADR-166), so no
database URL is passed on a command line or written anywhere.

Commands (each prints one line of fixed tokens; never exception text, which can name the host):
  revision           current alembic revision(s), comma separated, or "none"
  can-migrate        "yes" if the API role can act as owner of every table in schema public
  upgrade <target>   alembic upgrade to exactly <target>, then alembic check
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET = re.compile(r"[0-9]{4}_[a-z0-9_]{1,80}")
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "120s"
NOT_OWNED = (
    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
    "AND NOT pg_has_role(current_user, c.relowner, 'USAGE')"
)


def valid_target(value: str) -> bool:
    return TARGET.fullmatch(value) is not None


def _engine(root: Path):  # noqa: ANN202 - imported lazily from the host's API checkout
    from sqlalchemy import event

    from vpn_api.config import Settings
    from vpn_api.db.connection import create_database_engine

    engine = create_database_engine(Settings(_env_file=None))

    @event.listens_for(engine, "connect")
    def _timeouts(dbapi_connection, _record) -> None:  # noqa: ANN001
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
            cursor.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
        dbapi_connection.commit()

    return engine


def run(argv: list[str], root: Path) -> tuple[int, str]:
    if not argv or argv[0] not in {"revision", "can-migrate", "upgrade"}:
        return 2, "managed_db_step_usage"
    if argv[0] == "upgrade" and (len(argv) != 2 or not valid_target(argv[1])):
        return 2, "managed_db_step_bad_target"
    if not (root / "alembic.ini").is_file():
        return 2, "managed_db_step_wrong_directory"
    sys.path.insert(0, str(root / "services" / "api" / "src"))
    stage = "connect"
    engine = None
    try:
        from sqlalchemy import text

        engine = _engine(root)
        if argv[0] == "revision":
            stage = "revision"
            with engine.connect() as connection:
                rows = connection.execute(text("SELECT version_num FROM alembic_version"))
                found = sorted(str(r[0]) for r in rows)
            return 0, ",".join(found) or "none"
        if argv[0] == "can-migrate":
            stage = "role"
            with engine.connect() as connection:
                missing = connection.scalar(text(NOT_OWNED))
            return 0, "yes" if missing == 0 else "no"
        from alembic import command
        from alembic.config import Config

        config = Config(str(root / "alembic.ini"))
        config.attributes["database_engine"] = engine
        stage = "upgrade"
        command.upgrade(config, argv[1])
        stage = "check"
        command.check(config)
        return 0, "managed_migration_passed"
    except Exception as error:  # noqa: BLE001 - only the class name is safe to print
        return 1, f"managed_migration_failed stage={stage} error={type(error).__name__}"
    finally:
        if engine is not None:
            engine.dispose()


def main() -> int:
    code, line = run(sys.argv[1:], Path.cwd())
    print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
