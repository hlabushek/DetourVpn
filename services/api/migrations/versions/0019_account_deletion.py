"""Add the deleted account tombstone state.

Revision ID: 0019_account_deletion
Revises: 0018_peer_window_resolution

Rollback: refuses while any deleted account exists, because the old status constraint cannot
represent it. Restore from backup or keep the schema; never rewrite deleted rows to 'disabled'.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0019_account_deletion"
down_revision: str | None = "0018_peer_window_resolution"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.create_check_constraint(
        "ck_users_status",
        "users",
        "status IN ('pending', 'active', 'disabled', 'deleted')",
    )
    op.create_check_constraint(
        "ck_users_deleted_at",
        "users",
        "(status = 'deleted') = (deleted_at IS NOT NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM users WHERE status = 'deleted' LIMIT 1")).first():
        raise RuntimeError("deleted accounts exist; downgrade would lose the tombstone state")
    op.drop_constraint("ck_users_deleted_at", "users", type_="check")
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.create_check_constraint(
        "ck_users_status", "users", "status IN ('pending', 'active', 'disabled')"
    )
    op.drop_column("users", "deleted_at")
