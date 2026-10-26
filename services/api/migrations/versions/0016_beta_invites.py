"""Pseudonymous closed-beta invite records with audited admin ownership."""

import sqlalchemy as sa
from alembic import op

revision: str = "0016_beta_invites"
down_revision: str | None = "0015_window_admission"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "beta_invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_digest", sa.String(length=64), nullable=False),
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "subject_digest ~ '^[0-9a-f]{64}$'", name="ck_beta_invites_subject_digest"
        ),
        sa.ForeignKeyConstraint(["admin_id"], ["admin_identities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_beta_invites"),
    )
    op.create_index(
        "uq_beta_invites_active_subject",
        "beta_invites",
        ["subject_digest"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_beta_invites_admin_id_created", "beta_invites", ["admin_id", "created_at"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM beta_invites)")):
        raise RuntimeError("beta_invites_data_requires_reviewed_forward_migration")
    op.drop_index("ix_beta_invites_admin_id_created", table_name="beta_invites")
    op.drop_index("uq_beta_invites_active_subject", table_name="beta_invites")
    op.drop_table("beta_invites")
