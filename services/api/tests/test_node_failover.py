"""I-019: real PostgreSQL/API; TEST ONLY node commands, no VPN/network evidence."""

import base64
import hashlib
import hmac
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from test_authentication import Harness, harness, headers, login
from test_mini_app import exchange, payload
from test_tunnels import agent_headers

from vpn_api.auth.security import AuthError
from vpn_api.db.connection import transaction
from vpn_api.db.models import PeerCommand, QuotaOperation, Tunnel, TunnelReservation, VPNNode
from vpn_api.devices import DeviceInput, register_device
from vpn_api.nodes.contracts import ProvisionNodeInput, ProvisionNodeResult
from vpn_api.nodes.service import NodeRegistry
from vpn_api.operations import monitor_database
from vpn_api.quota.contracts import LedgerCommand
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.admission import NodeManifest, NodeProfile, key_challenge, profiles
from vpn_api.tunnels.contracts import TunnelInput
from vpn_api.tunnels.lifecycle import LifecycleAck, apply_ack
from vpn_api.tunnels.service import enqueue_revoke, recover_expired, request_tunnel

__all__ = ["harness"]
pytestmark = pytest.mark.postgres
PREFIX = "/api/internal/v1/agent/peers/lifecycle/"


def setup_nodes(h: Harness, path: Path) -> list[tuple[ProvisionNodeResult, NodeProfile]]:
    nodes = []
    for number in range(2):
        provisioned = NodeRegistry(h.engine).provision(
            ProvisionNodeInput(idempotency_key=uuid4(), name=f"TEST-ONLY-{number}", region="test")
        )
        selected = NodeProfile(
            node_id=provisioned.node_id,
            runtime_id=uuid4(),
            server_public_key=base64.b64encode(
                X25519PrivateKey.generate().public_key().public_bytes_raw()
            ).decode(),
            header_protection_key=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
            endpoint=f"8.8.8.{number + 1}:51892",  # Never contacted; TEST ONLY config.
        )
        nodes.append((provisioned, selected))
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    p.model_dump(mode="json")
                    | {"header_protection_key": p.header_protection_key.get_secret_value()}
                    for _, p in nodes
                ],
            }
        )
    )
    path.chmod(0o600)
    h.settings.tunnel_profile_file = path
    for provisioned, p in nodes:
        response = h.client.post(
            PREFIX + "poll",
            headers=agent_headers(provisioned),
            json={
                "contract_version": 2,
                "capabilities": ["peer-lease.v2", "kernel-udp-budget.v1"],
                "server_public_key": p.server_public_key,
                "runtime_id": str(p.runtime_id),
                "observed_public_keys": [],
            },
        )
        assert response.status_code == 200
    return nodes


def intent(
    h: Harness, access: str, node_id: UUID | None = None
) -> tuple[UUID, TunnelInput, X25519PrivateKey]:
    private = X25519PrivateKey.generate()
    device = register_device(
        h.auth,
        access,
        DeviceInput(
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(private.public_key().public_bytes_raw()).decode(),
        ),
    )
    with transaction(h.engine) as session:
        principal = h.auth.authenticate(session, access, "tunnels:manage")
        challenge = key_challenge(session, principal, device.id)
        owner = principal.user_id
    shared = private.exchange(
        X25519PublicKey.from_public_bytes(base64.b64decode(challenge.ephemeral_public_key))
    )
    proof = hmac.new(shared, challenge.transcript.encode(), hashlib.sha256).hexdigest()
    LedgerService(h.engine).apply(
        owner,
        LedgerCommand(
            kind="credit", amount_bytes=3 * 1048576, source_id=uuid4(), idempotency_key=uuid4()
        ),
    )
    return (
        owner,
        TunnelInput(
            device_id=device.id,
            node_id=node_id,
            idempotency_key=uuid4(),
            proof_id=challenge.id,
            proof_response=SecretStr(proof),
        ),
        private,
    )


def expire(h: Harness, tunnel_id: UUID, node_id: UUID) -> None:
    with transaction(h.engine) as session:
        t = session.get(Tunnel, tunnel_id)
        n = session.get(VPNNode, node_id)
        assert t is not None and n is not None
        t.lease_deadline = datetime.now(UTC) - timedelta(seconds=11)
        n.last_seen_at = datetime.now(UTC) - timedelta(seconds=30)


