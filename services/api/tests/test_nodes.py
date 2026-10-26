"""PostgreSQL contract tests use synthetic node credentials, never a real VPN node."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from test_migrations import migration_config

from vpn_api.db.connection import transaction
from vpn_api.db.models import AgentRequestNonce, Device, User, VPNNode
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeError, NodeRegistry
from vpn_api.quota.contracts import LedgerCommand
from vpn_api.quota.service import LedgerService
from vpn_api.usage.service import UsageService

pytestmark = pytest.mark.postgres


def test_provisioning_is_idempotent_and_secret_is_returned_once(engine: Engine) -> None:
    command.upgrade(migration_config(engine), "head")
    registry = NodeRegistry(engine)
    request = ProvisionNodeInput(idempotency_key=uuid4(), name="test-node", region="eu-test")
    created = registry.provision(request)
    replay = registry.provision(request)
    assert created.node_id == replay.node_id
    assert created.secret is not None and len(created.secret) >= 40
    assert replay.secret is None and replay.replayed
    with pytest.raises(NodeError, match="node_provisioning_conflict"):
        registry.provision(request.model_copy(update={"name": "different-node"}))
    with transaction(engine) as session:
        assert (
            session.scalar(select(VPNNode.id).where(VPNNode.id == created.node_id))
            == created.node_id
        )


def test_beta_staged_provision_is_disabled_and_replay_does_not_reveal_secret(
    engine: Engine,
) -> None:
    command.upgrade(migration_config(engine), "head")
    registry = NodeRegistry(engine)
    request = ProvisionNodeInput(idempotency_key=uuid4(), name="beta-node", region="helsinki")
    staged = registry.provision(request, initial_status="disabled")
    replay = registry.provision(request, initial_status="disabled")
    assert staged.secret is not None and len(staged.secret) == 43
    assert replay.node_id == staged.node_id
    assert replay.secret is None and replay.replayed
    with transaction(engine) as session:
        row = session.get(VPNNode, staged.node_id)
        assert row is not None and row.status == "disabled"
    with pytest.raises(NodeError, match="agent_unauthorized"):
        with transaction(engine) as session:
            NodeRegistry.verify_and_consume(
                session,
                node_id=staged.node_id,
                timestamp=str(int(datetime.now(UTC).timestamp())),
                nonce=uuid4(),
                credential=staged.secret,
            )


def test_agent_nonce_is_one_time_and_credential_is_verified(engine: Engine) -> None:
    command.upgrade(migration_config(engine), "head")
    registry = NodeRegistry(engine)
    provisioned = registry.provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="node", region="eu-test")
    )
    assert provisioned.secret is not None
    node_id, nonce = provisioned.node_id, uuid4()
    timestamp = str(int(datetime.now(UTC).timestamp()))
    with transaction(engine) as session:
        principal = registry.verify_and_consume(
            session,
            node_id=node_id,
            timestamp=timestamp,
            nonce=nonce,
            credential=provisioned.secret,
        )
        assert principal.node_id == node_id
    with pytest.raises(NodeError, match="agent_replay"):
        with transaction(engine) as session:
            registry.verify_and_consume(
                session,
                node_id=node_id,
                timestamp=timestamp,
                nonce=nonce,
                credential=provisioned.secret,
            )
    with pytest.raises(NodeError, match="agent_unauthorized"):
        with transaction(engine) as session:
            registry.verify_and_consume(
                session,
                node_id=node_id,
                timestamp=timestamp,
                nonce=uuid4(),
                credential="wrong credential",
            )
    with transaction(engine) as session:
        assert session.scalar(select(AgentRequestNonce).where(AgentRequestNonce.nonce == nonce))


def test_agent_usage_endpoint_charges_once_and_retries_by_receipt(engine: Engine) -> None:
    command.upgrade(migration_config(engine), "head")
    with transaction(engine) as session:
        user = User(status="active")
        session.add(user)
        session.flush()
        device = Device(
            user_id=user.id,
            platform="android",
            key_type="x25519",
            public_key="A" * 43,
            created_at=datetime.now(UTC),
        )
        session.add(device)
        session.flush()
        user_id, device_id = user.id, device.id
    registry = NodeRegistry(engine)
    node = registry.provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="node", region="eu-test")
    )
    assert node.secret is not None
    peer_id, epoch_id = uuid4(), uuid4()
    usage = UsageService(engine)
    usage.bind(node.node_id, peer_id, device_id)
    sampled_at = datetime.now(UTC).replace(microsecond=0)
    usage.open_epoch(node.node_id, peer_id, epoch_id, 0, sampled_at)
    LedgerService(engine).apply(
        user_id,
        LedgerCommand(kind="credit", amount_bytes=100, idempotency_key=uuid4(), source_id=uuid4()),
    )
    sample = {
        "schema_version": 1,
        "peer_id": str(peer_id),
        "counter_epoch": str(epoch_id),
        "sequence": 1,
        "sampled_at": sampled_at.isoformat(),
        "rx_total_bytes": 11,
        "tx_total_bytes": 14,
        "idempotency_key": str(uuid4()),
    }
    app = create_app(database_engine=engine)
    with TestClient(app) as client:
        responses = []
        for _ in range(2):
            response = client.post(
                "/api/internal/v1/agent/usage",
                headers={
                    "X-Agent-Node-Id": str(node.node_id),
                    "X-Agent-Credential": node.secret,
                    "X-Agent-Timestamp": str(int(datetime.now(UTC).timestamp())),
                    "X-Agent-Nonce": str(uuid4()),
                },
                json={"contract_version": 1, "sample": sample},
            )
            assert response.status_code == 200, response.json()
            responses.append(response.json()["result"])
    assert responses[0] == responses[1]
    assert responses[0]["status"] == "charged"
    assert responses[0]["delta_bytes"] == 25
    with transaction(engine) as session:
        assert LedgerService(engine).quota(session, user_id).available_bytes == 75
