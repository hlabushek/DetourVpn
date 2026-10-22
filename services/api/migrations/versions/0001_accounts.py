"""Initial local account storage, no authentication or session implementation."""

import sqlalchemy as sa
from alembic import op

revision: str = "0001_accounts"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("locale", sa.String(16), server_default=sa.text("'ru'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'active', 'disabled')", name="ck_users_status"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_table(
        "auth_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provider", sa.String(32), server_default=sa.text("'telegram_bot'"), nullable=False
        ),
        sa.Column("provider_subject", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("provider = 'telegram_bot'", name="ck_auth_identities_provider"),
        sa.CheckConstraint("provider_subject > 0", name="ck_auth_identities_subject"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_identities_user_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_identities"),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identities_provider_subject"
        ),
        sa.UniqueConstraint("user_id", "provider", name="uq_auth_identities_user_provider"),
    )


def downgrade() -> None:
    # Lock before the emptiness check: a concurrent writer must not slip between
    # the check and DROP. Fail after a bounded wait instead of blocking indefinitely.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE users, auth_identities IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM auth_identities) THEN
            RAISE EXCEPTION 'Refusing downgrade: account tables contain data';
        END IF;
        END $$;"""
    )
    op.drop_table("auth_identities")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_table("users")
