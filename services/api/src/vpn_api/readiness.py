"""Control-plane dependency readiness; never claims tunnel or WAN availability."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError


def database_ready(engine: Engine | None, *, least_privilege: bool) -> bool:
    if engine is None:
        return False
    try:
        root = Path(__file__).resolve().parents[4]
        config = Config(str(root / "alembic.ini"))
        heads = set(ScriptDirectory.from_config(config).get_heads())
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            revisions = set(connection.scalars(text("SELECT version_num FROM alembic_version")))
            if not heads or revisions != heads:
                return False
            if least_privilege:
                safe = connection.scalar(
                    text(
                        "SELECT current_user = 'vpn_api' AND NOT rolsuper AND NOT rolcreatedb "
                        "AND NOT rolcreaterole AND NOT rolbypassrls AND NOT rolreplication "
                        "AND NOT has_schema_privilege(current_user, 'public', 'CREATE') "
                        "AND NOT has_database_privilege("
                        "current_user, current_database(), 'CREATE') "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
                if safe is not True:
                    return False
            return connection.scalar(text("SELECT NOT pg_is_in_recovery()")) is True
    except (SQLAlchemyError, OSError):
        return False
