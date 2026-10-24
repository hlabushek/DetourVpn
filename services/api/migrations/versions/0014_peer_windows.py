"""Lot-pinned window funding and immutable final evidence (no node admission)."""

import sqlalchemy as sa
from alembic import op

revision: str = "0014_peer_windows"
down_revision: str | None = "0013_admin"
branch_labels: str | None = None
depends_on: str | None = None


def sources(include_windows: bool) -> None:
    values = "'registration', 'internal', 'usage', 'tunnel_lease', 'reward'"
    usage = "'usage', 'tunnel_lease'"
    if include_windows:
        values += ", 'peer_window'"
        usage += ", 'peer_window'"
    for table in ("quota_operations", "quota_ledger"):
        for suffix in ("source", "usage"):
            op.drop_constraint(f"ck_{table}_{suffix}", table)
        op.create_check_constraint(f"ck_{table}_source", table, f"source_type IN ({values})")
        op.create_check_constraint(
            f"ck_{table}_usage", table, f"source_type NOT IN ({usage}) OR kind = 'debit'"
        )


def upgrade() -> None:
    sources(True)
    op.create_table(
        "peer_window_holds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("amount_bytes", sa.BigInteger(), nullable=False),
        sa.Column("grant", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "user_id", name="uq_peer_window_hold_owner"),
        sa.UniqueConstraint("connection_id", "generation", name="uq_peer_window_generation"),
        sa.CheckConstraint("amount_bytes BETWEEN 65536 AND 67108864", name="ck_peer_window_bytes"),
        sa.CheckConstraint("generation > 0", name="ck_peer_window_generation"),
        sa.CheckConstraint("not_after > created_at", name="ck_peer_window_deadline"),
    )
    op.create_index("ix_peer_window_hold_user", "peer_window_holds", ["user_id"])
    op.create_table(
        "peer_window_allocations",
        sa.Column("window_id", sa.Uuid(), primary_key=True),
        sa.Column("lot_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("amount_bytes", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["window_id", "user_id"],
            ["peer_window_holds.id", "peer_window_holds.user_id"],
            name="fk_peer_window_allocation_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id", "user_id"],
            ["quota_funding_lots.id", "quota_funding_lots.user_id"],
            name="fk_peer_window_allocation_lot",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("window_id", "position", name="uq_peer_window_allocation_position"),
        sa.CheckConstraint("amount_bytes > 0 AND position >= 0", name="ck_peer_window_allocation"),
    )
    op.create_index("ix_peer_window_allocation_lot", "peer_window_allocations", ["lot_id"])
    op.create_table(
        "peer_window_receipts",
        sa.Column("window_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("observation", sa.String(16), nullable=False),
        sa.Column("observed_bytes", sa.BigInteger()),
        sa.Column("operation_id", sa.Uuid()),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["window_id", "user_id"],
            ["peer_window_holds.id", "peer_window_holds.user_id"],
            name="fk_peer_window_receipt_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "user_id"],
            ["quota_operations.id", "quota_operations.user_id"],
            name="fk_peer_window_receipt_operation",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("operation_id", name="uq_peer_window_receipt_operation"),
        sa.CheckConstraint(
            "(observation = 'unknown' AND observed_bytes IS NULL AND operation_id IS NULL) OR "
            "(observation = 'measured' AND observed_bytes IS NOT NULL "
            "AND observed_bytes BETWEEN 0 AND 67108864 AND "
            "((observed_bytes = 0 AND operation_id IS NULL) OR "
            "(observed_bytes > 0 AND operation_id IS NOT NULL)))",
            name="ck_peer_window_receipt_evidence",
        ),
    )
    for table in ("peer_window_holds", "peer_window_allocations", "peer_window_receipts"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE users, quota_operations, quota_ledger, peer_window_holds, "
        "peer_window_allocations, peer_window_receipts IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM peer_window_holds)
        OR EXISTS(SELECT 1 FROM quota_operations WHERE source_type='peer_window') THEN
            RAISE EXCEPTION 'Refusing downgrade: peer window history exists';
        END IF;
    END $$""")
    for table in ("peer_window_receipts", "peer_window_allocations", "peer_window_holds"):
        op.drop_table(table)
    sources(False)