def test_failover_catalog_selection_recovery_and_replay(harness: Harness, tmp_path: Path) -> None:
    h = harness
    nodes = setup_nodes(h, tmp_path / "nodes.json")
    a, b = nodes[0][1], nodes[1][1]
    tokens = login(h)
    owner, body, private = intent(h, tokens.access_token, a.node_id)
    catalog = h.client.get("/api/v1/nodes", headers=headers(tokens))
    assert catalog.status_code == 200
    assert [r["reason"] for r in catalog.json()["nodes"]] == ["available", "available"]
    assert set(catalog.json()["nodes"][0]) == {"id", "name", "region", "reason"}
    t = request_tunnel(h.auth, tokens.access_token, body)
    assert t.node_id == a.node_id
    assert request_tunnel(h.auth, tokens.access_token, body).id == t.id
    with pytest.raises(AuthError, match="tunnel_idempotency_conflict"):
        request_tunnel(h.auth, tokens.access_token, body.model_copy(update={"node_id": b.node_id}))
    with pytest.raises(AuthError, match="tunnel_recovery_not_ready"):
        recover_expired(h.auth, tokens.access_token, t.id)
    with pytest.raises(AuthError, match="tunnel_exists"):
        request_tunnel(
            h.auth,
            tokens.access_token,
            body.model_copy(update={"node_id": b.node_id, "idempotency_key": uuid4()}),
        )
    expire(h, t.id, a.node_id)
    with transaction(h.engine) as session:
        pending = session.get(Tunnel, t.id)
        assert pending is not None
        pending.lease_deadline = datetime.now(UTC) - timedelta(seconds=6)
    with pytest.raises(AuthError, match="tunnel_recovery_not_ready"):
        recover_expired(h.auth, tokens.access_token, t.id)
    expire(h, t.id, a.node_id)
    with transaction(h.engine) as session:
        cmd = session.scalar(select(PeerCommand).where(PeerCommand.tunnel_id == t.id))
        assert cmd is not None
        cmd.created_at = datetime.now(UTC) - timedelta(seconds=120)
    assert monitor_database(h.engine, datetime.now(UTC))["peer_command_stalled"] == 1
    result = h.client.post(f"/api/v1/tunnels/{t.id}/recover", headers=headers(tokens))
    assert result.status_code == 200 and result.json()["state"] == "revoked"
    assert recover_expired(h.auth, tokens.access_token, t.id).state == "revoked"
    assert "peer_command_stalled" not in monitor_database(h.engine, datetime.now(UTC))
    catalog = h.client.get("/api/v1/nodes", headers=headers(tokens)).json()
    assert [r["reason"] for r in catalog["nodes"]] == ["heartbeat_stale", "available"]
    # New intent, same device and unused proof is NOT reused: first proof was consumed.
    with transaction(h.engine) as session:
        principal = h.auth.authenticate(session, tokens.access_token, "tunnels:manage")
        challenge = key_challenge(session, principal, body.device_id)
    shared = private.exchange(
        X25519PublicKey.from_public_bytes(base64.b64decode(challenge.ephemeral_public_key))
    )
    second_proof = hmac.new(shared, challenge.transcript.encode(), hashlib.sha256).hexdigest()
    second = request_tunnel(
        h.auth,
        tokens.access_token,
        body.model_copy(
            update={
                "idempotency_key": uuid4(),
                "node_id": b.node_id,
                "proof_id": challenge.id,
                "proof_response": SecretStr(second_proof),
            }
        ),
    )
    assert second.node_id == b.node_id and second.state == "provision_pending"
    with transaction(h.engine) as session:
        second_command = session.scalar(
            select(PeerCommand).where(PeerCommand.tunnel_id == second.id)
        )
        assert second_command is not None
        second_command_id = second_command.id
    activated = h.client.post(
        PREFIX + "ack",
        headers=agent_headers(nodes[1][0]),
        json={
            "contract_version": 2,
            "command_id": str(second_command_id),
            "peer_id": str(second.id),
            "peer_version": 1,
            "operation": "add_peer",
            "server_public_key": b.server_public_key,
        },
    )
    assert activated.status_code == 200
    config = h.client.get(f"/api/v1/tunnels/{second.id}/config", headers=headers(tokens))
    assert config.status_code == 200
    correct_endpoint = (
        b.endpoint in config.json()["config"] and a.endpoint not in config.json()["config"]
    )
    assert correct_endpoint  # Never include profile material in assertion diagnostics.
    # Agent B key/runtime cannot be used to poll A.
    bad = h.client.post(
        PREFIX + "poll",
        headers=agent_headers(nodes[0][0]),
        json={
            "contract_version": 2,
            "capabilities": ["peer-lease.v2", "kernel-udp-budget.v1"],
            "server_public_key": b.server_public_key,
            "runtime_id": str(b.runtime_id),
            "observed_public_keys": [],
        },
    )
    assert bad.status_code == 409
    with transaction(h.engine) as session:
        r = session.get(TunnelReservation, t.id)
        assert r is not None and r.charged_bytes == 1048576 and r.observed_bytes is None
        quota = LedgerService(h.engine).quota(session, owner)
        assert quota.used_bytes == 1048576 and quota.reserved_bytes == 1048576
        assert LedgerService(h.engine).reconcile(session, owner).consistent
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_id == t.id)
            )
            == 1
        )
        command = session.scalar(select(PeerCommand).where(PeerCommand.tunnel_id == t.id))
        assert command is not None and command.acknowledged_at is None


