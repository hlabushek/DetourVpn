"""V3 transactional intent/outbox. Trusted callers supply an operator WindowNode.

No native/network operations in this module. HTTP authentication and protected
node transport remain mandatory at the adapter boundary.
"""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field, SecretStr, StrictBool
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from vpn_api.auth.security import AuthError
from vpn_api.auth.service import AuthService, database_now
from vpn_api.db.models import (
    Device,
    PeerConnection,
    PeerRuntimeEpoch,
    PeerWindowAllocation,
    PeerWindowHold,
    PeerWindowReceipt,
    QuotaFundingLot,
    Tunnel,
    User,
    VPNNode,
)
from vpn_api.nodes.service import NodeError
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.admission import bound_device, consume_proof
from vpn_api.tunnels.v3_contracts import (
    FrozenInput,
    WindowCommand,
    WindowGrant,
    validate_capabilities,
)
from vpn_api.tunnels.v3_contracts import WindowCommands as WindowCommands
from vpn_api.tunnels.v3_contracts import WindowPoll as WindowPoll
from vpn_api.tunnels.window_funding import reserve_window, settle_window
from vpn_api.tunnels.window_profiles import WindowNode


class EpochStart(FrozenInput):
    contract_version: int = Field(strict=True, ge=3, le=3)
    runtime_id: UUID
    previous_runtime_id: UUID | None
    capabilities: list[str] = Field(min_length=4, max_length=4)
    server_public_keys: list[str] = Field(min_length=1, max_length=16)
    gates_closed: StrictBool


class ConnectionInput(FrozenInput):
    device_id: UUID
    node_id: UUID
    idempotency_key: UUID
    budget_bytes: int = Field(strict=True, ge=65536, le=67108864)
    proof_id: UUID
    proof_response: SecretStr


class RenewalInput(FrozenInput):
    previous_window_id: UUID
    idempotency_key: UUID
    budget_bytes: int = Field(strict=True, ge=65536, le=67108864)


def start_epoch(
    session: Session,
    auth: AuthService,
    selected: WindowNode,
    body: EpochStart,
) -> UUID:
    """NodeRegistry must already have authenticated/locked the same selected node."""
    validate_capabilities(body.contract_version, body.capabilities)
    if not body.gates_closed or body.server_public_keys != [
        s.server_public_key for s in selected.slots
    ]:
        raise NodeError("window_operator_inventory_conflict")
    auth.lock(session, "tunnel-node-slot", int(selected.node_id.hex[:15], 16))
    node = session.get(VPNNode, selected.node_id, populate_existing=True)
    if node is None or node.status != "active":
        raise NodeError("agent_unavailable", 503)
    previous = (
        session.get(PeerRuntimeEpoch, (node.id, node.peer_runtime_id))
        if node.peer_runtime_id
        else None
    )
    prior = session.get(PeerRuntimeEpoch, (node.id, body.runtime_id))
    if prior is not None:
        if (
            node.peer_runtime_id != body.runtime_id
            or prior.previous_runtime_id != body.previous_runtime_id
            or prior.manifest_digest != selected.digest()
        ):
            raise NodeError("window_epoch_conflict")
        return body.runtime_id
    if (
        body.previous_runtime_id != node.peer_runtime_id
        or body.runtime_id == body.previous_runtime_id
    ):
        raise NodeError("window_epoch_compare_failed")
    if previous is not None and previous.manifest_digest != selected.digest():
        raise NodeError("window_operator_manifest_changed")
    if session.scalar(
        select(Tunnel.id).where(Tunnel.node_id == node.id, Tunnel.state != "revoked")
    ):
        raise NodeError("window_legacy_tunnel_active")
    session.add(
        PeerRuntimeEpoch(
            node_id=node.id,
            runtime_id=body.runtime_id,
            previous_runtime_id=body.previous_runtime_id,
            manifest_digest=selected.digest(),
            created_at=database_now(session),
        )
    )
    node.peer_runtime_id, node.peer_protocol_version = body.runtime_id, 3
    session.flush()
    return body.runtime_id


def runtime_ready(session: Session, node_id: UUID, runtime_id: UUID) -> bool:
    missing = session.scalar(
        select(PeerWindowHold.id)
        .outerjoin(
            PeerWindowReceipt,
            PeerWindowReceipt.window_id == PeerWindowHold.id,
        )
        .where(
            PeerWindowHold.node_id == node_id,
            PeerWindowHold.runtime_id != runtime_id,
            PeerWindowReceipt.window_id.is_(None),
        )
        .limit(1)
    )
    old_connection = session.scalar(
        select(PeerConnection.id)
        .where(
            PeerConnection.node_id == node_id,
            PeerConnection.runtime_id != runtime_id,
            PeerConnection.state != "closed",
        )
        .limit(1)
    )
    return missing is None and old_connection is None


