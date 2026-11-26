"""ADR-200 owner health summary: aggregate counts only. Real PostgreSQL; all data synthetic."""

import base64
import json
import secrets
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from test_admin import Harness, admin, authorization

from vpn_api.admin import HEALTH_STALLED_COMMAND_SECONDS, AdminHealthSummary
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    AdminAction,
    Device,
    PeerCommand,
    PeerConnection,
    PeerRuntimeEpoch,
    PeerWindowHold,
    Tunnel,
    User,
    VPNNode,
)
from vpn_api.nodes.catalog import HEARTBEAT_SECONDS

__all__ = ["admin"]

NODE_NAME = "TEST ONLY health node"


def device(user_id: object, created: object, **fields: object) -> Device:
    return Device(
        user_id=user_id,
        platform="android",
        key_type="x25519",
        public_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        created_at=created,
        **fields,
    )


def node(name: str, **fields: object) -> VPNNode:
    return VPNNode(
        provisioning_key=uuid4(),
        name=name,
        region="test",
        secret_digest=secrets.token_hex(32),
        **fields,
    )


def seed(h: Harness) -> dict[str, str]:
    """Synthetic rows; returns the identifiers that must never appear in the summary."""
    with transaction(h.engine) as session:
        now = database_now(session)
    created = now - timedelta(seconds=15)
    node_id, runtime_id, stale_node_id = uuid4(), uuid4(), uuid4()
    device_id, connection_id, window_id = uuid4(), uuid4(), uuid4()
    with transaction(h.engine) as session:
        session.add_all(
            [
                User(status="pending"),
                User(status="disabled"),
                node(NODE_NAME, id=node_id, peer_protocol_version=3, last_seen_at=now),
                node(
                    "TEST ONLY stale node",
                    id=stale_node_id,
                    last_seen_at=now - timedelta(seconds=HEARTBEAT_SECONDS + 60),
                ),
                node("TEST ONLY disabled node", status="disabled", last_seen_at=now),
            ]
        )
        session.flush()
        session.add(
            PeerRuntimeEpoch(
                node_id=node_id,
                runtime_id=runtime_id,
                previous_runtime_id=None,
                manifest_digest="0" * 64,
                created_at=created,
            )
        )
        second_device = device(h.user_id, created)
        session.add_all(
            [
                device(h.user_id, created, id=device_id),
                second_device,
                device(h.user_id, created, revoked_at=now),
            ]
        )
        session.flush()
        tunnels = [
            Tunnel(
                user_id=h.user_id,
                device_id=owner,
                idempotency_key=uuid4(),
                node_id=on_node,
                state="revoke_pending",
                peer_version=version,
                created_at=now,
            )
            for owner, on_node, version in (
                (device_id, node_id, 2),
                (second_device.id, stale_node_id, 1),
            )
        ]
        session.add_all(tunnels)
        session.flush()
        stalled = now - timedelta(seconds=HEALTH_STALLED_COMMAND_SECONDS + 30)
        # Tunnel 0: a superseded version-1 command and a stalled current one; tunnel 1: a fresh one.
        for tunnel, version, at in (
            (tunnels[0], 1, stalled),
            (tunnels[0], 2, stalled),
            (tunnels[1], 1, now),
        ):
            session.add(
                PeerCommand(
                    tunnel_id=tunnel.id,
                    node_id=tunnel.node_id,
                    peer_version=version,
                    operation="remove_peer",
                    created_at=at,
                )
            )
        session.add(
            PeerWindowHold(
                id=window_id,
                user_id=h.user_id,
                connection_id=connection_id,
                generation=1,
                node_id=node_id,
                runtime_id=runtime_id,
                slot_id=1,
                amount_bytes=65536,
                grant={},
                created_at=created,
                not_after=now + timedelta(seconds=60),
            )
        )
        session.flush()
        session.add(
            PeerConnection(
                id=connection_id,
                user_id=h.user_id,
                device_id=device_id,
                node_id=node_id,
                runtime_id=runtime_id,
                slot_id=1,
                window_id=window_id,
                generation=1,
                state="active",
                idempotency_key=uuid4(),
                created_at=created,
            )
        )
    return {
        "user": str(h.user_id),
        "node": str(node_id),
        "stale_node": str(stale_node_id),
        "device": str(device_id),
        "connection": str(connection_id),
        "window": str(window_id),
        "runtime": str(runtime_id),
        "node_name": NODE_NAME,
    }


def test_health_summary_returns_exact_aggregate_counts(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    seed(h)
    response = h.client.get("/api/admin/v1/health-summary", headers=auth)
    assert response.status_code == 200
    assert response.json() == {
        "users_active": 1,
        "users_pending": 1,
        "users_disabled": 1,
        "devices_active": 2,
        "devices_revoked": 1,
        "nodes_active": 2,
        "nodes_disabled": 1,
        "nodes_active_heartbeat_fresh": 1,
        "connections_pending": 0,
        "connections_active": 1,
        "connections_revoke_pending": 0,
        "tunnels_not_revoked": 2,
        # The version-1 command of tunnel 0 is superseded by its current peer version 2.
        "commands_unacknowledged": 2,
        "commands_stalled": 1,
    }


def test_health_summary_contains_no_identifiers_names_or_rows(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    secrets_in_db = seed(h)
    body = h.client.get("/api/admin/v1/health-summary", headers=auth).json()
    assert set(body) == set(AdminHealthSummary.model_fields)
    assert all(type(value) is int and value >= 0 for value in body.values())
    text = json.dumps(body)
    for value in secrets_in_db.values():
        assert value not in text


def test_health_summary_on_an_empty_database_is_all_zero_but_the_seed_user(
    admin: Harness,
) -> None:
    h = admin
    body = h.client.get("/api/admin/v1/health-summary", headers=authorization(h)).json()
    assert body.pop("users_active") == 1  # the harness user
    assert set(body.values()) == {0}


def test_health_summary_requires_admin_authentication(admin: Harness) -> None:
    h = admin
    assert h.client.get("/api/admin/v1/health-summary").status_code == 401
    bad = {"Authorization": "Bearer " + secrets.token_urlsafe(32)}
    assert h.client.get("/api/admin/v1/health-summary", headers=bad).status_code == 401


def test_health_summary_is_read_only_and_documented(admin: Harness) -> None:
    h = admin
    auth = authorization(h)
    seed(h)

    def snapshot() -> tuple[int, ...]:
        with transaction(h.engine) as session:
            return tuple(
                int(session.scalar(select(func.count()).select_from(model)) or 0)
                for model in (AdminAction, User, Device, VPNNode, Tunnel, PeerCommand)
            ) + (
                int(
                    session.scalar(
                        select(func.count())
                        .select_from(PeerConnection)
                        .where(PeerConnection.state == "active")
                    )
                    or 0
                ),
            )

    before = snapshot()
    for _ in range(2):
        assert h.client.get("/api/admin/v1/health-summary", headers=auth).status_code == 200
    assert snapshot() == before
    schema = h.client.app.openapi()
    operation = schema["paths"]["/api/admin/v1/health-summary"]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AdminHealthSummary"
    )
