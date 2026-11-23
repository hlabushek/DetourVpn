"""TEST ONLY (H3, ADR-181): the hot-path index migration applies, rolls back and re-applies.

Real temporary PostgreSQL; no managed database.
"""

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from test_migrations import HEAD, migration_config

from vpn_api.db.connection import transaction

pytestmark = pytest.mark.postgres
NEW: set[tuple[str, str | None]] = {
    ("peer_window_allocations", "ix_peer_window_allocation_user"),
    ("peer_connections", "ix_peer_connection_live_runtime"),
}


def indexes(engine: Engine) -> set[tuple[str, str | None]]:
    found = set()
    for table, _ in NEW:
        found |= {(table, row["name"]) for row in inspect(engine).get_indexes(table)}
    return found & NEW


def test_index_migration_applies_rolls_back_and_matches_the_models(engine: Engine) -> None:
    config = migration_config(engine)
    command.upgrade(config, "head")
    assert HEAD == "0029_window_hot_path_indexes"
    assert indexes(engine) == NEW
    command.downgrade(config, "0028_window_not_opened_receipts")
    assert indexes(engine) == set()
    command.upgrade(config, "head")
    assert indexes(engine) == NEW
    command.check(config)  # models and migrations agree (no drift)
    with transaction(engine) as session:
        definition = session.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": "ix_peer_connection_live_runtime"},
        )
    assert definition is not None and "WHERE" in definition and "'closed'" in definition


def test_held_lots_filter_can_use_the_user_index(engine: Engine) -> None:
    config = migration_config(engine)
    command.upgrade(config, "head")
    with transaction(engine) as session:
        session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            session.scalars(
                text(
                    "EXPLAIN SELECT lot_id FROM peer_window_allocations "
                    "WHERE user_id = '00000000-0000-0000-0000-000000000001'"
                )
            )
        )
    assert "ix_peer_window_allocation_user" in plan