def available_runtime(session: Session, selected: WindowNode) -> VPNNode:
    node = session.get(VPNNode, selected.node_id, populate_existing=True)
    now = database_now(session)
    epoch = (
        session.get(PeerRuntimeEpoch, (node.id, node.peer_runtime_id))
        if node and node.peer_runtime_id
        else None
    )
    if (
        node is None
        or node.status != "active"
        or node.peer_protocol_version != 3
        or node.peer_runtime_id is None
        or node.last_seen_at is None
        or node.last_seen_at < now - timedelta(seconds=10)
        or epoch is None
        or epoch.manifest_digest != selected.digest()
        or not runtime_ready(session, node.id, node.peer_runtime_id)
    ):
        raise AuthError("window_node_unavailable", 503)
    return node


def funding_deadline(session: Session, user_id: UUID, budget: int) -> datetime:
    now = database_now(session)
    held = LedgerService.held_lots(session, user_id)
    deadline = now + timedelta(seconds=90)
    remaining = budget
    for lot in session.scalars(
        select(QuotaFundingLot)
        .where(
            QuotaFundingLot.user_id == user_id,
            QuotaFundingLot.starts_at <= now,
            QuotaFundingLot.revoked_at.is_(None),
            QuotaFundingLot.remaining_bytes > 0,
            or_(
                QuotaFundingLot.expires_at.is_(None),
                QuotaFundingLot.expires_at > now + timedelta(seconds=10),
            ),
        )
        .order_by(
            QuotaFundingLot.expires_at.asc().nulls_last(),
            QuotaFundingLot.starts_at,
            QuotaFundingLot.id,
        )
    ):
        amount = min(remaining, lot.remaining_bytes - held.get(lot.id, 0))
        if amount > 0:
            remaining -= amount
            if lot.expires_at is not None:
                deadline = min(deadline, lot.expires_at)
        if remaining == 0:
            return deadline
    raise QuotaError("insufficient_window_funding")


def create_connection(
    session: Session,
    auth: AuthService,
    access: str,
    selected: WindowNode,
    body: ConnectionInput,
) -> PeerConnection:
    principal = auth.authenticate(session, access, "tunnels:manage")
    device = bound_device(session, principal, body.device_id)
    if body.node_id != selected.node_id:
        raise AuthError("window_node_unavailable", 503)
    prior = session.scalar(
        select(PeerConnection).where(
            PeerConnection.user_id == principal.user_id,
            PeerConnection.idempotency_key == body.idempotency_key,
        )
    )
    if prior is not None:
        first = session.scalar(
            select(PeerWindowHold).where(
                PeerWindowHold.connection_id == prior.id,
                PeerWindowHold.generation == 1,
            )
        )
        if (
            prior.device_id != body.device_id
            or prior.node_id != body.node_id
            or first is None
            or first.amount_bytes != body.budget_bytes
        ):
            raise AuthError("window_idempotency_conflict", 409)
        return prior
    auth.lock(session, "tunnel-node-slot", int(selected.node_id.hex[:15], 16))
    node = available_runtime(session, selected)
    if (
        session.scalar(
            select(PeerConnection.id).where(
                PeerConnection.device_id == device.id,
                PeerConnection.state != "closed",
            )
        )
        is not None
        or session.scalar(
            select(Tunnel.id).where(
                Tunnel.user_id == principal.user_id,
                Tunnel.state != "revoked",
            )
        )
        is not None
    ):
        raise AuthError("window_connection_exists", 409)
    occupied = set(
        session.scalars(
            select(PeerConnection.slot_id).where(
                PeerConnection.node_id == node.id,
                PeerConnection.state != "closed",
            )
        )
    )
    slot = next((slot for slot in selected.slots if slot.slot_id not in occupied), None)
    if slot is None:
        raise AuthError("window_node_capacity", 409)
    deadline = funding_deadline(session, principal.user_id, body.budget_bytes)
    consume_proof(session, principal, device.id, body.proof_id, body.proof_response)
    now = database_now(session)
    connection_id = uuid4()
    if node.peer_runtime_id is None:
        raise AuthError("window_node_unavailable", 503)
    grant = WindowGrant(
        connection_id=connection_id,
        window_id=uuid4(),
        node_id=node.id,
        runtime_id=node.peer_runtime_id,
        slot_id=slot.slot_id,
        generation=1,
        public_key=device.public_key,
        issued_at=now,
        not_after=min(now + timedelta(seconds=90), deadline),
        budget_bytes=body.budget_bytes,
    )
    reserve_window(session, principal.user_id, grant)
    connection = PeerConnection(
        id=connection_id,
        user_id=principal.user_id,
        device_id=device.id,
        node_id=node.id,
        runtime_id=node.peer_runtime_id,
        slot_id=slot.slot_id,
        window_id=grant.window_id,
        generation=1,
        state="pending",
        idempotency_key=body.idempotency_key,
        created_at=now,
    )
    session.add(connection)
    session.flush()
    return connection


