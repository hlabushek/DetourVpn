"""Bounded referral campaigns; no seeded codes or economic defaults."""

from alembic import op

revision: str = "0011_referrals"
down_revision: str | None = "0010_mini_app"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Frozen schema; never import live ORM models into a migration.
    op.execute("""CREATE TABLE referral_campaigns (
        id UUID CONSTRAINT pk_referral_campaigns PRIMARY KEY,
        reward_bytes BIGINT NOT NULL, budget_bytes BIGINT NOT NULL,
        inviter_cap INTEGER NOT NULL, enabled BOOLEAN NOT NULL DEFAULT false,
        starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT ck_referral_campaigns_bytes CHECK (
            reward_bytes > 0 AND budget_bytes >= reward_bytes),
        CONSTRAINT ck_referral_campaigns_cap CHECK (inviter_cap > 0),
        CONSTRAINT ck_referral_campaigns_window CHECK (ends_at > starts_at)
    )""")
    op.execute("""CREATE TABLE referral_codes (
        user_id UUID CONSTRAINT pk_referral_codes PRIMARY KEY
            REFERENCES users(id) ON DELETE RESTRICT,
        code VARCHAR(24) NOT NULL CONSTRAINT uq_referral_codes_code UNIQUE
    )""")
    op.execute("""CREATE TABLE referrals (
        id UUID CONSTRAINT pk_referrals PRIMARY KEY,
        inviter_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        invitee_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        campaign_id UUID NOT NULL REFERENCES referral_campaigns(id) ON DELETE RESTRICT,
        idempotency_key UUID NOT NULL, reward_bytes BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_referrals_invitee UNIQUE (invitee_id),
        CONSTRAINT uq_referrals_id_inviter UNIQUE (id, inviter_id),
        CONSTRAINT ck_referrals_no_self CHECK (inviter_id <> invitee_id),
        CONSTRAINT ck_referrals_bytes CHECK (reward_bytes > 0)
    )""")
    op.execute("CREATE INDEX ix_referrals_inviter_created ON referrals(inviter_id,created_at,id)")
    op.execute("CREATE INDEX ix_referrals_campaign ON referrals(campaign_id)")
    op.execute("""CREATE TABLE referral_awards (
        referral_id UUID CONSTRAINT pk_referral_awards PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        campaign_id UUID NOT NULL REFERENCES referral_campaigns(id) ON DELETE RESTRICT,
        operation_id UUID NOT NULL, idempotency_key UUID NOT NULL,
        reward_bytes BIGINT NOT NULL, balance_after_bytes BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT fk_referral_awards_owner FOREIGN KEY(referral_id,user_id)
            REFERENCES referrals(id,inviter_id) ON DELETE RESTRICT,
        CONSTRAINT fk_referral_awards_operation FOREIGN KEY(operation_id,user_id)
            REFERENCES quota_operations(id,user_id) ON DELETE RESTRICT,
        CONSTRAINT uq_referral_awards_operation UNIQUE(operation_id),
        CONSTRAINT uq_referral_awards_user_key UNIQUE(user_id,idempotency_key),
        CONSTRAINT ck_referral_awards_bytes CHECK (
            reward_bytes > 0 AND balance_after_bytes >= reward_bytes)
    )""")
    op.execute(
        "CREATE INDEX ix_referral_awards_campaign_user ON referral_awards(campaign_id,user_id)"
    )
    for table in ("referrals", "referral_awards", "referral_codes"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()"
        )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE users, referral_campaigns, referral_codes, referrals, referral_awards, "
        "quota_operations, quota_ledger IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM referrals) OR EXISTS (SELECT 1 FROM referral_codes) OR
           EXISTS (SELECT 1 FROM referral_awards) THEN
            RAISE EXCEPTION 'Refusing downgrade: referral records exist';
        END IF; END $$""")
    for table in ("referral_awards", "referrals", "referral_codes", "referral_campaigns"):
        op.drop_table(table)
