"""Frozen quota ledger/receipts schema, append-only guards and safe rollback."""

from alembic import op

revision: str = "0004_quota_ledger"
down_revision: str | None = "0003_devices"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE quota_operations (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        idempotency_key UUID NOT NULL,
        kind VARCHAR(16) NOT NULL,
        amount_bytes BIGINT NOT NULL,
        source_type VARCHAR(32) NOT NULL,
        source_id UUID NOT NULL,
        status VARCHAR(16) NOT NULL,
        error_code VARCHAR(32),
        balance_after_bytes BIGINT NOT NULL,
        revision_before BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_quota_operations PRIMARY KEY (id),
        CONSTRAINT uq_quota_operations_id_user UNIQUE (id, user_id),
        CONSTRAINT uq_quota_operations_user_key UNIQUE (user_id, idempotency_key),
        CONSTRAINT uq_quota_operations_source UNIQUE (user_id, source_type, source_id),
        CONSTRAINT ck_quota_operations_kind CHECK (kind IN ('credit', 'debit')),
        CONSTRAINT ck_quota_operations_source CHECK (source_type IN ('registration',
'internal')),
        CONSTRAINT ck_quota_operations_amount CHECK (amount_bytes >= 0 AND (amount_bytes > 0 OR
(source_type = 'registration' AND kind = 'credit'))),
        CONSTRAINT ck_quota_operations_registration CHECK (source_type <> 'registration' OR
(kind = 'credit' AND source_id = user_id AND idempotency_key = user_id)),
        CONSTRAINT ck_quota_operations_outcome CHECK ((status = 'applied' AND error_code IS
NULL) OR (status = 'rejected' AND error_code IS NOT NULL AND error_code IN
('insufficient_quota', 'quota_capacity_exceeded'))),
        CONSTRAINT ck_quota_operations_balance CHECK (balance_after_bytes >= 0),
        CONSTRAINT ck_quota_operations_revision CHECK (revision_before >= 0),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
)""")
    op.execute("""CREATE INDEX ix_quota_operations_created_at ON quota_operations (created_at)""")
    op.execute("""CREATE TABLE quota_ledger (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        operation_id UUID NOT NULL,
        sequence BIGINT NOT NULL,
        kind VARCHAR(16) NOT NULL,
        delta_bytes BIGINT NOT NULL,
        source_type VARCHAR(32) NOT NULL,
        source_id UUID NOT NULL,
        idempotency_key UUID NOT NULL,
        balance_after_bytes BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_quota_ledger PRIMARY KEY (id),
        CONSTRAINT fk_quota_ledger_operation_owner FOREIGN KEY(operation_id, user_id) REFERENCES
quota_operations (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT uq_quota_ledger_operation UNIQUE (operation_id),
        CONSTRAINT uq_quota_ledger_user_sequence UNIQUE (user_id, sequence),
        CONSTRAINT uq_quota_ledger_user_key UNIQUE (user_id, idempotency_key),
        CONSTRAINT uq_quota_ledger_source UNIQUE (user_id, source_type, source_id),
        CONSTRAINT ck_quota_ledger_sequence CHECK (sequence > 0),
        CONSTRAINT ck_quota_ledger_direction CHECK (delta_bytes >= -9223372036854775807 AND
((kind = 'credit' AND delta_bytes >= 0) OR (kind = 'debit' AND delta_bytes < 0))),
        CONSTRAINT ck_quota_ledger_source CHECK (source_type IN ('registration', 'internal')),
        CONSTRAINT ck_quota_ledger_zero CHECK (delta_bytes <> 0 OR source_type =
'registration'),
        CONSTRAINT ck_quota_ledger_registration CHECK (source_type <> 'registration' OR (kind =
'credit' AND source_id = user_id AND idempotency_key = user_id)),
        CONSTRAINT ck_quota_ledger_balance CHECK (balance_after_bytes >= 0),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
)""")
    op.execute("""CREATE INDEX ix_quota_ledger_created_at ON quota_ledger (created_at)""")
    op.execute("""CREATE FUNCTION reject_quota_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Quota records are append only'; END; $$;""")
    for table in ("quota_operations", "quota_ledger"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE OR TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()""")  # noqa: S608 - fixed table names


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE users, quota_operations, quota_ledger IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM quota_operations) OR EXISTS (SELECT 1 FROM quota_ledger) THEN
            RAISE EXCEPTION 'Refusing downgrade: quota tables contain data';
        END IF; END $$;""")
    op.drop_table("quota_ledger")
    op.drop_table("quota_operations")
    op.execute("DROP FUNCTION reject_quota_mutation()")
