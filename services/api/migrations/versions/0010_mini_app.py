"""Restricted Mini App sessions and durable initData replay protection."""

from alembic import op

revision: str = "0010_mini_app"
down_revision: str | None = "0009_rewards"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE session_families ADD COLUMN client_kind VARCHAR(16) NOT NULL DEFAULT 'native'"
    )
    op.execute(
        "CREATE TABLE mini_app_exchanges (digest VARCHAR(64) "
        "CONSTRAINT pk_mini_app_exchanges PRIMARY KEY, expires_at TIMESTAMPTZ NOT NULL)"
    )
    op.create_index("ix_mini_app_exchanges_expires_at", "mini_app_exchanges", ["expires_at"])
    op.create_check_constraint(
        "ck_session_families_client_kind",
        "session_families",
        "client_kind IN ('native', 'mini_app')",
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE session_families, mini_app_exchanges IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM session_families WHERE client_kind = 'mini_app') OR
           EXISTS (SELECT 1 FROM mini_app_exchanges) THEN
            RAISE EXCEPTION 'Refusing downgrade: Mini App sessions or replay records exist';
        END IF; END $$""")
    op.drop_table("mini_app_exchanges")
    op.drop_constraint("ck_session_families_client_kind", "session_families", type_="check")
    op.drop_column("session_families", "client_kind")
