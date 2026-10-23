"""PostgreSQL intent lifecycle. User locks serialize all peer state transitions."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from vpn_api.auth.security import AuthError
from vpn_api.auth.service import AuthService, database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    Device,
    PeerCommand,
    SessionFamily,
    Subscription,
    Tunnel,
    TunnelReservation,
    User,
    VPNNode,
)
from vpn_api.nodes.catalog import node_reason
from vpn_api.nodes.service import NodeError
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.admission import consume_proof, profiles
from vpn_api.tunnels.contracts import (
    PeerAckInput,
    PeerAckResponse,
    PeerPollResponse,
    PeerRemoval,
    TunnelInput,
    TunnelResponse,
)


def request_tunnel(auth: AuthService, access: str, body: TunnelInput) -> TunnelResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        device = session.scalar(
            select(Device).where(Device.id == body.device_id, Device.user_id == principal.user_id)
        )
        if device is None:
            raise AuthError("not_found", 404)
        if device.revoked_at is not None:
            raise AuthError("device_revoked", 409)
        family = session.get(SessionFamily, principal.family_id)
        if family is None or family.device_id != device.id:
            raise AuthError("session_device_mismatch", 409)
        prior = session.scalar(
            select(Tunnel).where(
                Tunnel.user_id == principal.user_id,
                Tunnel.idempotency_key == body.idempotency_key,
            )
        )
        if prior is not None:
            if prior.device_id != device.id:
                raise AuthError("tunnel_idempotency_conflict", 409)
            if body.node_id is not None and prior.node_id != body.node_id:
                raise AuthError("tunnel_idempotency_conflict", 409)
            if prior.budget_bytes is not None and prior.budget_bytes != body.budget_bytes:
                raise AuthError("tunnel_idempotency_conflict", 409)
            return TunnelResponse.model_validate(prior)
        # v2 cannot pin reservations to expiring quota lots. Never silently reuse it
        # for subscriptions; v3 must negotiate per-lot leases and kernel enforcement.
        if (
            session.scalar(
                select(Subscription.id).where(Subscription.user_id == principal.user_id).limit(1)
            )
            is not None
        ):
            raise AuthError("entitlement_requires_peer_protocol_v3", 409)
        existing = session.scalar(
            select(Tunnel.id).where(Tunnel.device_id == device.id, Tunnel.state != "revoked")
        )
        if existing is not None:
            raise AuthError("tunnel_exists", 409)
        tunnel = Tunnel(
            user_id=principal.user_id,
            device_id=device.id,
            idempotency_key=body.idempotency_key,
            state="admission_pending",
            peer_version=1,
            created_at=database_now(session),
        )
        session.add(tunnel)
        session.flush()
        configured = profiles(auth)
        selected = None
        now = database_now(session)
        if body.node_id is not None:
            selected = next((p for p in configured if p.node_id == body.node_id), None)
            if selected is None:
                raise AuthError("tunnel_node_unavailable", 503)
        elif configured:
            selected = next(
                (p for p in configured if node_reason(session, p, now) == "available"), None
            )
            if selected is None:
                raise AuthError("tunnel_node_unavailable", 503)
        if selected is not None:
            node = session.get(VPNNode, selected.node_id)
            if (
                node is None
                or node.status != "active"
                or node.peer_protocol_version != 2
                or node.peer_runtime_id != selected.runtime_id
                or node.last_seen_at is None
                or node.last_seen_at < now - timedelta(seconds=10)
            ):
                raise AuthError("tunnel_node_unavailable", 503)
            # Same User lock as quota debit. Node slot is protected by a unique index
            # and a dedicated advisory lock, never by a VPNNode row lock (agent order).
            auth.lock(session, "tunnel-node-slot", int(node.id.hex[:15], 16))
            # Recheck after waiting for capacity lock; do not lock VPNNode after User.
            session.refresh(node)
            now = database_now(session)
            if (
                node.status != "active"
                or node.peer_protocol_version != 2
                or node.peer_runtime_id != selected.runtime_id
                or node.last_seen_at is None
                or node.last_seen_at < now - timedelta(seconds=10)
            ):
                raise AuthError("tunnel_node_unavailable", 503)
            if session.scalar(
                select(Tunnel.id).where(Tunnel.node_id == node.id, Tunnel.state != "revoked")
            ):
                raise AuthError("tunnel_node_capacity", 409)
            if (
                LedgerService(auth.engine).quota(session, principal.user_id).available_bytes
                < body.budget_bytes
            ):
                raise AuthError("insufficient_quota", 409)
            consume_proof(session, principal, device.id, body.proof_id, body.proof_response)
            tunnel.node_id = node.id
            tunnel.runtime_id = selected.runtime_id
            tunnel.budget_bytes = body.budget_bytes
            tunnel.lease_deadline = now + timedelta(seconds=60)
            tunnel.state = "provision_pending"
            session.flush()
            session.add(
                TunnelReservation(
                    tunnel_id=tunnel.id, user_id=principal.user_id, amount_bytes=body.budget_bytes
                )
            )
            session.add(
                PeerCommand(
                    tunnel_id=tunnel.id,
                    node_id=node.id,
                    operation="add_peer",
                    peer_version=tunnel.peer_version,
                    created_at=now,
                )
            )
            session.flush()
        return TunnelResponse.model_validate(tunnel)


def recover_expired(auth: AuthService, access: str, tunnel_id: UUID) -> TunnelResponse:
    """Explicit conservative settlement after enforced lease expiry; never a node ACK."""
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "tunnels:manage")
        tunnel = owned_tunnel(session, principal.user_id, tunnel_id)
        if tunnel.state == "revoked":
            return TunnelResponse.model_validate(tunnel)
        now = database_now(session)
        node = session.get(VPNNode, tunnel.node_id) if tunnel.node_id else None
        if (
            tunnel.lease_deadline is None
            # Reviewed staging assumption: <=1s clock skew and <=5s gate operation.
            or tunnel.lease_deadline + timedelta(seconds=10) > now
            or (
                node is not None
                and node.last_seen_at is not None
                and node.last_seen_at >= now - timedelta(seconds=10)
            )
        ):
            raise AuthError("tunnel_recovery_not_ready", 409)
        reservation = session.get(TunnelReservation, tunnel.id)
        if reservation is None or reservation.settled_at is not None:
            raise AuthError("tunnel_recovery_not_ready", 409)
        reservation.settled_at = now
        reservation.charged_bytes = reservation.amount_bytes
        reservation.observed_bytes = None
        session.flush()
        result = LedgerService(auth.engine).apply_tunnel_settlement_in_transaction(
            session, principal.user_id, reservation.amount_bytes, tunnel.id
        )
        if result.status != "applied":
            raise AuthError("peer_settlement_failed", 409)
        # Fence outstanding commands without claiming an agent acknowledgement.
        tunnel.peer_version += 1
        tunnel.state, tunnel.revoked_at = "revoked", now
        session.flush()
        return TunnelResponse.model_validate(tunnel)


def owned_tunnel(session: Session, user_id: UUID, tunnel_id: UUID) -> Tunnel:
    tunnel = session.scalar(
        select(Tunnel).where(Tunnel.id == tunnel_id, Tunnel.user_id == user_id).with_for_update()
    )
    if tunnel is None:
        raise AuthError("not_found", 404)
    return tunnel


def enqueue_revoke(session: Session, tunnel: Tunnel) -> None:
    """Caller holds User then Tunnel locks; never locks a node or performs network I/O."""
    if tunnel.state == "revoked":
        return
    if tunnel.node_id is None:
        # Admission pending never dispatched an add command or issued a config.
        now = database_now(session)
        tunnel.state = "revoked"
        tunnel.revoked_at = now
        return
    if tunnel.state != "revoke_pending":
        tunnel.state = "revoke_pending"
        tunnel.peer_version += 1
    prior = session.scalar(
        select(PeerCommand).where(
            PeerCommand.tunnel_id == tunnel.id, PeerCommand.peer_version == tunnel.peer_version
        )
    )
    if prior is None:
        session.add(
            PeerCommand(
                tunnel_id=tunnel.id,
                node_id=tunnel.node_id,
                operation="remove_peer",
                peer_version=tunnel.peer_version,
                created_at=database_now(session),
            )
        )
    session.flush()


def revoke_device_tunnels(session: Session, user_id: UUID, device_id: UUID) -> None:
    # Public device revocation already holds User and Device locks.
    tunnels = session.scalars(
        select(Tunnel)
        .where(Tunnel.user_id == user_id, Tunnel.device_id == device_id, Tunnel.state != "revoked")
        .order_by(Tunnel.id)
        .with_for_update()
    )
    for tunnel in tunnels:
        enqueue_revoke(session, tunnel)


def pending_commands(session: Session, node_id: UUID, limit: int) -> PeerPollResponse:
    rows = session.execute(
        select(PeerCommand, Device.public_key)
        .join(Tunnel, Tunnel.id == PeerCommand.tunnel_id)
        .join(Device, Device.id == Tunnel.device_id)
        .where(
            PeerCommand.node_id == node_id,
            PeerCommand.acknowledged_at.is_(None),
            PeerCommand.operation == "remove_peer",
            PeerCommand.peer_version == Tunnel.peer_version,
        )
        .order_by(PeerCommand.created_at, PeerCommand.id)
        .limit(limit)
    )
    return PeerPollResponse(
        commands=[
            PeerRemoval(
                command_id=command.id,
                peer_id=command.tunnel_id,
                peer_version=command.peer_version,
                public_key=public_key,
            )
            for command, public_key in rows
        ]
    )


def acknowledge(session: Session, node_id: UUID, body: PeerAckInput) -> PeerAckResponse:
    # Authenticate has locked VPNNode. Discover owner without locking Tunnel first.
    owner_id = session.scalar(
        select(Tunnel.user_id)
        .join(PeerCommand, PeerCommand.tunnel_id == Tunnel.id)
        .where(PeerCommand.id == body.command_id, PeerCommand.node_id == node_id)
    )
    if owner_id is None:
        raise NodeError("not_found", 404)
    session.scalar(select(User).where(User.id == owner_id).with_for_update())
    command = session.get(PeerCommand, body.command_id)
    if command is None:
        raise NodeError("not_found", 404)
    tunnel = session.scalar(select(Tunnel).where(Tunnel.id == command.tunnel_id).with_for_update())
    if tunnel is None:
        raise NodeError("not_found", 404)
    session.refresh(command, with_for_update=True)
    if session.get(TunnelReservation, tunnel.id) is not None:
        raise NodeError("agent_version_unsupported")
    if (
        body.peer_id != tunnel.id
        or body.peer_version != command.peer_version
        or tunnel.peer_version != command.peer_version
    ):
        raise NodeError("peer_version_conflict")
    if command.acknowledged_at is not None:
        if tunnel.state != "revoked":
            raise NodeError("peer_state_conflict")
        return PeerAckResponse(command_id=command.id)
    if tunnel.state != "revoke_pending":
        raise NodeError("peer_state_conflict")
    now = database_now(session)
    command.acknowledged_at = now
    tunnel.state = "revoked"
    tunnel.revoked_at = now
    session.flush()
    return PeerAckResponse(command_id=command.id)
