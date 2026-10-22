"""Durable admission intents and peer removal outbox; admission stays closed."""

from alembic import op

revision: str = "0007_tunnel_lifecycle"
down_revision: str | None = "0006_node_registry"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE tunnels (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        device_id UUID NOT NULL,
        idempotency_key UUID NOT NULL,
        node_id UUID REFERENCES vpn_nodes(id) ON DELETE RESTRICT,
        state VARCHAR(24) NOT NULL,
        peer_version BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        revoked_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT fk_tunnels_device_owner FOREIGN KEY (device_id, user_id)
            REFERENCES devices(id, user_id) ON DELETE RESTRICT,
        CONSTRAINT uq_tunnels_user_key UNIQUE (user_id, idempotency_key),
        CONSTRAINT uq_tunnels_id_node UNIQUE (id, node_id),
        CONSTRAINT ck_tunnels_version CHECK (peer_version > 0),
        CONSTRAINT ck_tunnels_state CHECK (
            (state = 'admission_pending' AND node_id IS NULL AND revoked_at IS NULL) OR
            (state = 'revoke_pending' AND node_id IS NOT NULL AND revoked_at IS NULL) OR
            (state = 'revoked' AND revoked_at IS NOT NULL))
    )""")
    op.execute("CREATE INDEX ix_tunnels_user_created ON tunnels (user_id, created_at, id)")
    op.execute("""CREATE UNIQUE INDEX uq_tunnels_live_device ON tunnels(device_id)
        WHERE state <> 'revoked'""")
    op.execute("""CREATE TABLE peer_commands (
        id UUID PRIMARY KEY,
        tunnel_id UUID NOT NULL,
        node_id UUID NOT NULL,
        operation VARCHAR(16) NOT NULL,
        peer_version BIGINT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        acknowledged_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT fk_peer_commands_tunnel_node FOREIGN KEY (tunnel_id, node_id)
            REFERENCES tunnels(id, node_id) ON DELETE RESTRICT,
        CONSTRAINT uq_peer_commands_peer_version UNIQUE (tunnel_id, peer_version),
        CONSTRAINT ck_peer_commands_operation CHECK (operation = 'remove_peer'),
        CONSTRAINT ck_peer_commands_version CHECK (peer_version > 0),
        CONSTRAINT ck_peer_commands_ack_time CHECK (
            acknowledged_at IS NULL OR acknowledged_at >= created_at)
    )""")
    op.execute("""CREATE INDEX ix_peer_commands_pending ON peer_commands(node_id, created_at, id)
        WHERE acknowledged_at IS NULL""")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE tunnels, peer_commands IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM tunnels) OR EXISTS (SELECT 1 FROM peer_commands) THEN
            RAISE EXCEPTION 'Refusing downgrade: tunnel lifecycle contains data';
        END IF; END $$;""")
    op.drop_table("peer_commands")
    op.drop_table("tunnels")
