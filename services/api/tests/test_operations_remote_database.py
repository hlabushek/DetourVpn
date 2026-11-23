"""TEST ONLY (H6, ADR-180): backups and maintenance accept a managed database only with
verified TLS. Parsing and environment only; no database or network. Synthetic hosts.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from vpn_api import operations as ops

ROOT = Path(__file__).resolve().parents[3]
REMOTE = "postgresql+psycopg://vpn_api:TEST_ONLY@db.example.invalid/neondb"


@pytest.mark.parametrize(
    "query",
    [
        "",  # no explicit sslmode: refused here even though the API defaults to verify-full
        "?sslmode=require",
        "?sslmode=verify-ca",
        "?sslmode=disable",
        "?sslmode=prefer",
        "?sslmode=verify-full&hostaddr=192.0.2.1",
        "?sslmode=verify-full&options=-c%20x",
        "?sslmode=verify-full&channel_binding=prefer",
    ],
)
def test_remote_database_without_verified_tls_is_refused(query: str) -> None:
    with pytest.raises(ops.OperationsError, match="local_database_required"):
        ops.local_url(REMOTE + query)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://vpn_api:x@192.0.2.10/neondb?sslmode=verify-full",  # IP literal
        "postgresql+psycopg://vpn_api:x@dbhost/neondb?sslmode=verify-full",  # no dot
        "postgresql+psycopg://vpn_api:x@localhost/neondb?sslmode=verify-full",
    ],
)
def test_remote_host_rules_match_the_api_parser(url: str) -> None:
    with pytest.raises(ops.OperationsError, match="local_database_required"):
        ops.local_url(url)


def test_verify_full_remote_url_is_accepted_and_keeps_tls_settings() -> None:
    url = ops.local_url(REMOTE + "?sslmode=verify-full&channel_binding=require")
    assert (url.host, url.database, url.username) == ("db.example.invalid", "neondb", "vpn_api")
    assert dict(url.query) == {"sslmode": "verify-full", "channel_binding": "require"}
    env = ops.pg_environment(url)
    assert env["PGHOST"] == "db.example.invalid"
    assert env["PGSSLMODE"] == "verify-full" and env["PGCHANNELBINDING"] == "require"
    assert env["PGSSLROOTCERT"]
    assert "TEST_ONLY" not in " ".join(value for key, value in env.items() if key != "PGPASSWORD")


def test_loopback_rules_are_unchanged() -> None:
    url = ops.local_url("postgresql://operator@127.0.0.1/db")
    assert ops.pg_environment(url)["PGSSLMODE"] == "disable"
    with pytest.raises(ops.OperationsError, match="local_database_required"):
        ops.local_url("postgresql://operator@127.0.0.1/db?sslmode=verify-full")


def test_api_engine_url_carries_the_verify_full_it_was_built_with() -> None:
    # create_database_engine strips the query of a remote URL and passes sslmode separately.
    engine = create_engine("postgresql+psycopg://vpn_api:x@db.example.invalid/neondb")
    try:
        assert ops.local_url(ops.engine_source(engine)).query["sslmode"] == "verify-full"
    finally:
        engine.dispose()


def test_backup_and_maintenance_units_do_not_require_the_local_database() -> None:
    for name in ("detourvpn-beta-backup.service", "detourvpn-beta-maintenance.service"):
        text = (ROOT / "infra" / "beta" / name).read_text(encoding="utf-8")
        assert "Requires=detourvpn-beta-database.service" not in text
        assert "Wants=detourvpn-beta-database.service" in text
        assert "After=detourvpn-beta-database.service" in text
