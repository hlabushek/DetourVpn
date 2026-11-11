"""Allow pool peer indexes up to 250 as connection slot ids (ADR-101).

Revision ID: 0025_pool_peer_slots
Revises: 0024_refresh_replay_grace

Only the check constraint widens. The rollback refuses while any connection uses an index above
16, because the old constraint would then fail or hide those rows.
"""

from alembic import op

revision: str = "0025_pool_peer_slots"
down_revision: str | None = "0024_refresh_replay_grace"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("ck_peer_connection_slot", "peer_connections", type_="check")
    op.create_check_constraint(
        "ck_peer_connection_slot",
        "peer_connections",
        "slot_id BETWEEN 1 AND 250 AND generation > 0",
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE peer_connections IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM peer_connections WHERE slot_id > 16) THEN
            RAISE EXCEPTION 'Refusing downgrade: pool peer connections exist';
        END IF; END $$;""")
    op.drop_constraint("ck_peer_connection_slot", "peer_connections", type_="check")
    op.create_check_constraint(
        "ck_peer_connection_slot",
        "peer_connections",
        "slot_id BETWEEN 1 AND 16 AND generation > 0",
    )
