"""Real PostgreSQL lifecycle tests; assigned peers/agent ACKs are synthetic, no VPN."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_authentication import Harness, harness, headers, login
from test_devices import key_input
from test_migrations import migration_config

from vpn_api.auth.contracts import TokenResponse
from vpn_api.auth.security import AuthError
from vpn_api.db.connection import transaction
from vpn_api.db.models import AgentRequestNonce, PeerCommand, Tunnel
from vpn_api.devices import register_device
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput, ProvisionNodeResult
from vpn_api.nodes.service import NodeRegistry
from vpn_api.tunnels.contracts import TunnelInput
from vpn_api.tunnels.service import enqueue_revoke, request_tunnel

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def setup_request(h: Harness) -> tuple[TokenResponse, TunnelInput]:
    tokens = login(h)
    device = register_device(h.auth, tokens.access_token, key_input())
    return tokens, TunnelInput(device_id=device.id, idempotency_key=uuid4())


def node(h: Harness) -> ProvisionNodeResult:
    return NodeRegistry(h.engine).provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="synthetic-node", region="test")
    )


def agent_headers(provisioned: ProvisionNodeResult) -> dict[str, str]:
    assert provisioned.secret is not None
    return {
        "X-Agent-Node-Id": str(provisioned.node_id),
        "X-Agent-Credential": provisioned.secret,
        "X-Agent-Timestamp": str(int(datetime.now(UTC).timestamp())),
        "X-Agent-Nonce": str(uuid4()),
    }


def assigned_peer(
    h: Harness, tokens: TokenResponse, body: TunnelInput, provisioned: ProvisionNodeResult
) -> UUID:
    """TEST ONLY: seed future orchestration's peer state, not an installed peer."""
    tunnel = request_tunnel(h.auth, tokens.access_token, body)
    with transaction(h.engine) as session:
        session.execute(
            update(Tunnel)
            .where(Tunnel.id == tunnel.id)
            .values(node_id=provisioned.node_id, state="revoke_pending", peer_version=2)
        )
    return tunnel.id


def poll(h: Harness, provisioned: ProvisionNodeResult) -> list[dict[str, object]]:
    response = h.client.post(
        "/api/internal/v1/agent/peers/poll",
        headers=agent_headers(provisioned),
        json={"contract_version": 1, "capabilities": ["peer-removal.v1"]},
    )
    assert response.status_code == 200, response.text
    result: list[dict[str, object]] = response.json()["commands"]
    return result


def test_request_replay_cancel_and_new_request(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    created = h.client.post(
        "/api/v1/tunnels", headers=headers(tokens), json=body.model_dump(mode="json")
    )
    assert created.status_code == 202
    assert created.json()["state"] == "admission_pending"
    assert set(created.json()) == {
        "id",
        "device_id",
        "node_id",
        "state",
        "peer_version",
        "created_at",
        "revoked_at",
        "lease_deadline",
        "budget_bytes",
    }
    tunnel_id = created.json()["id"]
    replay = h.client.post(
        "/api/v1/tunnels", headers=headers(tokens), json=body.model_dump(mode="json")
    )
    assert replay.json() == created.json()
    assert h.client.get("/api/v1/tunnels", headers=headers(tokens)).json() == [created.json()]
    for _ in range(2):
        response = h.client.delete(f"/api/v1/tunnels/{tunnel_id}", headers=headers(tokens))
        assert response.status_code == 200 and response.json()["state"] == "revoked"
    assert request_tunnel(h.auth, tokens.access_token, body).state == "revoked"
    replacement = request_tunnel(
        h.auth, tokens.access_token, body.model_copy(update={"idempotency_key": uuid4()})
    )
    assert replacement.id != UUID(tunnel_id)
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(PeerCommand)) == 0


