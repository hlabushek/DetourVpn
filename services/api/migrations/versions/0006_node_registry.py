"""Node registry and replay-resistant agent principals."""

from alembic import op

revision: str = "0006_node_registry"
down_revision: str | None = "0005_usage_ingestion"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE vpn_nodes (
        id UUID NOT NULL PRIMARY KEY,
        provisioning_key UUID NOT NULL,
        name VARCHAR(80) NOT NULL,
        region VARCHAR(32) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        contract_version INTEGER NOT NULL DEFAULT 1,
        secret_digest VARCHAR(64) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        last_seen_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT uq_vpn_nodes_id_secret_digest UNIQUE (id, secret_digest),
        CONSTRAINT uq_vpn_nodes_provisioning_key UNIQUE (provisioning_key),
        CONSTRAINT ck_vpn_nodes_status CHECK (status IN ('active', 'disabled')),
        CONSTRAINT ck_vpn_nodes_contract_version CHECK (contract_version = 1),
        CONSTRAINT ck_vpn_nodes_name CHECK (length(name) BETWEEN 1 AND 80),
        CONSTRAINT ck_vpn_nodes_region CHECK (length(region) BETWEEN 1 AND 32)
    )""")
    op.execute("""CREATE TABLE agent_request_nonces (
        id UUID NOT NULL PRIMARY KEY,
        node_id UUID NOT NULL,
        secret_digest VARCHAR(64) NOT NULL,
        nonce UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        CONSTRAINT fk_agent_request_nonces_node_secret FOREIGN KEY (node_id, secret_digest)
            REFERENCES vpn_nodes (id, secret_digest) ON DELETE RESTRICT,
        CONSTRAINT uq_agent_request_nonces_node_nonce UNIQUE (node_id, nonce)
    )""")
    op.execute(
        "CREATE INDEX ix_agent_request_nonces_created_at ON agent_request_nonces (created_at)"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE vpn_nodes, agent_request_nonces IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM agent_request_nonces) OR EXISTS (SELECT 1 FROM vpn_nodes) THEN
            RAISE EXCEPTION 'Refusing downgrade: node registry contains data';
        END IF; END $$;""")
    op.drop_table("agent_request_nonces")
    op.drop_table("vpn_nodes")
