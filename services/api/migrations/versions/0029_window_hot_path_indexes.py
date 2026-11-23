"""Indexes for the window hot paths (ADR-181).

Revision ID: 0029_window_hot_path_indexes
Revises: 0028_window_not_opened_receipts

- peer_window_allocations(user_id): `LedgerService.held_lots` filtered by user without an
  index, i.e. scanned every user's whole allocation history on each create, renew and settle.
- peer_connections(node_id, runtime_id) WHERE state <> 'closed': the live-connection half of
  `runtime_ready` (other-runtime connections that are not closed).

Plain CREATE INDEX (not CONCURRENTLY): Alembic runs each migration in a transaction. On a
large table this briefly blocks writes to it; the beta tables are small. Rollback drops both.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0029_window_hot_path_indexes"
down_revision: str | None = "0028_window_not_opened_receipts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_index("ix_peer_window_allocation_user", "peer_window_allocations", ["user_id"])
    op.create_index(
        "ix_peer_connection_live_runtime",
        "peer_connections",
        ["node_id", "runtime_id"],
        postgresql_where=sa.text("state <> 'closed'"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("ix_peer_connection_live_runtime", table_name="peer_connections")
    op.drop_index("ix_peer_window_allocation_user", table_name="peer_window_allocations")