def owned_connection(session: Session, user_id: UUID, connection_id: UUID) -> PeerConnection:
    connection = session.scalar(
        select(PeerConnection)
        .where(
            PeerConnection.id == connection_id,
            PeerConnection.user_id == user_id,
        )
        .with_for_update()
    )
    if connection is None:
        raise AuthError("not_found", 404)
    return connection


def current_grant(session: Session, connection: PeerConnection) -> WindowGrant:
    hold = session.get(PeerWindowHold, connection.window_id)
    if hold is None or hold.user_id != connection.user_id or hold.connection_id != connection.id:
        raise QuotaError("window_inconsistent", 503)
    grant = WindowGrant.model_validate(hold.grant)
    binding = (connection.node_id, connection.runtime_id, connection.slot_id, connection.generation)
    if (
        (hold.node_id, hold.runtime_id, hold.slot_id, hold.generation) != binding
        or (grant.node_id, grant.runtime_id, grant.slot_id, grant.generation) != binding
        or grant.window_id != hold.id
        or grant.connection_id != connection.id
        or grant.budget_bytes != hold.amount_bytes
        or grant.not_after != hold.not_after
    ):
        raise QuotaError("window_inconsistent", 503)
    return grant


def grant_authorized(session: Session, connection: PeerConnection, grant: WindowGrant) -> bool:
    user, device = session.get(User, connection.user_id), session.get(Device, connection.device_id)
    if (
        user is None
        or user.status != "active"
        or device is None
        or device.revoked_at is not None
        or grant.not_after <= database_now(session) + timedelta(seconds=5)
    ):
        return False
    now = database_now(session)
    lots = list(
        session.scalars(
            select(QuotaFundingLot)
            .join(
                PeerWindowAllocation,
                PeerWindowAllocation.lot_id == QuotaFundingLot.id,
            )
            .where(PeerWindowAllocation.window_id == grant.window_id)
        )
    )
    return bool(lots) and all(
        lot.revoked_at is None
        and lot.starts_at <= now
        and (lot.expires_at is None or lot.expires_at > now)
        for lot in lots
    )


def renew_connection(
    session: Session,
    auth: AuthService,
    access: str,
    selected: WindowNode,
    connection_id: UUID,
    body: RenewalInput,
) -> PeerWindowHold:
    principal = auth.authenticate(session, access, "tunnels:manage")
    connection = owned_connection(session, principal.user_id, connection_id)
    bound_device(session, principal, connection.device_id)
    previous = session.get(PeerWindowHold, body.previous_window_id)
    if previous is None or previous.connection_id != connection.id:
        raise AuthError("window_generation_conflict", 409)
    existing = session.get(PeerWindowHold, body.idempotency_key)
    if existing is not None:
        if (
            existing.connection_id != connection.id
            or existing.generation != previous.generation + 1
            or existing.amount_bytes != body.budget_bytes
        ):
            raise AuthError("window_idempotency_conflict", 409)
        return existing
    if (
        connection.state != "active"
        or connection.window_id != previous.id
        or connection.node_id != selected.node_id
    ):
        raise AuthError("window_not_renewable", 409)
    auth.lock(session, "tunnel-node-slot", int(selected.node_id.hex[:15], 16))
    node = available_runtime(session, selected)
    old = current_grant(session, connection)
    if node.peer_runtime_id != connection.runtime_id or not grant_authorized(
        session, connection, old
    ):
        raise AuthError("window_not_renewable", 409)
    deadline = funding_deadline(session, principal.user_id, body.budget_bytes)
    now = database_now(session)
    grant = WindowGrant.model_validate(
        {
            **old.model_dump(),
            "window_id": body.idempotency_key,
            "generation": old.generation + 1,
            "issued_at": now,
            "not_after": min(now + timedelta(seconds=90), deadline),
            "budget_bytes": body.budget_bytes,
        }
    )
    hold = reserve_window(session, principal.user_id, grant)
    connection.window_id, connection.generation, connection.state = (
        hold.id,
        hold.generation,
        "pending",
    )
    session.flush()
    return hold