def test_owner_and_session_checks(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    tunnel = request_tunnel(h.auth, tokens.access_token, body)
    other = login(h, 123)
    assert h.client.get("/api/v1/tunnels", headers=headers(other)).json() == []
    for target in (tunnel.id, uuid4()):
        for method in ("GET", "DELETE"):
            result = h.client.request(method, f"/api/v1/tunnels/{target}", headers=headers(other))
            assert result.status_code == 404 and result.json()["error"]["code"] == "not_found"
    for target in (body.device_id, uuid4()):
        result = h.client.post(
            "/api/v1/tunnels",
            headers=headers(other),
            json={"device_id": str(target), "idempotency_key": str(uuid4())},
        )
        assert result.status_code == 404
    unbound = login(h)
    result = h.client.post(
        "/api/v1/tunnels", headers=headers(unbound), json=body.model_dump(mode="json")
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "session_device_mismatch"
    assert h.client.get("/api/v1/tunnels?limit=101", headers=headers(tokens)).status_code == 422
    assert h.client.get("/api/v1/tunnels?offset=1", headers=headers(tokens)).json() == []
    assert h.client.get(f"/api/v1/tunnels/{tunnel.id}").status_code == 401


def test_key_conflict_live_device_and_extra_fields(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    request_tunnel(h.auth, tokens.access_token, body)
    with pytest.raises(AuthError, match="tunnel_exists"):
        request_tunnel(
            h.auth, tokens.access_token, body.model_copy(update={"idempotency_key": uuid4()})
        )
    second = login(h)
    device = register_device(h.auth, second.access_token, key_input())
    with pytest.raises(AuthError, match="tunnel_idempotency_conflict"):
        request_tunnel(
            h.auth, second.access_token, body.model_copy(update={"device_id": device.id})
        )
    extras: tuple[dict[str, object], ...] = (
        {"node_id": "invalid-uuid"},
        {"private_key": "rejected"},
        {"state": "active"},
        {"config": {}},
    )
    for extra in extras:
        result = h.client.post(
            "/api/v1/tunnels", headers=headers(tokens), json=body.model_dump(mode="json") | extra
        )
        assert result.status_code == 422
    # I-019 accepts node_id, but changing a replayed intent must fail closed.
    changed_node = h.client.post(
        "/api/v1/tunnels",
        headers=headers(tokens),
        json=body.model_dump(mode="json") | {"node_id": str(uuid4())},
    )
    assert changed_node.status_code == 409
    assert changed_node.json()["error"]["code"] == "tunnel_idempotency_conflict"


def test_concurrent_request_is_once_only(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    barrier = Barrier(4, timeout=10)

    def create(_: int) -> UUID:
        barrier.wait()
        return request_tunnel(h.auth, tokens.access_token, body).id

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(create, range(4)))) == 1
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(Tunnel)) == 1


def test_device_revoke_closes_intent_and_session_atomically(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    tunnel = request_tunnel(h.auth, tokens.access_token, body)
    operator = login(h)
    result = h.client.delete(f"/api/v1/devices/{body.device_id}", headers=headers(operator))
    assert result.status_code == 204
    assert h.client.get(f"/api/v1/tunnels/{tunnel.id}", headers=headers(tokens)).status_code == 401
    assert (
        h.client.get(f"/api/v1/tunnels/{tunnel.id}", headers=headers(operator)).json()["state"]
        == "revoked"
    )
    with pytest.raises(AuthError, match="device_revoked"):
        request_tunnel(h.auth, operator.access_token, body)


def test_remove_outbox_retry_ack_and_node_isolation(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    provisioned, another = node(h), node(h)
    tunnel_id = assigned_peer(h, tokens, body, provisioned)
    for _ in range(2):
        result = h.client.delete(f"/api/v1/tunnels/{tunnel_id}", headers=headers(tokens))
        assert result.status_code == 200 and result.json()["state"] == "revoke_pending"
    pending = poll(h, provisioned)
    assert len(pending) == 1 and poll(h, provisioned) == pending
    assert poll(h, another) == []
    ack_body = {
        "contract_version": 1,
        "capability": "peer-removal.v1",
        "command_id": pending[0]["command_id"],
        "peer_id": str(tunnel_id),
        "peer_version": 2,
        "outcome": "removed",
    }
    forbidden = h.client.post(
        "/api/internal/v1/agent/peers/ack", headers=agent_headers(another), json=ack_body
    )
    assert forbidden.status_code == 404
    for _ in range(2):
        result = h.client.post(
            "/api/internal/v1/agent/peers/ack", headers=agent_headers(provisioned), json=ack_body
        )
        assert result.status_code == 200 and result.json()["state"] == "revoked"
    assert poll(h, provisioned) == []
    assert (
        h.client.get(f"/api/v1/tunnels/{tunnel_id}", headers=headers(tokens)).json()["state"]
        == "revoked"
    )


def test_agent_replay_validation_and_stale_ack_rollback(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    provisioned = node(h)
    tunnel_id = assigned_peer(h, tokens, body, provisioned)
    h.client.delete(f"/api/v1/tunnels/{tunnel_id}", headers=headers(tokens))
    pending = poll(h, provisioned)[0]
    common = {"contract_version": 1, "capabilities": ["peer-removal.v1"]}
    once = agent_headers(provisioned)
    assert (
        h.client.post("/api/internal/v1/agent/peers/poll", headers=once, json=common).status_code
        == 200
    )
    replay = h.client.post("/api/internal/v1/agent/peers/poll", headers=once, json=common)
    assert replay.status_code == 409 and replay.json()["error"]["code"] == "agent_replay"
    assert h.client.post("/api/internal/v1/agent/peers/poll", json=common).status_code == 401
    for change, status in (
        ({"contract_version": 2}, 409),
        ({"contract_version": True}, 422),
        ({"capabilities": ["add-peer.v1"]}, 422),
        ({"limit": 101}, 422),
        ({"node_id": str(provisioned.node_id)}, 422),
    ):
        result = h.client.post(
            "/api/internal/v1/agent/peers/poll",
            headers=agent_headers(provisioned),
            json=common | change,
        )
        assert result.status_code == status
    ack_body = {
        "contract_version": 1,
        "capability": "peer-removal.v1",
        "outcome": "removed",
        "command_id": pending["command_id"],
        "peer_id": str(tunnel_id),
        "peer_version": 1,
    }
    retry_headers = agent_headers(provisioned)
    result = h.client.post("/api/internal/v1/agent/peers/ack", headers=retry_headers, json=ack_body)
    assert result.status_code == 409
    with transaction(h.engine) as session:
        assert (
            session.scalar(
                select(AgentRequestNonce).where(
                    AgentRequestNonce.nonce == UUID(retry_headers["X-Agent-Nonce"])
                )
            )
            is None
        )
        tunnel = session.get(Tunnel, tunnel_id)
        assert tunnel is not None and tunnel.state == "revoke_pending"
    assert (
        h.client.post(
            "/api/internal/v1/agent/peers/ack",
            headers=retry_headers,
            json=ack_body | {"peer_version": 2},
        ).status_code
        == 200
    )


def test_device_revoke_outbox_and_ack_race(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    operator, provisioned = login(h), node(h)
    tunnel_id = assigned_peer(h, tokens, body, provisioned)
    h.client.delete(f"/api/v1/tunnels/{tunnel_id}", headers=headers(operator))
    pending = poll(h, provisioned)[0]
    barrier = Barrier(2, timeout=10)

    def revoke() -> int:
        barrier.wait()
        return h.client.delete(
            f"/api/v1/devices/{body.device_id}", headers=headers(operator)
        ).status_code

    def ack() -> int:
        barrier.wait()
        return h.client.post(
            "/api/internal/v1/agent/peers/ack",
            headers=agent_headers(provisioned),
            json={
                "contract_version": 1,
                "capability": "peer-removal.v1",
                "command_id": pending["command_id"],
                "peer_id": str(tunnel_id),
                "peer_version": 2,
                "outcome": "removed",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        revocation, acked = pool.submit(revoke), pool.submit(ack)
        assert revocation.result(timeout=15) == 204
        assert acked.result(timeout=15) == 200
    assert poll(h, provisioned) == []
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(PeerCommand)) == 1
        tunnel = session.get(Tunnel, tunnel_id)
        assert tunnel is not None and tunnel.state == "revoked"


def test_outbox_rollback_and_populated_downgrade(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    provisioned = node(h)
    tunnel_id = assigned_peer(h, tokens, body, provisioned)
    with pytest.raises(RuntimeError, match="injected"), transaction(h.engine) as session:
        tunnel = session.get(Tunnel, tunnel_id)
        assert tunnel is not None
        enqueue_revoke(session, tunnel)
        raise RuntimeError("injected transaction failure")
    assert poll(h, provisioned) == []
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0006_node_registry")
    assert h.client.get(f"/api/v1/tunnels/{tunnel_id}", headers=headers(tokens)).status_code == 200


def test_database_owner_and_command_node_constraints(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    provisioned, another = node(h), node(h)
    tunnel_id = assigned_peer(h, tokens, body, provisioned)
    other = login(h, 555)
    other_id = UUID(h.client.get("/api/v1/me", headers=headers(other)).json()["id"])
    with pytest.raises(IntegrityError), transaction(h.engine) as session:
        session.execute(update(Tunnel).where(Tunnel.id == tunnel_id).values(user_id=other_id))
    with pytest.raises(IntegrityError), transaction(h.engine) as session:
        session.add(
            PeerCommand(
                tunnel_id=tunnel_id,
                node_id=another.node_id,
                operation="remove_peer",
                peer_version=2,
                created_at=datetime.now(UTC),
            )
        )
    with pytest.raises(IntegrityError), transaction(h.engine) as session:
        session.execute(update(Tunnel).where(Tunnel.id == tunnel_id).values(state="active"))
    with pytest.raises(IntegrityError), transaction(h.engine) as session:
        session.add(
            PeerCommand(
                tunnel_id=tunnel_id,
                node_id=provisioned.node_id,
                operation="arbitrary_shell",
                peer_version=2,
                created_at=datetime.now(UTC),
            )
        )


def test_device_revoke_enqueues_and_survives_api_restart(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    operator, provisioned = login(h), node(h)
    tunnel_id = assigned_peer(h, tokens, body, provisioned)
    result = h.client.delete(f"/api/v1/devices/{body.device_id}", headers=headers(operator))
    assert result.status_code == 204
    pending = poll(h, provisioned)
    assert len(pending) == 1
    assert (
        h.client.get(f"/api/v1/tunnels/{tunnel_id}", headers=headers(operator)).json()["state"]
        == "revoke_pending"
    )
    # Fresh app/ORM sessions use the durable database outbox, not in-memory delivery state.
    with TestClient(
        create_app(h.settings, database_engine=h.engine, telegram_sender=h.sender)
    ) as client:
        result = client.post(
            "/api/internal/v1/agent/peers/poll",
            headers=agent_headers(provisioned),
            json={"contract_version": 1, "capabilities": ["peer-removal.v1"]},
        )
        assert result.status_code == 200 and result.json()["commands"] == pending


def test_create_device_revoke_race_leaves_no_live_intent(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    operator = login(h)
    barrier = Barrier(2, timeout=10)

    def create() -> str:
        barrier.wait()
        try:
            return request_tunnel(h.auth, tokens.access_token, body).state
        except AuthError as exc:
            return exc.code

    def revoke() -> int:
        barrier.wait()
        return h.client.delete(
            f"/api/v1/devices/{body.device_id}", headers=headers(operator)
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        created, revoked = pool.submit(create), pool.submit(revoke)
        assert created.result(timeout=15) in {"admission_pending", "unauthorized"}
        assert revoked.result(timeout=15) == 204
    with transaction(h.engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Tunnel).where(Tunnel.state != "revoked")
            )
            == 0
        )


def test_stale_command_after_version_change_cannot_ack(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    provisioned = node(h)
    tunnel_id = assigned_peer(h, tokens, body, provisioned)
    h.client.delete(f"/api/v1/tunnels/{tunnel_id}", headers=headers(tokens))
    pending = poll(h, provisioned)[0]
    # TEST ONLY: simulate a newer desired peer generation committed before delayed ack.
    with transaction(h.engine) as session:
        session.execute(update(Tunnel).where(Tunnel.id == tunnel_id).values(peer_version=3))
    result = h.client.post(
        "/api/internal/v1/agent/peers/ack",
        headers=agent_headers(provisioned),
        json={
            "contract_version": 1,
            "capability": "peer-removal.v1",
            "command_id": pending["command_id"],
            "peer_id": str(tunnel_id),
            "peer_version": 2,
            "outcome": "removed",
        },
    )
    assert result.status_code == 409
    with transaction(h.engine) as session:
        receipt = session.get(PeerCommand, UUID(str(pending["command_id"])))
        assert receipt is not None and receipt.acknowledged_at is None
        tunnel = session.get(Tunnel, tunnel_id)
        assert tunnel is not None and tunnel.state == "revoke_pending"
