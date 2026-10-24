"""V3 durable admission and CAS node runtime history; no remote rollout."""

from alembic import op

revision: str = "0015_window_admission"
down_revision: str | None = "0014_peer_windows"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE peer_window_holds ADD COLUMN node_id UUID, "
        "ADD COLUMN runtime_id UUID, ADD COLUMN slot_id INTEGER"
    )
    op.execute("DROP TRIGGER peer_window_holds_immutable ON peer_window_holds")
    op.execute(
        "UPDATE peer_window_holds SET node_id=(\"grant\"->>'node_id')::uuid, "
        "runtime_id=(\"grant\"->>'runtime_id')::uuid, slot_id=(\"grant\"->>'slot_id')::integer"
    )
    op.execute(
        "CREATE TRIGGER peer_window_holds_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
        "ON peer_window_holds FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()"
    )
    op.execute(
        "ALTER TABLE peer_window_holds ALTER COLUMN node_id SET NOT NULL, "
        "ALTER COLUMN runtime_id SET NOT NULL, ALTER COLUMN slot_id SET NOT NULL"
    )
    op.create_index("ix_peer_window_hold_runtime", "peer_window_holds", ["node_id", "runtime_id"])
    op.execute("""
CREATE TABLE peer_runtime_epochs (
node_id UUID NOT NULL,
runtime_id UUID NOT NULL,
previous_runtime_id UUID,
manifest_digest VARCHAR(64) NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
CONSTRAINT pk_peer_runtime_epochs PRIMARY KEY (node_id, runtime_id),
FOREIGN KEY(node_id) REFERENCES vpn_nodes (id) ON DELETE RESTRICT
)
""")
    op.execute("""
CREATE TABLE peer_connections (
id UUID NOT NULL,
user_id UUID NOT NULL,
device_id UUID NOT NULL,
node_id UUID NOT NULL,
runtime_id UUID NOT NULL,
slot_id INTEGER NOT NULL,
window_id UUID NOT NULL,
generation BIGINT NOT NULL,
state VARCHAR(24) NOT NULL,
idempotency_key UUID NOT NULL,
created_at TIMESTAMP WITH TIME ZONE NOT NULL,
CONSTRAINT pk_peer_connections PRIMARY KEY (id),
CONSTRAINT fk_peer_connection_device_owner FOREIGN KEY(device_id, user_id) REFERENCES devices
(id, user_id) ON DELETE RESTRICT,
CONSTRAINT fk_peer_connection_window_owner FOREIGN KEY(window_id, user_id) REFERENCES
peer_window_holds (id, user_id) ON DELETE RESTRICT,
CONSTRAINT fk_peer_connection_epoch FOREIGN KEY(node_id, runtime_id) REFERENCES
peer_runtime_epochs (node_id, runtime_id) ON DELETE RESTRICT,
CONSTRAINT uq_peer_connection_key UNIQUE (user_id, idempotency_key),
CONSTRAINT ck_peer_connection_slot CHECK (slot_id BETWEEN 1 AND 16 AND generation > 0),
CONSTRAINT ck_peer_connection_state CHECK (state IN
('pending','active','revoke_pending','closed')),
FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT
)
""")
    op.execute("CREATE INDEX ix_peer_connection_owner ON peer_connections (user_id, created_at)")
    op.execute("""
CREATE UNIQUE INDEX uq_peer_connection_live_device ON peer_connections (device_id) WHERE state
<> 'closed'
""")
    op.execute("""
CREATE UNIQUE INDEX uq_peer_connection_live_slot ON peer_connections (node_id, slot_id) WHERE
state <> 'closed'
""")
    op.execute(
        "CREATE TRIGGER peer_runtime_epochs_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
        "ON peer_runtime_epochs FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()"
    )
    op.execute("""CREATE FUNCTION guard_peer_connection() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'UPDATE' THEN
                RAISE EXCEPTION 'Connection history cannot be removed';
            END IF;
            IF ROW(NEW.id,NEW.user_id,NEW.device_id,NEW.node_id,NEW.runtime_id,NEW.slot_id,
                   NEW.idempotency_key,NEW.created_at) IS DISTINCT FROM
               ROW(OLD.id,OLD.user_id,OLD.device_id,OLD.node_id,OLD.runtime_id,OLD.slot_id,
                   OLD.idempotency_key,OLD.created_at)
               OR NEW.generation < OLD.generation OR NEW.generation - OLD.generation > 1
               OR (OLD.state='closed' AND NEW IS DISTINCT FROM OLD) THEN
                RAISE EXCEPTION 'Connection terms are immutable';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute(
        "CREATE TRIGGER peer_connection_guard BEFORE UPDATE OR DELETE ON peer_connections "
        "FOR EACH ROW EXECUTE FUNCTION guard_peer_connection()"
    )
    op.execute(
        "CREATE TRIGGER peer_connection_no_truncate BEFORE TRUNCATE ON peer_connections "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_quota_mutation()"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE users,vpn_nodes,peer_window_holds,peer_connections,peer_runtime_epochs "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM peer_runtime_epochs) OR EXISTS(SELECT 1 FROM peer_connections) THEN
            RAISE EXCEPTION 'Refusing downgrade: window admission history exists';
        END IF;
    END $$""")
    op.drop_table("peer_connections")
    op.execute("DROP FUNCTION guard_peer_connection()")
    op.drop_table("peer_runtime_epochs")
    op.drop_index("ix_peer_window_hold_runtime", table_name="peer_window_holds")
    for column in ("node_id", "runtime_id", "slot_id"):
        op.drop_column("peer_window_holds", column)