def revoke_connections(session: Session, user_id: UUID, device_id: UUID | None = None) -> None:
    """Caller holds User lock. Shared by admin/device/subscription revoke paths."""
    for connection in session.scalars(
        select(PeerConnection)
        .where(
            PeerConnection.user_id == user_id,
            PeerConnection.state != "closed",
            *([PeerConnection.device_id == device_id] if device_id is not None else []),
        )
        .order_by(PeerConnection.id)
        .with_for_update()
    ):
        connection.state = "revoke_pending"
    session.flush()


def poll_windows(
    session: Session,
    selected: WindowNode,
    body: WindowPoll,
) -> WindowCommands:
    """NodeRegistry authenticated this node; scoped exact receipts are applied atomically."""
    node = session.get(VPNNode, selected.node_id, populate_existing=True)
    if (
        node is None
        or node.status != "active"
        or node.peer_runtime_id != body.runtime_id
        or node.peer_protocol_version != 3
    ):
        raise NodeError("window_runtime_mismatch")
    epoch = session.get(PeerRuntimeEpoch, (node.id, body.runtime_id))
    if epoch is None or epoch.manifest_digest != selected.digest():
        raise NodeError("window_operator_manifest_changed")
    # Every user lock in deterministic UUID order, before any connection/lot lock.
    holds = {
        receipt.window_id: session.get(PeerWindowHold, receipt.window_id)
        for receipt in body.receipts
    }
    if any(hold is None or hold.node_id != node.id for hold in holds.values()):
        raise NodeError("window_receipt_not_found", 404)
    candidates = list(
        session.scalars(
            select(PeerConnection)
            .where(
                PeerConnection.node_id == node.id,
                PeerConnection.state != "closed",
            )
            .order_by(PeerConnection.user_id, PeerConnection.id)
        )
    )
    owners = {hold.user_id for hold in holds.values() if hold is not None} | {
        c.user_id for c in candidates
    }
    for owner in sorted(owners):
        LedgerService._lock_owner(session, owner, active=False)
    acknowledged = []
    for receipt in body.receipts:
        hold = holds[receipt.window_id]
        if hold is None:
            raise NodeError("window_receipt_not_found", 404)
        settle_window(session, hold.user_id, receipt)
        connection = session.get(PeerConnection, hold.connection_id, populate_existing=True)
        if connection is not None and connection.window_id == hold.id:
            connection.state = "closed"
        acknowledged.append(receipt)
    session.flush()
    ready = runtime_ready(session, node.id, body.runtime_id)
    commands = []
    if not ready:
        for hold in session.scalars(
            select(PeerWindowHold)
            .outerjoin(
                PeerWindowReceipt,
                PeerWindowReceipt.window_id == PeerWindowHold.id,
            )
            .where(
                PeerWindowHold.node_id == node.id,
                PeerWindowHold.runtime_id != body.runtime_id,
                PeerWindowReceipt.window_id.is_(None),
            )
            .order_by(PeerWindowHold.created_at)
            .limit(16)
        ):
            commands.append(
                WindowCommand(operation="recover", grant=WindowGrant.model_validate(hold.grant))
            )
    else:
        reported = set(body.active_windows)
        for candidate in candidates:
            session.refresh(candidate, with_for_update=True)
            if candidate.state == "closed":
                continue
            grant = current_grant(session, candidate)
            if candidate.runtime_id != body.runtime_id or not grant_authorized(
                session, candidate, grant
            ):
                candidate.state = "revoke_pending"
            if candidate.state == "revoke_pending":
                commands.append(WindowCommand(operation="revoke", grant=grant))
            elif candidate.window_id in reported:
                candidate.state = "active"
            elif candidate.state == "pending":
                commands.append(WindowCommand(operation="window", grant=grant))
            else:
                # An agent losing a supposedly active peer may not silently reopen it.
                candidate.state = "revoke_pending"
                commands.append(WindowCommand(operation="revoke", grant=grant))
    session.flush()
    return WindowCommands(
        runtime_id=body.runtime_id,
        ready=ready,
        commands=commands,
        acknowledged_receipts=acknowledged,
    )