def test_recovery_ten_concurrent_requests_single_debit(harness: Harness, tmp_path: Path) -> None:
    h = harness
    a = setup_nodes(h, tmp_path / "nodes.json")[0][1]
    token = login(h).access_token
    owner, body, _ = intent(h, token, a.node_id)
    t = request_tunnel(h.auth, token, body)
    expire(h, t.id, a.node_id)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: recover_expired(h.auth, token, t.id).state, range(10)))
    assert results == ["revoked"] * 10
    with transaction(h.engine) as session:
        assert LedgerService(h.engine).quota(session, owner).used_bytes == 1048576
        assert LedgerService(h.engine).reconcile(session, owner).consistent


def test_recovery_owner_freshness_and_late_ack(harness: Harness, tmp_path: Path) -> None:
    h = harness
    provisioned, a = setup_nodes(h, tmp_path / "nodes.json")[0]
    tokens = login(h)
    _, body, _ = intent(h, tokens.access_token, a.node_id)
    t = request_tunnel(h.auth, tokens.access_token, body)
    other = login(h, 999)
    assert (
        h.client.post(f"/api/v1/tunnels/{t.id}/recover", headers=headers(other)).status_code == 404
    )
    assert h.client.get("/api/v1/nodes").status_code == 401
    mini = headers(exchange(h, payload(h)))
    assert h.client.get("/api/v1/nodes", headers=mini).status_code == 403
    assert h.client.post(f"/api/v1/tunnels/{t.id}/recover", headers=mini).status_code == 403
    expire(h, t.id, a.node_id)
    with transaction(h.engine) as session:
        n = session.get(VPNNode, a.node_id)
        assert n is not None
        n.last_seen_at = datetime.now(UTC)
    with pytest.raises(AuthError, match="tunnel_recovery_not_ready"):
        recover_expired(h.auth, tokens.access_token, t.id)
    expire(h, t.id, a.node_id)
    recover_expired(h.auth, tokens.access_token, t.id)
    with transaction(h.engine) as session:
        cmd = session.scalar(select(PeerCommand).where(PeerCommand.tunnel_id == t.id))
        assert cmd is not None
        ack = LifecycleAck(
            contract_version=2,
            command_id=cmd.id,
            peer_id=t.id,
            peer_version=1,
            operation="add_peer",
            server_public_key=a.server_public_key,
        )
    result = h.client.post(
        PREFIX + "ack", headers=agent_headers(provisioned), json=ack.model_dump(mode="json")
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "peer_version_conflict"


def test_ack_and_recovery_race_preserves_one_settlement(harness: Harness, tmp_path: Path) -> None:
    h = harness
    a = setup_nodes(h, tmp_path / "nodes.json")[0][1]
    token = login(h).access_token
    owner, body, _ = intent(h, token, a.node_id)
    t = request_tunnel(h.auth, token, body)
    with transaction(h.engine) as session:
        tunnel = session.get(Tunnel, t.id)
        assert tunnel is not None
        enqueue_revoke(session, tunnel)
        cmd = session.scalar(
            select(PeerCommand).where(
                PeerCommand.tunnel_id == t.id, PeerCommand.operation == "remove_peer"
            )
        )
        assert cmd is not None
        ack = LifecycleAck(
            contract_version=2,
            command_id=cmd.id,
            peer_id=t.id,
            peer_version=2,
            operation="remove_peer",
            server_public_key=a.server_public_key,
            rx_bytes=17,
        )
    expire(h, t.id, a.node_id)

    def settle() -> None:
        from vpn_api.nodes.service import NodeError

        try:
            with transaction(h.engine) as session:
                apply_ack(session, a.node_id, ack)
        except NodeError as exc:
            assert exc.code == "peer_version_conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        ack_future = pool.submit(settle)
        recovery_future = pool.submit(recover_expired, h.auth, token, t.id)
        ack_future.result(timeout=20)
        recovery_future.result(timeout=20)
    with transaction(h.engine) as session:
        assert LedgerService(h.engine).quota(session, owner).used_bytes in (17, 1048576)
        assert LedgerService(h.engine).reconcile(session, owner).consistent
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_id == t.id)
            )
            == 1
        )


