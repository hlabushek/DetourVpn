"""Telegram username on the Telegram identity, for admin search (ADR-110).

Revision ID: 0026_telegram_username
Revises: 0025_pool_peer_slots

Adds one nullable column. Existing identities keep NULL until their next Mini App login.
Rollback drops the column: stored usernames are lost (Telegram sends them again on the next
login after a re-upgrade); no other data depends on it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0026_telegram_username"
down_revision: str | None = "0025_pool_peer_slots"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("auth_identities", sa.Column("telegram_username", sa.String(32)))
    op.create_check_constraint(
        "ck_auth_identities_telegram_username",
        "auth_identities",
        "telegram_username IS NULL OR telegram_username ~ '^[A-Za-z0-9_]{4,32}$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_auth_identities_telegram_username", "auth_identities", type_="check")
    op.drop_column("auth_identities", "telegram_username")
