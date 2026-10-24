"""Real PostgreSQL/X25519; synthetic node ACKs and fake Telegram delivery, no VPN."""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness, harness, headers, login
from test_migrations import migration_config
from test_operations import binaries, key
from test_quota import LedgerHarness, event
from test_subscriptions import grant as subscription_grant
from test_subscriptions import plan
from test_tunnels import agent_headers
from test_window_daemon import daemon
from test_window_funding import close
from test_window_runtime import TestGate, TestPeer

from vpn_api import operations as ops
from vpn_api.auth.contracts import TokenResponse
from vpn_api.auth.security import AuthError
from vpn_api.db.connection import transaction
from vpn_api.db.models import PeerConnection, PeerRuntimeEpoch, PeerWindowHold, VPNNode
from vpn_api.devices import DeviceInput, register_device
from vpn_api.main import create_app
from vpn_api.nodes.contracts import ProvisionNodeInput, ProvisionNodeResult
from vpn_api.nodes.service import NodeError, NodeRegistry
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.subscriptions import EntitlementService
from vpn_api.tunnels.admission import key_challenge
from vpn_api.tunnels.v3_contracts import CAPABILITIES, ClosedWindow, WindowGrant
from vpn_api.tunnels.window_profiles import WindowNode, WindowSlot
from vpn_api.tunnels.window_runtime import WindowJournal, WindowRuntime
from vpn_api.tunnels.window_service import (
    ConnectionInput,
    EpochStart,
    RenewalInput,
    WindowCommands,
    WindowPoll,
    create_connection,
    current_grant,
    poll_windows,
    renew_connection,
    start_epoch,
)

__all__ = ["harness", "daemon", "binaries", "key"]
pytestmark = pytest.mark.postgres
BUDGET = 65536


def verify(h: Harness, session: object, node: ProvisionNodeResult) -> None:
    # Every simulated agent call still exercises the actual scoped credential/nonce verifier.
    from sqlalchemy.orm import Session

    assert isinstance(session, Session) and node.secret is not None
    NodeRegistry.verify_and_consume(
        session,
        node_id=node.node_id,
        timestamp=str(int(datetime.now(UTC).timestamp())),
        nonce=uuid4(),
        credential=node.secret,
    )


def inventory(h: Harness, slots: int = 2) -> tuple[ProvisionNodeResult, WindowNode]:
    node = NodeRegistry(h.engine).provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="TEST ONLY window node", region="test")
    )
    selected = WindowNode(
        node_id=node.node_id,
        slots=[
            WindowSlot(
                slot_id=slot,
                endpoint=f"85.209.132.205:{51900 + slot}",
                server_public_key=base64.b64encode(
                    X25519PrivateKey.generate().public_key().public_bytes_raw()
                ).decode(),
                header_protection_key=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
            )
            for slot in range(1, slots + 1)
        ],
    )
    return node, selected


def boot(
    h: Harness, node: ProvisionNodeResult, selected: WindowNode, previous: UUID | None = None
) -> UUID:
    runtime = uuid4()
    body = EpochStart(
        contract_version=3,
        runtime_id=runtime,
        previous_runtime_id=previous,
        capabilities=sorted(CAPABILITIES),
        server_public_keys=[s.server_public_key for s in selected.slots],
        gates_closed=True,
    )
    for _ in range(2):
        with transaction(h.engine) as session:
            verify(h, session, node)
            assert start_epoch(session, h.auth, selected, body) == runtime
    return runtime


@dataclass
class UserRequest:
    tokens: TokenResponse
    user_id: UUID
    body: ConnectionInput


def request(h: Harness, selected: WindowNode, subject: int = 1111) -> UserRequest:
    tokens = login(h, subject)
    private = X25519PrivateKey.generate()
    device = register_device(
        h.auth,
        tokens.access_token,
        DeviceInput(
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(private.public_key().public_bytes_raw()).decode(),
        ),
    )
    with transaction(h.engine) as session:
        principal = h.auth.authenticate(session, tokens.access_token, "tunnels:manage")
        challenge = key_challenge(session, principal, device.id)
        user_id = principal.user_id
    shared = private.exchange(
        X25519PublicKey.from_public_bytes(base64.b64decode(challenge.ephemeral_public_key))
    )
    proof = hmac.new(shared, challenge.transcript.encode(), hashlib.sha256).hexdigest()
    LedgerService(h.engine).apply(user_id, event("credit", BUDGET * 5))
    return UserRequest(
        tokens,
        user_id,
        ConnectionInput(
            device_id=device.id,
            node_id=selected.node_id,
            idempotency_key=uuid4(),
            budget_bytes=BUDGET,
            proof_id=challenge.id,
            proof_response=SecretStr(proof),
        ),
    )


