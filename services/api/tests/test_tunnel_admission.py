"""Real PostgreSQL and real X25519; node transport/native operations are synthetic here."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from pydantic import SecretStr
from sqlalchemy import update
from test_authentication import Harness, harness, headers, login
from test_referrals import bind, campaign, claim, code, referral_id
from test_tunnels import agent_headers

from vpn_api.auth.security import AuthError
from vpn_api.db.connection import transaction
from vpn_api.db.models import DeviceKeyProof, Tunnel, TunnelReservation
from vpn_api.devices import DeviceInput, register_device
from vpn_api.nodes.contracts import ProvisionNodeInput
from vpn_api.nodes.service import NodeRegistry
from vpn_api.quota.contracts import LedgerCommand
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.admission import NodeProfile
from vpn_api.tunnels.contracts import TunnelInput
from vpn_api.tunnels.service import request_tunnel
from vpn_api.usage.contracts import UsageError
from vpn_api.usage.service import UsageService

__all__ = ["harness"]
pytestmark = pytest.mark.postgres
PREFIX = "/api/internal/v1/agent/peers/lifecycle/"


def test_full_admission_lease_revoke_and_reservation(harness: Harness, tmp_path: Path) -> None:
    h = harness
    inviter = headers(login(h, 111))
    invitation = code(h, inviter)
    cid = campaign(h)
    tokens = login(h)
    assert bind(h, headers(tokens), invitation, cid).status_code == 200
    private = X25519PrivateKey.generate()
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    device = register_device(
        h.auth,
        tokens.access_token,
        DeviceInput(platform="android", key_type="x25519", public_key=public),
    )
    provisioned = NodeRegistry(h.engine).provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="synthetic-lease", region="test")
    )
    selected = NodeProfile(
        node_id=provisioned.node_id,
        runtime_id=uuid4(),
        server_public_key=base64.b64encode(
            X25519PrivateKey.generate().public_key().public_bytes_raw()
        ).decode(),
        header_protection_key=SecretStr(base64.b64encode(secrets.token_bytes(32)).decode()),
    )
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            selected.model_dump(mode="json")
            | {"header_protection_key": selected.header_protection_key.get_secret_value()}
        )
    )
    path.chmod(0o600)
    h.settings.tunnel_profile_file = path
    user_id = UUID(h.client.get("/api/v1/me", headers=headers(tokens)).json()["id"])
    rid = referral_id(h, user_id)
    assert claim(h, inviter, rid).json()["error"]["code"] == "referral_not_qualified"
    ledger = LedgerService(h.engine)
    ledger.apply(
        user_id,
        LedgerCommand(
            kind="credit", amount_bytes=1048576, source_id=uuid4(), idempotency_key=uuid4()
        ),
    )
    poll_body = {
        "contract_version": 2,
        "capabilities": ["peer-lease.v2", "kernel-udp-budget.v1"],
        "server_public_key": selected.server_public_key,
        "runtime_id": str(selected.runtime_id),
        "observed_public_keys": [],
    }

    def poll() -> list[dict[str, object]]:
        response = h.client.post(
            PREFIX + "poll", headers=agent_headers(provisioned), json=poll_body
        )
        assert response.status_code == 200, response.text
        result: list[dict[str, object]] = response.json()["commands"]
        return result

    assert poll() == []
    with pytest.raises(AuthError, match="device_key_proof_required"):
        request_tunnel(
            h.auth, tokens.access_token, TunnelInput(device_id=device.id, idempotency_key=uuid4())
        )
    response = h.client.post(f"/api/v1/tunnels/key-challenge/{device.id}", headers=headers(tokens))
    assert response.status_code == 200
    challenge = response.json()
    shared = private.exchange(
        X25519PublicKey.from_public_bytes(base64.b64decode(challenge["ephemeral_public_key"]))
    )
    proof = hmac.new(shared, challenge["transcript"].encode(), hashlib.sha256).hexdigest()
    body = TunnelInput(
        device_id=device.id,
        idempotency_key=uuid4(),
        proof_id=UUID(challenge["id"]),
        proof_response=SecretStr(proof),
    )
    rejected = body.model_copy(update={"proof_response": SecretStr("0" * 64)})
    with pytest.raises(AuthError, match="device_key_proof_invalid"):
        request_tunnel(h.auth, tokens.access_token, rejected)
    tunnel = request_tunnel(h.auth, tokens.access_token, body)
    assert tunnel.state == "provision_pending"
    with pytest.raises(UsageError, match="usage_managed_by_lease"):
        UsageService(h.engine).bind(provisioned.node_id, tunnel.id, device.id)
    assert request_tunnel(h.auth, tokens.access_token, body).id == tunnel.id
    with transaction(h.engine) as session:
        assert ledger.quota(session, user_id).reserved_bytes == 1048576
        assert ledger.quota(session, user_id).available_bytes == 0
    refused = ledger.apply(
        user_id,
        LedgerCommand(kind="debit", amount_bytes=1, source_id=uuid4(), idempotency_key=uuid4()),
    )
    assert refused.status == "rejected"
    with transaction(h.engine) as session:
        assert ledger.reconcile(session, user_id).consistent
        assert UsageService(h.engine).audit_user(session, user_id).consistent
    assert (
        h.client.get(f"/api/v1/tunnels/{tunnel.id}/config", headers=headers(tokens)).status_code
        == 409
    )
    pending = poll()
    assert len(pending) == 1 and pending[0]["operation"] == "add_peer"
    assert poll() == pending
    command = pending[0]
    ack = {
        "contract_version": 2,
        "command_id": command["command_id"],
        "peer_id": str(tunnel.id),
        "peer_version": 1,
        "operation": "add_peer",
        "server_public_key": selected.server_public_key,
    }
    for _ in range(2):
        result = h.client.post(PREFIX + "ack", headers=agent_headers(provisioned), json=ack)
        assert result.status_code == 200 and result.json()["state"] == "active"
    config = h.client.get(f"/api/v1/tunnels/{tunnel.id}/config", headers=headers(tokens))
    assert config.status_code == 200
    assert "PrivateKey" not in config.text and selected.server_public_key in config.json()["config"]
    assert config.headers["cache-control"] == "no-store"
    poll_body["observed_public_keys"] = [public]
    assert poll() == []
    revoked = h.client.delete(f"/api/v1/tunnels/{tunnel.id}", headers=headers(tokens))
    assert revoked.json()["state"] == "revoke_pending"
    stale = h.client.post(PREFIX + "ack", headers=agent_headers(provisioned), json=ack)
    assert stale.status_code == 409
    removal = poll()[0]
    final = ack | {
        "command_id": removal["command_id"],
        "peer_version": 2,
        "operation": "remove_peer",
        "rx_bytes": 70,
        "tx_bytes": 30,
    }
    for _ in range(2):
        result = h.client.post(PREFIX + "ack", headers=agent_headers(provisioned), json=final)
        assert result.status_code == 200 and result.json()["state"] == "revoked"
    assert poll() == []
    with transaction(h.engine) as session:
        quota = ledger.quota(session, user_id)
        assert quota.reserved_bytes == 0 and quota.used_bytes == 100
        assert quota.available_bytes == 1048476
        assert ledger.reconcile(session, user_id).consistent
        assert UsageService(h.engine).audit_user(session, user_id).consistent
        receipt = session.get(TunnelReservation, tunnel.id)
        assert receipt is not None and receipt.observed_bytes == 100
    # Signed synthetic node settlement qualifies the referral; no physical VPN claim.
    referral_receipt = claim(h, inviter, rid)
    assert referral_receipt.status_code == 200, referral_receipt.text
    assert referral_receipt.json()["reward_bytes"] == 17
    assert claim(h, inviter, rid).json() == referral_receipt.json()
    # Even a fresh request cannot consume the previous proof twice.
    ledger.apply(
        user_id,
        LedgerCommand(kind="credit", amount_bytes=100, source_id=uuid4(), idempotency_key=uuid4()),
    )
    with pytest.raises(AuthError, match="device_key_proof_required"):
        request_tunnel(
            h.auth, tokens.access_token, body.model_copy(update={"idempotency_key": uuid4()})
        )


def test_key_challenge_low_order_and_expiry(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    low_order = base64.b64encode(b"\x01" + bytes(31)).decode()
    device = register_device(
        h.auth,
        tokens.access_token,
        DeviceInput(platform="android", key_type="x25519", public_key=low_order),
    )
    result = h.client.post(f"/api/v1/tunnels/key-challenge/{device.id}", headers=headers(tokens))
    assert result.status_code == 409 and result.json()["error"]["code"] == "device_key_unusable"


def test_runtime_change_retires_peer_and_lost_counters_settle(
    harness: Harness, tmp_path: Path
) -> None:
    # Reuse a real auth/device but seed the accepted node-side state explicitly as synthetic.
    h = harness
    tokens = login(h)
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
    owner = UUID(h.client.get("/api/v1/me", headers=headers(tokens)).json()["id"])
    provisioned = NodeRegistry(h.engine).provision(
        ProvisionNodeInput(idempotency_key=uuid4(), name="synthetic-restart", region="test")
    )
    server_public = base64.b64encode(
        X25519PrivateKey.generate().public_key().public_bytes_raw()
    ).decode()
    runtime = uuid4()
    selected = {
        "node_id": str(provisioned.node_id),
        "runtime_id": str(runtime),
        "server_public_key": server_public,
        "header_protection_key": base64.b64encode(secrets.token_bytes(32)).decode(),
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(selected))
    path.chmod(0o600)
    h.settings.tunnel_profile_file = path
    LedgerService(h.engine).apply(
        owner,
        LedgerCommand(
            kind="credit", amount_bytes=65536, source_id=uuid4(), idempotency_key=uuid4()
        ),
    )
    first_poll = {
        "contract_version": 2,
        "capabilities": ["peer-lease.v2", "kernel-udp-budget.v1"],
        "server_public_key": server_public,
        "runtime_id": str(runtime),
        "observed_public_keys": [],
    }
    assert (
        h.client.post(
            PREFIX + "poll", headers=agent_headers(provisioned), json=first_poll
        ).status_code
        == 200
    )
    with transaction(h.engine) as session:
        tunnel = Tunnel(
            user_id=owner,
            device_id=device.id,
            idempotency_key=uuid4(),
            node_id=provisioned.node_id,
            state="active",
            peer_version=1,
            budget_bytes=65536,
            lease_deadline=datetime.now(UTC) + timedelta(seconds=60),
            created_at=datetime.now(UTC),
            runtime_id=runtime,
        )
        session.add(tunnel)
        session.flush()
        tunnel_id = tunnel.id
        session.add(TunnelReservation(tunnel_id=tunnel_id, user_id=owner, amount_bytes=65536))
    replacement = uuid4()
    path.write_text(json.dumps(selected | {"runtime_id": str(replacement)}))
    result = h.client.post(
        PREFIX + "poll",
        headers=agent_headers(provisioned),
        json=first_poll | {"runtime_id": str(replacement)},
    )
    assert result.status_code == 200
    removal = result.json()["commands"][0]
    assert removal["operation"] == "remove_peer" and removal["peer_version"] == 2
    result = h.client.post(
        PREFIX + "ack",
        headers=agent_headers(provisioned),
        json={
            "contract_version": 2,
            "command_id": removal["command_id"],
            "peer_id": str(tunnel_id),
            "peer_version": 2,
            "operation": "remove_peer",
            "server_public_key": server_public,
            "counters_lost": True,
        },
    )
    assert result.status_code == 200 and result.json()["state"] == "revoked"
    changed = h.client.post(
        PREFIX + "ack",
        headers=agent_headers(provisioned),
        json={
            "contract_version": 2,
            "command_id": removal["command_id"],
            "peer_id": str(tunnel_id),
            "peer_version": 2,
            "operation": "remove_peer",
            "server_public_key": server_public,
            "counters_lost": False,
            "rx_bytes": 65536,
        },
    )
    assert changed.status_code == 409
    with transaction(h.engine) as session:
        assert LedgerService(h.engine).quota(session, owner).used_bytes == 65536


def test_expired_proof_is_rejected_in_transaction(harness: Harness) -> None:
    from vpn_api.tunnels.admission import consume_proof, key_challenge

    h = harness
    tokens = login(h)
    key = X25519PrivateKey.generate()
    device = register_device(
        h.auth,
        tokens.access_token,
        DeviceInput(
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        ),
    )
    with transaction(h.engine) as session:
        principal = h.auth.authenticate(session, tokens.access_token, "tunnels:manage")
        challenge = key_challenge(session, principal, device.id)
        session.flush()
        session.execute(
            update(DeviceKeyProof)
            .where(DeviceKeyProof.id == challenge.id)
            .values(
                created_at=datetime.now(UTC) - timedelta(minutes=5),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
    with (
        pytest.raises(AuthError, match="device_key_proof_required"),
        transaction(h.engine) as session,
    ):
        consume_proof(session, principal, device.id, challenge.id, SecretStr("0" * 64))
