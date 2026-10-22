"""Disposable real PostgreSQL database per test; no external provider calls."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from psycopg import sql
from sqlalchemy import Engine

from vpn_api.config import Settings
from vpn_api.db.connection import create_database_engine


@pytest.fixture(autouse=True)
def isolate_initial_quota_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPN_INITIAL_QUOTA_BYTES", raising=False)


@pytest.fixture
def engine() -> Iterator[Engine]:
    raw_url = os.environ.get("VPN_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("VPN_TEST_DATABASE_URL is absent: real PostgreSQL tests not run")
    settings = Settings(_env_file=None, environment="test", database_url=raw_url)
    admin_engine = create_database_engine(settings)
    db_name = "vpn_i004_" + uuid4().hex
    # Only a newly created random database is ever dropped, never the supplied admin DB.
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)).as_string()
        )
    target_url = admin_engine.url.set(database=db_name)
    target = create_database_engine(
        Settings(
            _env_file=None, environment="test", database_url=target_url.render_as_string(False)
        )
    )
    try:
        yield target
    finally:
        target.dispose()
        try:
            with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                statement = sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name))
                conn.exec_driver_sql(statement.as_string())
        finally:
            admin_engine.dispose()