def test_auto_selection_skips_stale_and_explicit_unavailable_fails(
    harness: Harness, tmp_path: Path
) -> None:
    h = harness
    a, b = [p for _, p in setup_nodes(h, tmp_path / "nodes.json")]
    token = login(h).access_token
    _, body, _ = intent(h, token)
    with transaction(h.engine) as session:
        n = session.get(VPNNode, a.node_id)
        assert n is not None
        n.last_seen_at = None
    for node_id in (a.node_id, uuid4()):
        with pytest.raises(AuthError, match="tunnel_node_unavailable"):
            request_tunnel(h.auth, token, body.model_copy(update={"node_id": node_id}))
    assert request_tunnel(h.auth, token, body).node_id == b.node_id


def test_manifest_fail_closed(harness: Harness, tmp_path: Path) -> None:
    h = harness
    selected = setup_nodes(h, tmp_path / "nodes.json")[0][1]
    for endpoint in (
        "127.0.0.1:80",
        "224.0.0.1:51892",
        "10.0.0.1:51892",
        "example.org:443",
        "8.8.8.8:0",
        "8.8.8.8:65536",
        "8.8.8.8:0443",
        "https://8.8.8.8:443",
        "::1:443",
    ):
        with pytest.raises(ValidationError):
            NodeProfile.model_validate(selected.model_dump() | {"endpoint": endpoint})
    with pytest.raises(ValidationError):
        NodeManifest(version=1, profiles=[selected, selected])
    path = tmp_path / "nodes.json"
    path.write_text('{"version": 2, "profiles": []}')
    with pytest.raises(AuthError, match="tunnel_profile_unavailable"):
        profiles(h.auth)
    tokens = login(h)
    result = h.client.get("/api/v1/nodes", headers=headers(tokens))
    assert result.status_code == 503 and "profiles" not in result.text


def test_recovery_rolls_back_settlement_failure(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    a = setup_nodes(h, tmp_path / "nodes.json")[0][1]
    token = login(h).access_token
    owner, body, _ = intent(h, token, a.node_id)
    t = request_tunnel(h.auth, token, body)
    expire(h, t.id, a.node_id)

    def fail(*args: object, **kwargs: object) -> None:
        raise AuthError("test_only_transaction_failure", 503)

    with monkeypatch.context() as scoped:
        scoped.setattr(LedgerService, "apply_tunnel_settlement_in_transaction", fail)
        with pytest.raises(AuthError, match="test_only_transaction_failure"):
            recover_expired(h.auth, token, t.id)
    with transaction(h.engine) as session:
        reservation = session.get(TunnelReservation, t.id)
        assert reservation is not None and reservation.settled_at is None
        assert LedgerService(h.engine).quota(session, owner).reserved_bytes == 1048576
        assert LedgerService(h.engine).quota(session, owner).used_bytes == 0
    assert recover_expired(h.auth, token, t.id).state == "revoked"


def test_concurrent_node_capacity_is_one_peer(harness: Harness, tmp_path: Path) -> None:
    h = harness
    a = setup_nodes(h, tmp_path / "nodes.json")[0][1]
    token_a, token_b = login(h).access_token, login(h, 789).access_token
    _, body_a, _ = intent(h, token_a, a.node_id)
    _, body_b, _ = intent(h, token_b, a.node_id)

    def create(token: str, body: TunnelInput) -> str:
        try:
            return request_tunnel(h.auth, token, body).state
        except AuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create, token_a, body_a)
        second = pool.submit(create, token_b, body_b)
        assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
            "provision_pending",
            "tunnel_node_capacity",
        ]
    with transaction(h.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Tunnel)
                .where(Tunnel.node_id == a.node_id, Tunnel.state != "revoked")
            )
            == 1
        )
