"""The reliability read-out runs valid, read-only SQL and never prints raw identifiers."""

import pytest
from scripts.beta_reliability_logs import ACCESS, MASKS, SQL, mask
from sqlalchemy import text
from test_authentication import Harness, harness

__all__ = ["harness"]


def statements(hours: int) -> list[str]:
    body = "\n".join(
        line
        for line in SQL.format(hours=hours).splitlines()
        if not line.startswith("\\echo") and line not in ("BEGIN READ ONLY;", "ROLLBACK;")
    )
    return [part.strip() for part in body.split(";") if part.strip()]


def test_sql_is_wrapped_read_only() -> None:
    lines = SQL.format(hours=48).strip().splitlines()
    assert lines[0] == "BEGIN READ ONLY;" and lines[-1] == "ROLLBACK;"
    for statement in statements(48):
        assert statement.upper().startswith("SELECT"), statement[:40]


@pytest.mark.postgres
def test_every_query_runs_on_the_current_schema(harness: Harness) -> None:
    with harness.engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        for statement in statements(48):
            connection.execute(text(statement)).fetchall()
        connection.rollback()


def test_masking_removes_ids_addresses_and_tokens() -> None:
    line = (
        "2026-11-11T10:00:00+0300 host api: INFO: 203.0.113.7:51234 - "
        '"POST /api/v1/window-tunnels/3f2a9c1e-1111-4111-8111-111111111111/native-renew HTTP/1.1"'
        " 202 user@example.com " + "a" * 48
    )
    masked = mask(line)
    assert "203.0.113.7" not in masked and "3f2a9c1e-1111" not in masked
    assert "user@example.com" not in masked and "a" * 48 not in masked
    assert "<ip>" in masked and "<id>" in masked


def test_access_lines_reduce_to_time_method_path_status() -> None:
    line = (
        "2026-11-11T10:00:00+0300 host api: INFO: 203.0.113.7:51234 - "
        '"POST /api/v1/window-tunnels/3f2a9c1e-1111-4111-8111-111111111111/native-renew HTTP/1.1"'
        " 202 Accepted"
    )
    match = ACCESS.search(line)
    assert match is not None
    path = MASKS[0][0].sub("<id>", match["path"])
    assert (match["time"], match["method"], path, match["status"]) == (
        "2026-11-11T10:00:00+0300",
        "POST",
        "/api/v1/window-tunnels/<id>/native-renew",
        "202",
    )
