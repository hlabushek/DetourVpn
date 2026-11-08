"""Auditable full-budget compensation for immutable unknown peer windows."""

import sqlalchemy as sa
from alembic import op

revision: str = "0018_peer_window_resolution"
down_revision: str | None = "0017_login_delivery_receipt"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("quota_operations", "quota_ledger"):
        op.drop_constraint(f"ck_{table}_source", table)
        op.drop_constraint(f"ck_{table}_usage", table)
        op.create_check_constraint(
            f"ck_{table}_source",
            table,
            "source_type IN ('registration', 'internal', 'usage', 'tunnel_lease', "
            "'reward', 'peer_window', 'peer_window_resolution')",
        )
        op.create_check_constraint(
            f"ck_{table}_usage",
            table,
            "source_type NOT IN ('usage', 'tunnel_lease', 'peer_window', "
            "'peer_window_resolution') OR kind = 'debit'",
        )
    op.create_table(
        "peer_window_resolutions",
        sa.Column("window_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("amount_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("window_id", name="pk_peer_window_resolutions"),
        sa.ForeignKeyConstraint(
            ["window_id", "user_id"],
            ["peer_window_holds.id", "peer_window_holds.user_id"],
            name="fk_peer_window_resolution_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_peer_window_resolution_operation_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin_identities.id"],
            name="fk_peer_window_resolution_admin",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("operation_id", name="uq_peer_window_resolution_operation"),
        sa.UniqueConstraint("idempotency_key", name="uq_peer_window_resolution_key"),
        sa.CheckConstraint("kind = 'charge_full_budget'", name="ck_peer_window_resolution_kind"),
        sa.CheckConstraint("amount_bytes > 0", name="ck_peer_window_resolution_amount"),
        sa.CheckConstraint("length(trim(reason)) >= 3", name="ck_peer_window_resolution_reason"),
        sa.CheckConstraint(
            "evidence_digest ~ '^[0-9a-f]{64}$'", name="ck_peer_window_resolution_digest"
        ),
    )
    op.create_index(
        "ix_peer_window_resolution_admin_created",
        "peer_window_resolutions",
        ["admin_id", "created_at"],
    )
    op.execute(
        "CREATE TRIGGER peer_window_resolutions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
        "ON peer_window_resolutions FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE quota_operations, quota_ledger, peer_window_resolutions "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM peer_window_resolutions)
        OR EXISTS(SELECT 1 FROM quota_operations WHERE source_type='peer_window_resolution') THEN
            RAISE EXCEPTION 'Refusing downgrade: peer window resolution history exists';
        END IF;
    END $$""")
    op.drop_table("peer_window_resolutions")
    for table in ("quota_operations", "quota_ledger"):
        op.drop_constraint(f"ck_{table}_source", table)
        op.drop_constraint(f"ck_{table}_usage", table)
        op.create_check_constraint(
            f"ck_{table}_source",
            table,
            "source_type IN ('registration', 'internal', 'usage', 'tunnel_lease', "
            "'reward', 'peer_window')",
        )
        op.create_check_constraint(
            f"ck_{table}_usage",
            table,
            "source_type NOT IN ('usage', 'tunnel_lease', 'peer_window') OR kind = 'debit'",
        )
