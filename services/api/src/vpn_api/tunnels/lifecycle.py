"""Version 2: one-peer staging lifecycle, native final counters and reservations."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy import select
from sqlalchemy.orm import Session

from vpn_api.auth.contracts import Input
from vpn_api.auth.service import database_now
from vpn_api.db.models import Device, PeerCommand, Tunnel, TunnelReservation, User, VPNNode
from vpn_api.nodes.service import NodeError
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.service import enqueue_revoke


class LifecyclePoll(Input):
    contract_version: StrictInt
    capabilities: list[Literal["peer-lease.v2", "kernel-udp-budget.v1"]] = Field(
        min_length=2, max_length=2
    )
    server_public_key: str = Field(min_length=44, max_length=44)
    runtime_id: UUID
    observed_public_keys: list[str] = Field(max_length=1)


class LifecycleCommand(BaseModel):
    command_id: UUID
    peer_id: UUID
    peer_version: int
    operation: Literal["add_peer", "remove_peer"]
    public_key: str
    lease_deadline: datetime
    budget_bytes: int


class LifecycleCommands(BaseModel):
    contract_version: Literal[2] = 2
    commands: list[LifecycleCommand]


class LifecycleAck(Input):
    contract_version: StrictInt
    command_id: UUID
    peer_id: UUID
    peer_version: StrictInt = Field(ge=1, le=2**63 - 1)
    operation: Literal["add_peer", "remove_peer"]
    server_public_key: str = Field(min_length=44, max_length=44)
    rx_bytes: StrictInt = Field(default=0, ge=0, le=2**63 - 1)
    tx_bytes: StrictInt = Field(default=0, ge=0, le=2**63 - 1)
    counters_lost: StrictBool = False
    applied: StrictBool = True


def commands(session: Session, node_id: UUID, observed_public_keys: list[str]) -> LifecycleCommands:
    candidates = list(
        session.scalars(select(Tunnel).where(Tunnel.node_id == node_id, Tunnel.state != "revoked"))
    )
    for candidate in candidates:
        session.scalar(select(User).where(User.id == candidate.user_id).with_for_update())
        session.refresh(candidate, with_for_update=True)
        if candidate.state == "active":
            device = session.get(Device, candidate.device_id)
            if device is None or observed_public_keys != [device.public_key]:
                enqueue_revoke(session, candidate)
        if candidate.lease_deadline is not None and candidate.lease_deadline <= database_now(
            session
        ):
            enqueue_revoke(session, candidate)
    rows = session.execute(
        select(PeerCommand, Tunnel, Device.public_key)
        .join(Tunnel, Tunnel.id == PeerCommand.tunnel_id)
        .join(Device, Device.id == Tunnel.device_id)
        .where(
            PeerCommand.node_id == node_id,
            PeerCommand.acknowledged_at.is_(None),
            PeerCommand.peer_version == Tunnel.peer_version,
        )
        .order_by(PeerCommand.created_at)
        .limit(1)
    )
    result = []
    for command, tunnel, public_key in rows:
        if tunnel.lease_deadline is None or tunnel.budget_bytes is None:
            # Legacy synthetic removal is served by v1, never guessed into a lease.
            continue
        result.append(
            LifecycleCommand(
                command_id=command.id,
                peer_id=tunnel.id,
                peer_version=command.peer_version,
                operation=command.operation,
                public_key=public_key,
                lease_deadline=tunnel.lease_deadline,
                budget_bytes=tunnel.budget_bytes,
            )
        )
    return LifecycleCommands(commands=result)


def apply_ack(session: Session, node_id: UUID, body: LifecycleAck) -> str:
    owner = session.scalar(
        select(Tunnel.user_id)
        .join(PeerCommand, PeerCommand.tunnel_id == Tunnel.id)
        .where(PeerCommand.id == body.command_id, PeerCommand.node_id == node_id)
    )
    if owner is None:
        raise NodeError("not_found", 404)
    session.scalar(select(User).where(User.id == owner).with_for_update())
    tunnel = session.scalar(
        select(Tunnel).where(Tunnel.id == body.peer_id, Tunnel.user_id == owner).with_for_update()
    )
    command = session.get(PeerCommand, body.command_id)
    if (
        tunnel is None
        or command is None
        or command.tunnel_id != tunnel.id
        or command.peer_version != body.peer_version
        or tunnel.peer_version != body.peer_version
        or command.operation != body.operation
    ):
        raise NodeError("peer_version_conflict")
    now = database_now(session)
    if body.operation == "add_peer":
        if not body.applied:
            if tunnel.state != "revoked":
                enqueue_revoke(session, tunnel)
            return tunnel.state
        if tunnel.state not in ("provision_pending", "active"):
            raise NodeError("peer_state_conflict")
        if tunnel.lease_deadline is None or tunnel.lease_deadline <= now:
            raise NodeError("peer_lease_expired")
        tunnel.state = "active"
    else:
        reservation = session.get(TunnelReservation, tunnel.id)
        if reservation is None:
            raise NodeError("peer_state_conflict")
        observed = body.rx_bytes + body.tx_bytes
        if observed > 2**63 - 1:
            raise NodeError("peer_budget_exceeded")
        spent = (
            reservation.amount_bytes
            if body.counters_lost
            else min(observed, reservation.amount_bytes)
        )
        if reservation.settled_at is not None:
            if reservation.charged_bytes != spent or reservation.observed_bytes != (
                None if body.counters_lost else observed
            ):
                raise NodeError("peer_settlement_conflict")
            return tunnel.state
        if tunnel.state != "revoke_pending":
            raise NodeError("peer_state_conflict")
        reservation.settled_at, reservation.charged_bytes = now, spent
        reservation.observed_bytes = None if body.counters_lost else observed
        session.flush()
        if spent:
            # Release this reservation before its final atomic debit; retain others.
            bind = session.get_bind()
            result = LedgerService(bind.engine).apply_tunnel_settlement_in_transaction(
                session, owner, spent, tunnel.id
            )
            if result.status != "applied":
                raise NodeError("peer_settlement_failed")
        tunnel.state, tunnel.revoked_at = "revoked", now
    command.acknowledged_at = now
    session.flush()
    return tunnel.state


def negotiate(
    session: Session, node_id: UUID, body: LifecyclePoll, expected_public_key: str
) -> None:
    if (
        set(body.capabilities) != {"peer-lease.v2", "kernel-udp-budget.v1"}
        or body.server_public_key != expected_public_key
    ):
        raise NodeError("agent_capability_mismatch")
    from vpn_api.tunnels.admission import canonical_key

    try:
        for public_key in body.observed_public_keys:
            canonical_key(public_key)
    except ValueError:
        raise NodeError("agent_inventory_invalid") from None
    node = session.get(VPNNode, node_id)
    if node is None:
        raise NodeError("agent_unauthorized", 401)
    from vpn_api.db.models import PeerRuntimeEpoch

    if (
        session.scalar(
            select(PeerRuntimeEpoch.runtime_id)
            .where(
                PeerRuntimeEpoch.node_id == node_id,
            )
            .limit(1)
        )
        is not None
    ):
        raise NodeError("agent_protocol_downgrade_forbidden")
    node.peer_protocol_version = 2
    if node.peer_runtime_id is not None and node.peer_runtime_id != body.runtime_id:
        for tunnel in list(
            session.scalars(
                select(Tunnel).where(Tunnel.node_id == node_id, Tunnel.state != "revoked")
            )
        ):
            session.scalar(select(User).where(User.id == tunnel.user_id).with_for_update())
            session.refresh(tunnel, with_for_update=True)
            enqueue_revoke(session, tunnel)
    node.peer_runtime_id = body.runtime_id
