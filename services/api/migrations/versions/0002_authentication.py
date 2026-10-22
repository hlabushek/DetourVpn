"""Local Telegram pairing, rate limits and opaque sessions."""

import sqlalchemy as sa
from alembic import op

revision: str = "0002_authentication"
down_revision: str | None = "0001_accounts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("role", sa.String(16), server_default=sa.text("'user'"), nullable=False)
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('user', 'admin')")
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("verifier_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.BigInteger()),
        sa.Column("code_digest", sa.String(64)),
        sa.Column("code_expires_at", sa.DateTime(timezone=True)),
        sa.Column("issued_at", sa.DateTime(timezone=True)),
        sa.Column("generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("checks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "checks BETWEEN 0 AND 5 AND generation >= 0", name="ck_login_attempts_budget"
        ),
        sa.CheckConstraint("subject IS NULL OR subject > 0", name="ck_login_attempts_subject"),
    )
    op.create_index("ix_login_attempts_expires_at", "login_attempts", ["expires_at"])
    op.create_index(
        "ix_login_attempts_subject_issued_at", "login_attempts", ["subject", "issued_at"]
    )
    op.create_table(
        "session_families",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_session_families_user_id", "session_families", ["user_id"])
    op.create_index("ix_session_families_expires_at", "session_families", ["expires_at"])
    op.create_table(
        "session_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "family_id",
            sa.Uuid(),
            sa.ForeignKey("session_families.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("access_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("refresh_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_session_credentials_family_id", "session_credentials", ["family_id"])
    op.create_table(
        "bot_updates",
        sa.Column("bot_id", sa.BigInteger(), primary_key=True),
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
    )
    op.create_index("ix_bot_updates_created_at", "bot_updates", ["created_at"])
    op.create_table(
        "code_issues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_code_issues_subject_created_at", "code_issues", ["subject", "created_at"])
    op.create_table(
        "rate_buckets",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_buckets_expires_at", "rate_buckets", ["expires_at"])


def downgrade() -> None:
    tables = [
        "login_attempts",
        "session_families",
        "session_credentials",
        "bot_updates",
        "code_issues",
        "rate_buckets",
    ]
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE users, " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE")
    # Keep populated authentication state intact; no force flag.
    for table in tables:
        op.execute(f"""DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table}) THEN
            RAISE EXCEPTION 'Refusing downgrade: authentication tables contain data';
            END IF; END $$;""")  # noqa: S608 - identifiers from the fixed list above
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM users WHERE role <> 'user') THEN
        RAISE EXCEPTION 'Refusing downgrade: privileged roles exist'; END IF; END $$;""")
    for table in [
        "rate_buckets",
        "code_issues",
        "bot_updates",
        "session_credentials",
        "session_families",
        "login_attempts",
    ]:
        op.drop_table(table)
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
