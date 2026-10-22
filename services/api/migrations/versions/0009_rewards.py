"""Atomic reward claims; deliberately no economic defaults."""

from alembic import op

revision: str = "0009_rewards"
down_revision: str | None = "0008_tunnel_admission"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("quota_operations", "quota_ledger"):
        op.drop_constraint(f"ck_{table}_source", table, type_="check")
        op.create_check_constraint(
            f"ck_{table}_source",
            table,
            "source_type IN ('registration', 'internal', 'usage', 'tunnel_lease', 'reward')",
        )
        op.create_check_constraint(
            f"ck_{table}_reward", table, "source_type <> 'reward' OR kind = 'credit'"
        )
    op.execute("""CREATE TABLE reward_rules (
        id UUID CONSTRAINT pk_reward_rules PRIMARY KEY,
        reward_type VARCHAR(16) NOT NULL,
        reward_bytes BIGINT NOT NULL,
        daily_cap_bytes BIGINT NOT NULL,
        daily_cap_claims INTEGER NOT NULL,
        cooldown_seconds INTEGER NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT false,
        starts_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_reward_rules_type UNIQUE(reward_type),
        CONSTRAINT ck_reward_rules_type CHECK (reward_type IN ('daily','clicker')),
        CONSTRAINT ck_reward_rules_bytes CHECK (
            reward_bytes > 0 AND daily_cap_bytes >= reward_bytes),
        CONSTRAINT ck_reward_rules_limits CHECK (daily_cap_claims > 0 AND cooldown_seconds >= 1),
        CONSTRAINT ck_reward_rules_daily CHECK (reward_type <> 'daily' OR daily_cap_claims = 1),
        CONSTRAINT ck_reward_rules_window CHECK (ends_at > starts_at)
    )""")
    op.execute("""CREATE TABLE reward_claims (
        id UUID CONSTRAINT pk_reward_claims PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        rule_id UUID NOT NULL REFERENCES reward_rules(id) ON DELETE RESTRICT,
        operation_id UUID NOT NULL,
        idempotency_key UUID NOT NULL,
        reward_type VARCHAR(16) NOT NULL,
        reward_bytes BIGINT NOT NULL, balance_after_bytes BIGINT NOT NULL,
        reward_day DATE NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_reward_claims_user_key UNIQUE(user_id,idempotency_key),
        CONSTRAINT uq_reward_claims_operation UNIQUE(operation_id),
        CONSTRAINT fk_reward_claims_operation_owner FOREIGN KEY(operation_id,user_id)
            REFERENCES quota_operations(id,user_id) ON DELETE RESTRICT,
        CONSTRAINT ck_reward_claims_type CHECK (reward_type IN ('daily','clicker')),
        CONSTRAINT ck_reward_claims_bytes CHECK (
            reward_bytes > 0 AND balance_after_bytes >= reward_bytes),
        CONSTRAINT ck_reward_claims_day CHECK (reward_day = (created_at AT TIME ZONE 'UTC')::date)
    )""")
    op.execute(
        "CREATE INDEX ix_reward_claims_owner_type_day "
        "ON reward_claims(user_id,reward_type,reward_day,created_at)"
    )
    op.execute(
        "CREATE INDEX ix_reward_claims_owner_type_time "
        "ON reward_claims(user_id,reward_type,created_at)"
    )
    op.execute("""CREATE TRIGGER reward_claims_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON reward_claims
        FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()""")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE users, reward_rules, reward_claims, quota_operations, quota_ledger "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM reward_claims) OR
           EXISTS (SELECT 1 FROM quota_operations WHERE source_type = 'reward') THEN
            RAISE EXCEPTION 'Refusing downgrade: reward accounting exists';
        END IF; END $$""")
    op.drop_table("reward_claims")
    op.drop_table("reward_rules")
    for table in ("quota_operations", "quota_ledger"):
        op.drop_constraint(f"ck_{table}_reward", table, type_="check")
        op.drop_constraint(f"ck_{table}_source", table, type_="check")
        op.create_check_constraint(
            f"ck_{table}_source",
            table,
            "source_type IN ('registration', 'internal', 'usage', 'tunnel_lease')",
        )
