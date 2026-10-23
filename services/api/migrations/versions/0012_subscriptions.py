"""Versioned manual subscriptions and source-aware quota funding; no payments."""

from alembic import op

revision: str = "0012_subscriptions"
down_revision: str | None = "0011_referrals"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE quota_operations ADD COLUMN unavailable_before_bytes BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE quota_operations ADD CONSTRAINT ck_quota_operations_unavailable "
        "CHECK (unavailable_before_bytes >= 0)"
    )
    op.execute(
        """CREATE TABLE quota_funding_lots (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    granted_bytes BIGINT NOT NULL,
    initial_remaining_bytes BIGINT NOT NULL,
    remaining_bytes BIGINT NOT NULL,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_quota_funding_lots PRIMARY KEY (id),
    CONSTRAINT fk_quota_funding_owner FOREIGN KEY(id, user_id) REFERENCES
quota_operations (id, user_id) ON DELETE RESTRICT,
    CONSTRAINT uq_quota_funding_id_user UNIQUE (id, user_id),
    CONSTRAINT ck_quota_funding_bytes CHECK (granted_bytes >= initial_remaining_bytes
AND initial_remaining_bytes >= remaining_bytes AND remaining_bytes >= 0),
    CONSTRAINT ck_quota_funding_window CHECK (expires_at IS NULL OR expires_at >
starts_at),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
)"""
    )
    op.execute(
        """CREATE INDEX ix_quota_funding_user_window ON quota_funding_lots (user_id, expires_at,
starts_at)"""
    )
    op.execute(
        """CREATE TABLE quota_funding_uses (
    operation_id UUID NOT NULL,
    lot_id UUID NOT NULL,
    user_id UUID NOT NULL,
    amount_bytes BIGINT NOT NULL,
    CONSTRAINT pk_quota_funding_uses PRIMARY KEY (operation_id, lot_id),
    CONSTRAINT fk_quota_funding_use_operation FOREIGN KEY(operation_id, user_id)
REFERENCES quota_operations (id, user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_quota_funding_use_lot FOREIGN KEY(lot_id, user_id) REFERENCES
quota_funding_lots (id, user_id) ON DELETE RESTRICT,
    CONSTRAINT ck_quota_funding_use_bytes CHECK (amount_bytes > 0),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
)"""
    )
    op.execute("CREATE INDEX ix_quota_funding_uses_lot ON quota_funding_uses (lot_id)")
    op.execute(
        """CREATE TABLE plan_revisions (
    id UUID NOT NULL,
    code VARCHAR(32) NOT NULL,
    version INTEGER NOT NULL,
    name VARCHAR(80) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    price_minor BIGINT NOT NULL,
    currency VARCHAR(3) DEFAULT 'RUB' NOT NULL,
    quota_bytes BIGINT NOT NULL,
    period_days INTEGER NOT NULL,
    idempotency_key UUID NOT NULL,
    actor VARCHAR(128) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_plan_revisions PRIMARY KEY (id),
    CONSTRAINT uq_plan_revisions_code_version UNIQUE (code, version),
    CONSTRAINT uq_plan_revisions_key UNIQUE (idempotency_key),
    CONSTRAINT ck_plan_revisions_amounts CHECK (version > 0 AND price_minor >= 0 AND
quota_bytes > 0),
    CONSTRAINT ck_plan_revisions_period CHECK (period_days BETWEEN 1 AND 366),
    CONSTRAINT ck_plan_revisions_kind CHECK (kind IN ('subscription', 'addon') AND
currency = 'RUB')
)"""
    )
    op.execute(
        """CREATE TABLE subscriptions (
    id UUID NOT NULL,
    user_id UUID NOT NULL,
    plan_revision_id UUID NOT NULL,
    funding_id UUID NOT NULL,
    idempotency_key UUID NOT NULL,
    basis VARCHAR(32) DEFAULT 'admin_grant' NOT NULL,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    actor VARCHAR(128) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_subscriptions PRIMARY KEY (id),
    CONSTRAINT fk_subscriptions_funding_owner FOREIGN KEY(funding_id, user_id)
REFERENCES quota_funding_lots (id, user_id) ON DELETE RESTRICT,
    CONSTRAINT uq_subscriptions_user_key UNIQUE (user_id, idempotency_key),
    CONSTRAINT uq_subscriptions_funding UNIQUE (funding_id),
    CONSTRAINT ck_subscriptions_window CHECK (ends_at > starts_at),
    CONSTRAINT ck_subscriptions_basis CHECK (basis = 'admin_grant'),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT,
    FOREIGN KEY(plan_revision_id) REFERENCES plan_revisions (id) ON DELETE RESTRICT
)"""
    )
    op.execute("CREATE INDEX ix_subscriptions_user_window ON subscriptions (user_id, ends_at)")
    op.execute(
        """CREATE TABLE subscription_revocations (
    subscription_id UUID NOT NULL,
    idempotency_key UUID NOT NULL,
    actor VARCHAR(128) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_subscription_revocations PRIMARY KEY (subscription_id),
    FOREIGN KEY(subscription_id) REFERENCES subscriptions (id) ON DELETE RESTRICT,
    UNIQUE (idempotency_key)
)"""
    )
    op.execute("""INSERT INTO quota_funding_lots
        (id,user_id,granted_bytes,initial_remaining_bytes,remaining_bytes,starts_at)
        SELECT operation_id,user_id,delta_bytes,remaining,remaining,created_at FROM (
            SELECT l.operation_id,l.user_id,l.delta_bytes,l.created_at,
                GREATEST(0, LEAST(l.delta_bytes,
                    SUM(l.delta_bytes) OVER (PARTITION BY l.user_id ORDER BY l.sequence)
                    - COALESCE((SELECT SUM(-d.delta_bytes) FROM quota_ledger d
                        WHERE d.user_id=l.user_id AND d.kind='debit'),0)))::BIGINT AS remaining
            FROM quota_ledger l WHERE l.kind='credit'
        ) legacy""")
    for table in (
        "quota_funding_uses",
        "plan_revisions",
        "subscriptions",
        "subscription_revocations",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()"
        )
    op.execute("""CREATE FUNCTION guard_quota_funding() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'UPDATE' THEN
                RAISE EXCEPTION 'Funding history cannot be removed';
            END IF;
            IF ROW(NEW.id, NEW.user_id, NEW.granted_bytes, NEW.initial_remaining_bytes,
                   NEW.starts_at, NEW.expires_at) IS DISTINCT FROM
               ROW(OLD.id, OLD.user_id, OLD.granted_bytes, OLD.initial_remaining_bytes,
                   OLD.starts_at, OLD.expires_at) OR NEW.remaining_bytes > OLD.remaining_bytes OR
               (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
                RAISE EXCEPTION 'Funding terms are immutable';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER quota_funding_guard BEFORE UPDATE OR DELETE
        ON quota_funding_lots FOR EACH ROW EXECUTE FUNCTION guard_quota_funding()""")
    op.execute("""CREATE TRIGGER quota_funding_no_truncate BEFORE TRUNCATE
        ON quota_funding_lots FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()""")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE users, quota_operations, quota_ledger, quota_funding_lots, "
        "quota_funding_uses, plan_revisions, subscriptions, subscription_revocations "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM quota_funding_lots) OR EXISTS(SELECT 1 FROM plan_revisions)
        OR EXISTS(SELECT 1 FROM subscriptions) THEN
            RAISE EXCEPTION 'Refusing downgrade: subscription or funding history exists';
        END IF;
    END $$""")
    for table in (
        "subscription_revocations",
        "subscriptions",
        "plan_revisions",
        "quota_funding_uses",
        "quota_funding_lots",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION guard_quota_funding()")
    op.execute("ALTER TABLE quota_operations DROP CONSTRAINT ck_quota_operations_unavailable")
    op.execute("ALTER TABLE quota_operations DROP COLUMN unavailable_before_bytes")
