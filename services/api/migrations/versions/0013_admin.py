"""Separate admin identities, MFA sessions and immutable action audit."""

from alembic import op

revision: str = "0013_admin"
down_revision: str | None = "0012_subscriptions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE admin_identities (
    id UUID NOT NULL,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(512) NOT NULL,
    totp_ciphertext VARCHAR(512) NOT NULL,
    role VARCHAR(16) NOT NULL,
    last_totp_step BIGINT DEFAULT -1 NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    disabled_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_admin_identities PRIMARY KEY (id),
    CONSTRAINT ck_admin_identities_role CHECK (role IN ('admin', 'operator')),
    UNIQUE (username)
)""")
    op.execute("""CREATE TABLE admin_sessions (
    id UUID NOT NULL,
    admin_id UUID NOT NULL,
    token_digest VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_admin_sessions PRIMARY KEY (id),
    FOREIGN KEY(admin_id) REFERENCES admin_identities (id) ON DELETE RESTRICT,
    UNIQUE (token_digest)
)""")
    op.execute("CREATE INDEX ix_admin_sessions_expiry ON admin_sessions (expires_at)")
    op.execute("""CREATE TABLE admin_actions (
    id UUID NOT NULL,
    admin_id UUID NOT NULL,
    user_id UUID,
    operation VARCHAR(32) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    idempotency_key UUID NOT NULL,
    request_digest VARCHAR(64) NOT NULL,
    result JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_admin_actions PRIMARY KEY (id),
    FOREIGN KEY(admin_id) REFERENCES admin_identities (id) ON DELETE RESTRICT,
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT,
    UNIQUE (idempotency_key)
)""")
    op.execute("CREATE INDEX ix_admin_actions_created ON admin_actions (created_at, id)")
    op.execute("""CREATE TRIGGER admin_actions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON admin_actions FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()""")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE admin_identities, admin_sessions, admin_actions IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM admin_identities) OR EXISTS(SELECT 1 FROM admin_actions) THEN
            RAISE EXCEPTION 'Refusing downgrade: admin history exists';
        END IF;
    END $$""")
    for table in ("admin_actions", "admin_sessions", "admin_identities"):
        op.drop_table(table)
