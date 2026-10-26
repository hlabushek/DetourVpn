"""Persist privacy-minimal Telegram OTP delivery outcomes.

Revision ID: 0017_login_delivery_receipt
Revises: 0016_beta_invites
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0017_login_delivery_receipt"
down_revision: str | None = "0016_beta_invites"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("login_attempts", sa.Column("delivery_status", sa.String(16), nullable=True))
    op.add_column(
        "login_attempts",
        sa.Column("delivery_recorded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_login_attempts_delivery_status",
        "login_attempts",
        "delivery_status IS NULL OR delivery_status IN ('pending', 'api_accepted', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_login_attempts_delivery_status", "login_attempts", type_="check")
    op.drop_column("login_attempts", "delivery_recorded_at")
    op.drop_column("login_attempts", "delivery_status")
