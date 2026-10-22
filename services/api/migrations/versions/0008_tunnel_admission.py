"""Key confirmation, bounded admission and quota reservations."""

from alembic import op

revision: str = "0008_tunnel_admission"
down_revision: str | None = "0007_tunnel_lifecycle"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table, prefix in (
        ("quota_operations", "ck_quota_operations"),
        ("quota_ledger", "ck_quota_ledger"),
    ):
        op.drop_constraint(prefix + "_source", table, type_="check")
        op.drop_constraint(prefix + "_usage", table, type_="check")
        op.create_check_constraint(
            prefix + "_source",
            table,
            "source_type IN ('registration', 'internal', 'usage', 'tunnel_lease')",
        )
        op.create_check_constraint(
            prefix + "_usage",
            table,
            "source_type NOT IN ('usage', 'tunnel_lease') OR kind = 'debit'",
        )
    op.execute("ALTER TABLE vpn_nodes ADD COLUMN peer_protocol_version INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE vpn_nodes ADD COLUMN peer_runtime_id UUID")
    op.execute("ALTER TABLE tunnels ADD COLUMN runtime_id UUID")
    op.execute("ALTER TABLE tunnels ADD COLUMN lease_deadline TIMESTAMPTZ")
    op.execute("ALTER TABLE tunnels ADD COLUMN budget_bytes BIGINT")
    op.execute("ALTER TABLE tunnels ADD CONSTRAINT uq_tunnels_id_user UNIQUE (id, user_id)")
    op.execute("ALTER TABLE tunnels DROP CONSTRAINT ck_tunnels_state")
    op.execute("""ALTER TABLE tunnels ADD CONSTRAINT ck_tunnels_state CHECK (
        (state = 'admission_pending' AND node_id IS NULL AND revoked_at IS NULL) OR
        (state IN ('provision_pending', 'active') AND node_id IS NOT NULL
         AND revoked_at IS NULL AND lease_deadline IS NOT NULL AND budget_bytes > 0) OR
        (state = 'revoke_pending' AND node_id IS NOT NULL AND revoked_at IS NULL) OR
        (state = 'revoked' AND revoked_at IS NOT NULL))""")
    op.execute("""CREATE UNIQUE INDEX uq_tunnels_live_node ON tunnels(node_id)
        WHERE state <> 'revoked' AND node_id IS NOT NULL""")
    op.execute("ALTER TABLE peer_commands DROP CONSTRAINT ck_peer_commands_operation")
    op.execute("""ALTER TABLE peer_commands ADD CONSTRAINT ck_peer_commands_operation
        CHECK (operation IN ('add_peer', 'remove_peer'))""")
    op.execute("""CREATE TABLE device_key_proofs (
        id UUID PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        device_id UUID NOT NULL,
        family_id UUID NOT NULL REFERENCES session_families(id) ON DELETE RESTRICT,
        response_digest VARCHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ,
        CONSTRAINT fk_device_key_proofs_owner FOREIGN KEY (device_id, user_id)
            REFERENCES devices(id, user_id) ON DELETE RESTRICT,
        CONSTRAINT ck_device_key_proofs_expiry CHECK (expires_at > created_at)
    )""")
    op.execute("CREATE INDEX ix_device_key_proofs_expiry ON device_key_proofs(expires_at)")
    op.execute("""CREATE TABLE tunnel_reservations (
        tunnel_id UUID PRIMARY KEY REFERENCES tunnels(id) ON DELETE RESTRICT,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
        amount_bytes BIGINT NOT NULL,
        charged_bytes BIGINT,
        observed_bytes BIGINT,
        settled_at TIMESTAMPTZ,
        CONSTRAINT fk_tunnel_reservations_owner FOREIGN KEY (tunnel_id, user_id)
            REFERENCES tunnels(id, user_id) ON DELETE RESTRICT,
        CONSTRAINT ck_tunnel_reservations_amount CHECK (
            amount_bytes > 0 AND amount_bytes <= 16777216),
        CONSTRAINT ck_tunnel_reservations_charge CHECK (
            charged_bytes IS NULL OR charged_bytes BETWEEN 0 AND amount_bytes),
        CONSTRAINT ck_tunnel_reservations_settled CHECK (
            (settled_at IS NULL) = (charged_bytes IS NULL)),
        CONSTRAINT ck_tunnel_reservations_observed CHECK (
            observed_bytes IS NULL OR observed_bytes >= 0)
    )""")
    op.execute("CREATE INDEX ix_tunnel_reservations_owner ON tunnel_reservations(user_id)")
    # Immutable receipt table already has an append-only trigger, so only extend schema.
    op.execute(
        "ALTER TABLE quota_operations ADD COLUMN reserved_before_bytes BIGINT NOT NULL DEFAULT 0"
    )
    op.execute("""ALTER TABLE quota_operations ADD CONSTRAINT ck_quota_operations_reserved
        CHECK (reserved_before_bytes BETWEEN 0 AND balance_after_bytes)""")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("""LOCK TABLE tunnels, peer_commands, device_key_proofs,
        tunnel_reservations, quota_operations IN ACCESS EXCLUSIVE MODE""")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM device_key_proofs) OR EXISTS (SELECT 1 FROM tunnel_reservations)
        OR EXISTS (SELECT 1 FROM tunnels WHERE state IN ('active','provision_pending'))
        OR EXISTS (SELECT 1 FROM peer_commands WHERE operation = 'add_peer')
        OR EXISTS (SELECT 1 FROM quota_operations WHERE reserved_before_bytes <> 0) THEN
            RAISE EXCEPTION 'Refusing downgrade: tunnel admission contains data';
        END IF; END $$;""")
    op.drop_constraint("ck_quota_operations_reserved", "quota_operations", type_="check")
    op.drop_column("quota_operations", "reserved_before_bytes")
    op.drop_table("tunnel_reservations")
    op.drop_table("device_key_proofs")
    op.drop_index("uq_tunnels_live_node", table_name="tunnels")
    op.execute("ALTER TABLE peer_commands DROP CONSTRAINT ck_peer_commands_operation")
    op.execute(
        "ALTER TABLE peer_commands ADD CONSTRAINT ck_peer_commands_operation "
        "CHECK (operation = 'remove_peer')"
    )
    op.execute("ALTER TABLE tunnels DROP CONSTRAINT ck_tunnels_state")
    op.execute("""ALTER TABLE tunnels ADD CONSTRAINT ck_tunnels_state CHECK (
        (state = 'admission_pending' AND node_id IS NULL AND revoked_at IS NULL) OR
        (state = 'revoke_pending' AND node_id IS NOT NULL AND revoked_at IS NULL) OR
        (state = 'revoked' AND revoked_at IS NOT NULL))""")
    op.drop_constraint("uq_tunnels_id_user", "tunnels", type_="unique")
    op.drop_column("tunnels", "budget_bytes")
    op.drop_column("tunnels", "lease_deadline")
    op.drop_column("vpn_nodes", "peer_protocol_version")
    op.drop_column("vpn_nodes", "peer_runtime_id")
    op.drop_column("tunnels", "runtime_id")
    for table, prefix in (
        ("quota_operations", "ck_quota_operations"),
        ("quota_ledger", "ck_quota_ledger"),
    ):
        op.drop_constraint(prefix + "_source", table, type_="check")
        op.drop_constraint(prefix + "_usage", table, type_="check")
        op.create_check_constraint(
            prefix + "_source", table, "source_type IN ('registration', 'internal', 'usage')"
        )
        op.create_check_constraint(
            prefix + "_usage", table, "source_type <> 'usage' OR kind = 'debit'"
        )
