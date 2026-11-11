"""Bounded refresh replay after a lost response (ADR-100).

Revision ID: 0024_refresh_replay_grace
Revises: 0023_window_renew_token

Adds two nullable columns to session_credentials. Existing rows keep NULL: a replay of a token
rotated before this migration has no successor link and still revokes its family, exactly as
before. The rollback drops both columns and restores strict one-time rotation.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0024_refresh_replay_grace"
down_revision: str | None = "0023_window_renew_token"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "session_credentials",
        sa.Column(
            "successor_id",
            sa.Uuid(),
            sa.ForeignKey(
                "session_credentials.id",
                name="fk_session_credentials_successor",
                ondelete="SET NULL",
            ),
        ),
    )
    op.add_column("session_credentials", sa.Column("first_used_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("session_credentials", "first_used_at")
    op.drop_constraint(
        "fk_session_credentials_successor", "session_credentials", type_="foreignkey"
    )
    op.drop_column("session_credentials", "successor_id")
