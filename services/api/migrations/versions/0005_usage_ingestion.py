"""Frozen usage bindings/epochs/receipts and usage-only quota source."""

from alembic import op

revision: str = "0005_usage_ingestion"
down_revision: str | None = "0004_quota_ledger"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table, prefix in (
        ("quota_operations", "ck_quota_operations"),
        ("quota_ledger", "ck_quota_ledger"),
    ):
        op.drop_constraint(prefix + "_source", table, type_="check")
        op.create_check_constraint(
            prefix + "_source", table, "source_type IN ('registration', 'internal', 'usage')"
        )
        op.create_check_constraint(
            prefix + "_usage", table, "source_type <> 'usage' OR kind = 'debit'"
        )
    op.execute("""CREATE TABLE usage_streams (
        id UUID NOT NULL,
        node_id UUID NOT NULL,
        peer_id UUID NOT NULL,
        user_id UUID NOT NULL,
        device_id UUID NOT NULL,
        generation BIGINT DEFAULT 0 NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_usage_streams PRIMARY KEY (id),
        CONSTRAINT uq_usage_streams_node_peer UNIQUE (node_id, peer_id),
        CONSTRAINT uq_usage_streams_id_user UNIQUE (id, user_id),
        CONSTRAINT uq_usage_streams_id_node UNIQUE (id, node_id),
        CONSTRAINT fk_usage_streams_device_owner FOREIGN KEY(device_id, user_id) REFERENCES
devices (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT ck_usage_streams_generation CHECK (generation >= 0),
        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
)""")
    op.execute("""CREATE INDEX ix_usage_streams_user_id ON usage_streams (user_id)""")
    op.execute("""CREATE TABLE usage_epochs (
        id UUID NOT NULL,
        stream_id UUID NOT NULL,
        generation BIGINT NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL,
        ended_at TIMESTAMP WITH TIME ZONE,
        last_sequence BIGINT DEFAULT 0 NOT NULL,
        receipt_count BIGINT DEFAULT 0 NOT NULL,
        rx_bytes BIGINT DEFAULT 0 NOT NULL,
        tx_bytes BIGINT DEFAULT 0 NOT NULL,
        charged_rx BIGINT DEFAULT 0 NOT NULL,
        charged_tx BIGINT DEFAULT 0 NOT NULL,
        observed_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_usage_epochs PRIMARY KEY (id),
        CONSTRAINT uq_usage_epochs_id_stream UNIQUE (id, stream_id),
        CONSTRAINT uq_usage_epochs_generation UNIQUE (stream_id, generation),
        CONSTRAINT ck_usage_epochs_generation CHECK (generation > 0 AND last_sequence >= 0),
        CONSTRAINT ck_usage_epochs_receipt_count CHECK (receipt_count >= 0),
        CONSTRAINT ck_usage_epochs_counters CHECK (rx_bytes >= 0 AND tx_bytes >= 0 AND rx_bytes
<= 9223372036854775807-tx_bytes AND charged_rx >= 0 AND charged_rx <= rx_bytes AND charged_tx >=
0 AND charged_tx <= tx_bytes),
        CONSTRAINT ck_usage_epochs_interval CHECK (ended_at IS NULL OR ended_at > started_at),
        CONSTRAINT ck_usage_epochs_observed CHECK ((last_sequence = 0 AND observed_at IS NULL
AND rx_bytes = 0 AND tx_bytes = 0) OR (last_sequence > 0 AND observed_at IS NOT NULL)),
        FOREIGN KEY(stream_id) REFERENCES usage_streams (id) ON DELETE RESTRICT
)""")
    op.execute("""CREATE TABLE usage_samples (
        id UUID NOT NULL,
        node_id UUID NOT NULL,
        stream_id UUID NOT NULL,
        user_id UUID NOT NULL,
        counter_epoch UUID NOT NULL,
        schema_version INTEGER NOT NULL,
        sequence BIGINT NOT NULL,
        sampled_at TIMESTAMP WITH TIME ZONE NOT NULL,
        epoch_end_at_receipt TIMESTAMP WITH TIME ZONE,
        arrival_order BIGINT NOT NULL,
        rx_total_bytes BIGINT NOT NULL,
        tx_total_bytes BIGINT NOT NULL,
        idempotency_key UUID NOT NULL,
        status VARCHAR(16) NOT NULL,
        reason VARCHAR(32),
        delta_bytes BIGINT NOT NULL,
        pending_bytes BIGINT NOT NULL,
        quota_operation_id UUID,
        received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT pk_usage_samples PRIMARY KEY (id),
        CONSTRAINT fk_usage_samples_node FOREIGN KEY(stream_id, node_id) REFERENCES
usage_streams (id, node_id) ON DELETE RESTRICT,
        CONSTRAINT fk_usage_samples_owner FOREIGN KEY(stream_id, user_id) REFERENCES
usage_streams (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT fk_usage_samples_epoch FOREIGN KEY(counter_epoch, stream_id) REFERENCES
usage_epochs (id, stream_id) ON DELETE RESTRICT,
        CONSTRAINT fk_usage_samples_quota_owner FOREIGN KEY(quota_operation_id, user_id)
REFERENCES quota_operations (id, user_id) ON DELETE RESTRICT,
        CONSTRAINT uq_usage_samples_node_key UNIQUE (node_id, idempotency_key),
        CONSTRAINT uq_usage_samples_epoch_sequence UNIQUE (counter_epoch, sequence),
        CONSTRAINT uq_usage_samples_epoch_arrival UNIQUE (counter_epoch, arrival_order),
        CONSTRAINT uq_usage_samples_quota_operation UNIQUE (quota_operation_id),
        CONSTRAINT ck_usage_samples_arrival_order CHECK (arrival_order > 0),
        CONSTRAINT ck_usage_samples_version_sequence CHECK (schema_version = 1 AND sequence >
0),
        CONSTRAINT ck_usage_samples_bytes CHECK (rx_total_bytes >= 0 AND tx_total_bytes >= 0 AND
rx_total_bytes <= 9223372036854775807-tx_total_bytes AND delta_bytes >= 0 AND pending_bytes >=
0),
        CONSTRAINT ck_usage_samples_outcome CHECK ((status = 'charged' AND reason IS NULL AND
delta_bytes > 0 AND pending_bytes = 0 AND quota_operation_id IS NOT NULL) OR (status = 'blocked'
AND reason IS NOT NULL AND reason IN ('insufficient_quota', 'quota_capacity_exceeded') AND
delta_bytes > 0 AND pending_bytes = delta_bytes AND quota_operation_id IS NOT NULL) OR (status
IN ('no_change', 'stale') AND reason IS NULL AND delta_bytes = 0 AND quota_operation_id IS NULL
AND (status <> 'no_change' OR pending_bytes = 0)) OR (status = 'rejected' AND reason IS NOT NULL
AND reason IN ('counter_regression', 'timestamp_regression', 'outside_epoch', 'future_sample',
'stale_conflict') AND delta_bytes = 0 AND quota_operation_id IS NULL))
)""")
    op.execute("""CREATE INDEX ix_usage_samples_received_at ON usage_samples (received_at)""")
    op.execute(
        """CREATE INDEX ix_usage_samples_stream_received
        ON usage_samples (stream_id, received_at)"""
    )
    op.execute("""CREATE INDEX ix_usage_samples_user_id ON usage_samples (user_id)""")
    op.execute("""CREATE FUNCTION reject_usage_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Usage receipts are append only'; END; $$;""")
    op.execute("""CREATE TRIGGER usage_samples_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON usage_samples
        FOR EACH STATEMENT EXECUTE FUNCTION reject_usage_mutation()""")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE users, usage_streams, usage_epochs, usage_samples, "
        "quota_operations, quota_ledger IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM usage_streams) OR EXISTS (SELECT 1 FROM usage_epochs)
        OR EXISTS (SELECT 1 FROM usage_samples)
        OR EXISTS (SELECT 1 FROM quota_operations WHERE source_type = 'usage')
        OR EXISTS (SELECT 1 FROM quota_ledger WHERE source_type = 'usage') THEN
            RAISE EXCEPTION 'Refusing downgrade: usage accounting contains data';
        END IF; END $$;""")
    op.drop_table("usage_samples")
    op.drop_table("usage_epochs")
    op.drop_table("usage_streams")
    op.execute("DROP FUNCTION reject_usage_mutation()")
    for table, prefix in (
        ("quota_operations", "ck_quota_operations"),
        ("quota_ledger", "ck_quota_ledger"),
    ):
        op.drop_constraint(prefix + "_usage", table, type_="check")
        op.drop_constraint(prefix + "_source", table, type_="check")
        op.create_check_constraint(
            prefix + "_source", table, "source_type IN ('registration', 'internal')"
        )