def create(h: Harness, selected: WindowNode, user: UserRequest) -> tuple[UUID, WindowGrant]:
    with transaction(h.engine) as session:
        connection = create_connection(
            session, h.auth, user.tokens.access_token, selected, user.body
        )
        return connection.id, current_grant(session, connection)


def poll(
    h: Harness,
    node: ProvisionNodeResult,
    selected: WindowNode,
    runtime: UUID,
    *,
    active: list[UUID] | None = None,
    receipts: list[ClosedWindow] | None = None,
) -> WindowCommands:
    with transaction(h.engine) as session:
        verify(h, session, node)
        return poll_windows(
            session,
            selected,
            WindowPoll(runtime_id=runtime, active_windows=active or [], receipts=receipts or []),
        )


def test_admission_replay_renew_and_exact_settlement(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    runtime = boot(h, node, selected)
    user = request(h, selected)
    connection_id, first = create(h, selected, user)
    assert create(h, selected, user) == (connection_id, first)
    assert poll(h, node, selected, runtime).commands[0].grant == first
    assert poll(h, node, selected, runtime, active=[first.window_id]).commands == []
    renewal = RenewalInput(
        previous_window_id=first.window_id, idempotency_key=uuid4(), budget_bytes=BUDGET
    )
    grants = []
    for _ in range(2):
        with transaction(h.engine) as session:
            hold = renew_connection(
                session, h.auth, user.tokens.access_token, selected, connection_id, renewal
            )
            grants.append(WindowGrant.model_validate(hold.grant))
    second = grants[0]
    assert grants[0] == grants[1] and second.generation == 2
    assert poll(h, node, selected, runtime).commands[0].grant == second
    receipt = close(first, 123)
    for _ in range(2):
        result = poll(h, node, selected, runtime, active=[second.window_id], receipts=[receipt])
        assert result.acknowledged_receipts == [receipt]
    poll(h, node, selected, runtime, receipts=[close(second, 234)])
    with transaction(h.engine) as session:
        connection = session.get(PeerConnection, connection_id)
        assert connection is not None and connection.state == "closed"
        ledger = LedgerService(h.engine)
        assert ledger._state(session, user.user_id).used_bytes == 357
        assert ledger._state(session, user.user_id).reserved_bytes == 0
        assert ledger.reconcile(session, user.user_id).consistent


def test_two_users_claim_distinct_slots_and_third_is_denied(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    runtime = boot(h, node, selected)
    users = [request(h, selected, subject) for subject in (111, 222, 333)]
    barrier = Barrier(3)

    def race(index: int) -> tuple[UUID, WindowGrant] | str:
        barrier.wait(timeout=10)
        try:
            return create(h, selected, users[index])
        except AuthError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(race, range(3)))
    accepted = [result for result in results if isinstance(result, tuple)]
    assert len(accepted) == 2 and results.count("window_node_capacity") == 1
    assert {result[1].slot_id for result in accepted} == {1, 2}
    commands = poll(h, node, selected, runtime).commands
    assert {command.grant.window_id for command in commands} == {
        result[1].window_id for result in accepted
    }
    for user in users:
        with transaction(h.engine) as session:
            assert LedgerService(h.engine).reconcile(session, user.user_id).consistent


def test_restart_requires_old_receipts_and_rejects_old_polls(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    old_runtime = boot(h, node, selected)
    user = request(h, selected)
    connection_id, first = create(h, selected, user)
    poll(h, node, selected, old_runtime, active=[first.window_id])
    new_runtime = boot(h, node, selected, old_runtime)
    with pytest.raises(NodeError, match="window_runtime_mismatch"):
        poll(h, node, selected, old_runtime)
    result = poll(h, node, selected, new_runtime)
    assert not result.ready and [(cmd.operation, cmd.grant) for cmd in result.commands] == [
        ("recover", first)
    ]
    result = poll(h, node, selected, new_runtime, receipts=[close(first, None)])
    assert result.ready
    with transaction(h.engine) as session:
        connection = session.get(PeerConnection, connection_id)
        assert connection is not None and connection.state == "closed"
        quota = LedgerService(h.engine)._state(session, user.user_id)
        assert quota.used_bytes == 0 and quota.reserved_bytes == BUDGET
        assert LedgerService(h.engine).reconcile(session, user.user_id).consistent


def test_epoch_cas_and_old_epoch_cannot_return(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    original = boot(h, node, selected)
    current = boot(h, node, selected, original)
    with pytest.raises(NodeError, match="window_epoch_compare_failed"):
        boot(h, node, selected, original)
    with transaction(h.engine) as session:
        verify(h, session, node)
        old = EpochStart(
            contract_version=3,
            runtime_id=original,
            previous_runtime_id=None,
            capabilities=sorted(CAPABILITIES),
            server_public_keys=[s.server_public_key for s in selected.slots],
            gates_closed=True,
        )
        with pytest.raises(NodeError, match="window_epoch_conflict"):
            start_epoch(session, h.auth, selected, old)
        row = session.get(VPNNode, node.node_id)
        assert row is not None and row.peer_runtime_id == current


def test_device_revoke_enqueues_v3_and_invalidates_user_session(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    runtime = boot(h, node, selected)
    user = request(h, selected)
    _, first = create(h, selected, user)
    assert (
        h.client.delete(
            f"/api/v1/devices/{user.body.device_id}", headers=headers(user.tokens)
        ).status_code
        == 204
    )
    result = poll(h, node, selected, runtime)
    assert result.commands[0].operation == "revoke" and result.commands[0].grant == first
    with pytest.raises(AuthError):
        create(h, selected, user)
    result = poll(h, node, selected, runtime, receipts=[close(first, 0)])
    assert result.commands == []


def test_subscription_revoke_closes_v3_without_fake_usage(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    runtime = boot(h, node, selected)
    user = request(h, selected)
    local = LedgerHarness(h.engine, user.user_id, LedgerService(h.engine))
    subscription_id = subscription_grant(local, plan(local, quota=BUDGET * 2))
    _, first = create(h, selected, user)
    with transaction(h.engine) as session:
        EntitlementService(h.engine).revoke(
            session,
            user.user_id,
            subscription_id,
            uuid4(),
            actor="test-operator",
            reason="Synthetic subscription revoke",
        )
    result = poll(h, node, selected, runtime)
    assert result.commands[0].operation == "revoke"
    poll(h, node, selected, runtime, receipts=[close(first, 123)])
    with transaction(h.engine) as session:
        quota = local.service._state(session, user.user_id)
        assert quota.used_bytes == 123 and quota.unavailable_bytes == BUDGET * 2 - 123
        assert local.service.reconcile(session, user.user_id).consistent


def test_cross_owner_and_cross_node_receipts_fail_closed(harness: Harness) -> None:
    h = harness
    node, selected = inventory(h)
    runtime = boot(h, node, selected)
    first, other = request(h, selected, 111), request(h, selected, 222)
    connection_id, grant = create(h, selected, first)
    with pytest.raises(AuthError, match="not_found"), transaction(h.engine) as session:
        renew_connection(
            session,
            h.auth,
            other.tokens.access_token,
            selected,
            connection_id,
            RenewalInput(
                previous_window_id=grant.window_id, idempotency_key=uuid4(), budget_bytes=BUDGET
            ),
        )
    foreign_node, foreign_manifest = inventory(h)
    foreign_runtime = boot(h, foreign_node, foreign_manifest)
    with pytest.raises(NodeError, match="window_receipt_not_found"):
        poll(h, foreign_node, foreign_manifest, foreign_runtime, receipts=[close(grant, 123)])
    assert poll(h, node, selected, runtime).commands[0].grant == grant
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(PeerWindowHold)) == 1


def enable_http(h: Harness, selected: WindowNode, directory: Path) -> None:
    # TEST ONLY operator inventory; no actual node/key material or network calls.
    path = directory / "window-manifest.json"
    data = {
        "version": 3,
        "nodes": [
            {
                "node_id": str(selected.node_id),
                "slots": [
                    {
                        **slot.model_dump(mode="json"),
                        "header_protection_key": slot.header_protection_key.get_secret_value(),
                    }
                    for slot in selected.slots
                ],
            }
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    h.settings.window_manifest_file = path
    h.settings.enable_window_tunnels = True


def test_http_v3_lifecycle_auth_replay_and_default_gate(harness: Harness, tmp_path: Path) -> None:
    h = harness
    node, selected = inventory(h)
    user = request(h, selected)
    prefix = "/api/internal/v3/windows"
    body = user.body.model_dump(mode="json") | {
        "proof_response": user.body.proof_response.get_secret_value()
    }
    assert (
        h.client.post("/api/v1/window-tunnels", headers=headers(user.tokens), json=body).status_code
        == 503
    )
    enable_http(h, selected, tmp_path)
    assert h.client.post("/api/v1/window-tunnels", json=body).status_code == 401
    runtime_id = uuid4()
    start_body = EpochStart(
        contract_version=3,
        runtime_id=runtime_id,
        previous_runtime_id=None,
        capabilities=sorted(CAPABILITIES),
        server_public_keys=[s.server_public_key for s in selected.slots],
        gates_closed=True,
    ).model_dump(mode="json")
    missing = h.client.post(
        prefix + "/start", headers={"X-Agent-Node-Id": str(node.node_id)}, json=start_body
    )
    assert missing.status_code == 401
    credentials = agent_headers(node)
    response = h.client.post(prefix + "/start", headers=credentials, json=start_body)
    assert response.status_code == 200 and response.json()["ready"]
    assert h.client.post(prefix + "/start", headers=credentials, json=start_body).status_code == 409
    assert h.client.post(prefix + "/inventory", headers=agent_headers(node)).json()[
        "runtime_id"
    ] == str(runtime_id)
    response = h.client.post("/api/v1/window-tunnels", headers=headers(user.tokens), json=body)
    assert response.status_code == 202 and response.json()["client_config"] is None
    connection_id = response.json()["id"]
    first = WindowGrant.model_validate(response.json()["window"])
    poll_body = WindowPoll(runtime_id=runtime_id).model_dump(mode="json")
    response = h.client.post(prefix + "/poll", headers=agent_headers(node), json=poll_body)
    assert response.status_code == 200 and response.json()["commands"][0]["operation"] == "window"
    poll_body["active_windows"] = [str(first.window_id)]
    assert (
        h.client.post(prefix + "/poll", headers=agent_headers(node), json=poll_body).status_code
        == 200
    )
    response = h.client.get(f"/api/v1/window-tunnels/{connection_id}", headers=headers(user.tokens))
    assert response.status_code == 200 and response.json()["state"] == "active"
    assert "HeaderProtectionKey" in response.json()["client_config"]
    assert "PrivateKey" not in response.json()["client_config"]
    stranger = login(h, 9999)
    assert (
        h.client.get(
            f"/api/v1/window-tunnels/{connection_id}", headers=headers(stranger)
        ).status_code
        == 404
    )
    response = h.client.delete(
        f"/api/v1/window-tunnels/{connection_id}", headers=headers(user.tokens)
    )
    assert response.status_code == 202 and response.json()["state"] == "revoke_pending"
    response = h.client.post(prefix + "/poll", headers=agent_headers(node), json=poll_body)
    assert response.json()["commands"][0]["operation"] == "revoke"
    poll_body["active_windows"] = []
    poll_body["receipts"] = [close(first, 123).model_dump(mode="json")]
    assert (
        h.client.post(prefix + "/poll", headers=agent_headers(node), json=poll_body).status_code
        == 200
    )
    response = h.client.get(f"/api/v1/window-tunnels/{connection_id}", headers=headers(user.tokens))
    assert response.json()["state"] == "closed" and response.json()["client_config"] is None
    assert not any("/api/internal/" in path for path in create_app(h.settings).openapi()["paths"])


def test_daemon_http_postgres_two_users_multiple_renewals_and_revoke(
    harness: Harness, tmp_path: Path, daemon: ModuleType
) -> None:
    """Actual daemon tick + HTTP + PostgreSQL + SQLite; TEST ONLY simulated kernel/peers."""
    h = harness
    node, selected = inventory(h)
    enable_http(h, selected, tmp_path)
    gates = {slot: TestGate() for slot in (1, 2)}
    peers = {slot: TestPeer() for slot in (1, 2)}
    journal = WindowJournal(sqlite3.connect(tmp_path / "node.sqlite"))
    runtime = WindowRuntime(
        node.node_id,
        journal,
        {slot: gate for slot, gate in gates.items()},
        {slot: peer for slot, peer in peers.items()},
    )
    runtime.startup()

    class HttpControl:
        def post(self, operation: str, payload: dict[str, object]) -> dict[str, Any]:
            response = h.client.post(
                "/api/internal/v3/windows/" + operation,
                headers=agent_headers(node),
                json=payload,
            )
            assert response.status_code == 200
            result = response.json()
            assert isinstance(result, dict)
            return result

    transport = HttpControl()
    daemon.register(runtime, transport, [slot.server_public_key for slot in selected.slots])
    users = [request(h, selected, subject) for subject in (111, 222)]
    connections = [create(h, selected, user) for user in users]
    daemon.tick(runtime, transport)
    daemon.tick(runtime, transport)
    expected = {user.user_id: 0 for user in users}
    for cycle in range(3):
        renewed = []
        for user, (connection_id, previous) in zip(users, connections, strict=True):
            gates[previous.slot_id].amount = 123 + cycle
            expected[user.user_id] += 123 + cycle
            response = h.client.post(
                f"/api/v1/window-tunnels/{connection_id}/renew",
                headers=headers(user.tokens),
                json={
                    "previous_window_id": str(previous.window_id),
                    "idempotency_key": str(uuid4()),
                    "budget_bytes": BUDGET,
                },
            )
            assert response.status_code == 202
            renewed.append((connection_id, WindowGrant.model_validate(response.json()["window"])))
        daemon.tick(runtime, transport)
        daemon.tick(runtime, transport)
        assert journal.pending() == []
        connections = renewed
    for user, (connection_id, current) in zip(users, connections, strict=True):
        gates[current.slot_id].amount = 456
        expected[user.user_id] += 456
        assert (
            h.client.delete(
                f"/api/v1/window-tunnels/{connection_id}", headers=headers(user.tokens)
            ).status_code
            == 202
        )
    daemon.tick(runtime, transport)
    daemon.tick(runtime, transport)
    assert journal.pending() == [] and not any(gate.live for gate in gates.values())
    for user in users:
        with transaction(h.engine) as session:
            ledger = LedgerService(h.engine)
            state = ledger._state(session, user.user_id)
            assert state.used_bytes == expected[user.user_id] and state.reserved_bytes == 0
            assert ledger.reconcile(session, user.user_id).consistent
    journal.db.close()


def test_connection_epoch_history_restore_and_protected_downgrade(
    harness: Harness, tmp_path: Path, binaries: Path, key: Path
) -> None:
    h = harness
    node, selected = inventory(h)
    runtime = boot(h, node, selected)
    user = request(h, selected)
    _, first = create(h, selected, user)
    poll(h, node, selected, runtime, receipts=[close(first, 123)])
    archive = tmp_path / "connections.fernet"
    backup = ops.make_backup(h.engine, binaries, archive, key)
    assert backup.tables["peer_connections"].rows == 1
    assert backup.tables["peer_runtime_epochs"].rows == 1
    assert ops.restore_drill(h.engine, binaries, archive, key)["accounts_reconciled"] == 1
    with pytest.raises(DBAPIError, match="window admission history"):
        command.downgrade(migration_config(h.engine), "0014_peer_windows")
    for sql in (
        "UPDATE peer_runtime_epochs SET manifest_digest=repeat('f',64)",
        "DELETE FROM peer_runtime_epochs",
        "TRUNCATE peer_connections",
    ):
        with pytest.raises(DBAPIError), h.engine.begin() as db:
            db.execute(text(sql))
    with transaction(h.engine) as session:
        assert session.get(PeerRuntimeEpoch, (node.node_id, runtime)) is not None
        assert LedgerService(h.engine).reconcile(session, user.user_id).consistent


@pytest.mark.parametrize("table", ["peer_window_holds", "peer_connections"])
def test_restore_audit_detects_altered_slot_binding(harness: Harness, table: str) -> None:
    h = harness
    node, selected = inventory(h)
    boot(h, node, selected)
    user = request(h, selected)
    connection_id, _ = create(h, selected, user)
    # TEST ONLY disposable superuser corruption; application role cannot bypass guards.
    statements = {
        "peer_window_holds": (
            "ALTER TABLE peer_window_holds DISABLE TRIGGER USER",
            "UPDATE peer_window_holds SET slot_id=2",
            "ALTER TABLE peer_window_holds ENABLE TRIGGER USER",
        ),
        "peer_connections": (
            "ALTER TABLE peer_connections DISABLE TRIGGER USER",
            "UPDATE peer_connections SET slot_id=2",
            "ALTER TABLE peer_connections ENABLE TRIGGER USER",
        ),
    }
    with h.engine.begin() as db:
        for statement in statements[table]:
            db.execute(text(statement))
    with transaction(h.engine) as session:
        assert not LedgerService(h.engine).reconcile(session, user.user_id).consistent
        connection = session.get(PeerConnection, connection_id)
        assert connection is not None
        with pytest.raises(QuotaError, match="window_inconsistent"):
            current_grant(session, connection)
