"""Connection-scoped native renewal token digest (ADR-097).

Revision ID: 0023_window_renew_token
Revises: 0022_device_platform_windows

Adds one nullable column. Existing connections keep NULL and simply cannot be renewed natively;
nothing is rewritten. The rollback drops the column, which only disables native renewal: clients
fall back to app-driven renewal with the user access token.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0023_window_renew_token"
down_revision: str | None = "0022_device_platform_windows"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("peer_connections", sa.Column("renew_token_digest", sa.String(64)))
    op.create_check_constraint(
        "ck_peer_connection_renew_token_digest",
        "peer_connections",
        "renew_token_digest IS NULL OR renew_token_digest ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_peer_connection_renew_token_digest", "peer_connections", type_="check")
    op.drop_column("peer_connections", "renew_token_digest")
