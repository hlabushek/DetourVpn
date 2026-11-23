"""Accept the "not_opened" window receipt (ADR-179).

Revision ID: 0028_window_not_opened_receipts
Revises: 0027_panel_server_sources

A node reports "not_opened" when the gate for a grant was never opened: nothing passed, nothing
is charged, and the receipt releases the budget hold. Only the evidence check constraint
changes. Rollback refuses while any "not_opened" receipt exists, because the old constraint
cannot hold it and receipts are immutable.
"""

from alembic import op

revision: str = "0028_window_not_opened_receipts"
down_revision: str | None = "0027_panel_server_sources"
branch_labels: str | None = None
depends_on: str | None = None

OLD = (
    "(observation = 'unknown' AND observed_bytes IS NULL AND operation_id IS NULL) OR "
    "(observation = 'measured' AND observed_bytes IS NOT NULL "
    "AND observed_bytes BETWEEN 0 AND 67108864 AND "
    "((observed_bytes = 0 AND operation_id IS NULL) OR "
    "(observed_bytes > 0 AND operation_id IS NOT NULL)))"
)
NEW = (
    "(observation IN ('unknown', 'not_opened') AND observed_bytes IS NULL "
    "AND operation_id IS NULL) OR "
    "(observation = 'measured' AND observed_bytes IS NOT NULL "
    "AND observed_bytes BETWEEN 0 AND 67108864 AND "
    "((observed_bytes = 0 AND operation_id IS NULL) OR "
    "(observed_bytes > 0 AND operation_id IS NOT NULL)))"
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint("ck_peer_window_receipt_evidence", "peer_window_receipts", type_="check")
    op.create_check_constraint("ck_peer_window_receipt_evidence", "peer_window_receipts", NEW)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM peer_window_receipts WHERE observation = 'not_opened') THEN
                RAISE EXCEPTION 'not_opened receipts exist; rollback would rewrite evidence';
            END IF;
        END $$"""
    )
    op.drop_constraint("ck_peer_window_receipt_evidence", "peer_window_receipts", type_="check")
    op.create_check_constraint("ck_peer_window_receipt_evidence", "peer_window_receipts", OLD)
