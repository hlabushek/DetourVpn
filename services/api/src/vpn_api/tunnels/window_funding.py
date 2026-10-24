"""I-024 funding transactions and restore audit; no external calls inside transactions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from vpn_api.db.models import (
    Device,
    PeerConnection,
    PeerWindowAllocation,
    PeerWindowHold,
    PeerWindowReceipt,
    QuotaFundingLot,
    QuotaFundingUse,
    QuotaOperation,
    TunnelReservation,
)
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.v3_contracts import (
    ClosedWindow,
    WindowGrant,
    validate_closed_window,
    validate_grant,
)


def reserve_window(session: Session, user_id: UUID, grant: WindowGrant) -> PeerWindowHold:
    """Caller must prove device/node/runtime authorization before any dispatch.

    Replay returns original funding authority even after expiry; it never authorizes
    reopening a gate. Node validation remains mandatory on every command.
    """
    LedgerService._lock_owner(session, user_id)
    existing = session.get(PeerWindowHold, grant.window_id)
    payload = grant.model_dump(mode="json")
    if existing is not None:
        if existing.user_id != user_id or existing.grant != payload:
            raise QuotaError("window_funding_conflict")
        return existing
    now = session.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise QuotaError("database_clock_unavailable", 503)
    previous = session.scalar(
        select(PeerWindowHold)
        .where(PeerWindowHold.connection_id == grant.connection_id)
        .order_by(PeerWindowHold.generation.desc())
        .limit(1)
    )
    if previous is not None and previous.user_id != user_id:
        raise QuotaError("window_funding_conflict")
    validate_grant(
        grant,
        node_id=grant.node_id,
        runtime_id=grant.runtime_id,
        slot_id=grant.slot_id,
        now=now,
        previous=WindowGrant.model_validate(previous.grant) if previous else None,
    )
    # One current plus one pending window maximum; unknown is never renewal evidence.
    unresolved = list(
        session.scalars(
            select(PeerWindowHold)
            .outerjoin(
                PeerWindowReceipt,
                PeerWindowReceipt.window_id == PeerWindowHold.id,
            )
            .where(
                PeerWindowHold.connection_id == grant.connection_id,
                (PeerWindowReceipt.window_id.is_(None))
                | (PeerWindowReceipt.observation == "unknown"),
            )
        )
    )
    if any(row.id != (previous.id if previous else None) for row in unresolved) or any(
        session.get(PeerWindowReceipt, row.id) is not None for row in unresolved
    ):
        raise QuotaError("window_reconciliation_required")
    if (
        session.scalar(
            select(TunnelReservation.tunnel_id).where(
                TunnelReservation.user_id == user_id,
                TunnelReservation.settled_at.is_(None),
            )
        )
        is not None
    ):
        raise QuotaError("subscription_legacy_tunnel_active")
    current = LedgerService._state(session, user_id, now)
    if current.available_bytes < grant.budget_bytes:
        raise QuotaError("insufficient_quota")
    held = LedgerService.held_lots(session, user_id)
    remaining = grant.budget_bytes
    allocations: list[tuple[UUID, int]] = []
    for lot in session.scalars(
        select(QuotaFundingLot)
        .where(
            QuotaFundingLot.user_id == user_id,
            QuotaFundingLot.starts_at <= now,
            QuotaFundingLot.revoked_at.is_(None),
            QuotaFundingLot.remaining_bytes > 0,
            (QuotaFundingLot.expires_at.is_(None))
            | (QuotaFundingLot.expires_at >= grant.not_after),
        )
        .order_by(
            QuotaFundingLot.expires_at.asc().nulls_last(),
            QuotaFundingLot.starts_at,
            QuotaFundingLot.id,
        )
        .with_for_update()
    ):
        amount = min(remaining, lot.remaining_bytes - held.get(lot.id, 0))
        if amount > 0:
            allocations.append((lot.id, amount))
            remaining -= amount
        if remaining == 0:
            break
    if remaining:
        raise QuotaError("window_funding_deadline")
    hold = PeerWindowHold(
        id=grant.window_id,
        user_id=user_id,
        connection_id=grant.connection_id,
        generation=grant.generation,
        node_id=grant.node_id,
        runtime_id=grant.runtime_id,
        slot_id=grant.slot_id,
        amount_bytes=grant.budget_bytes,
        grant=payload,
        created_at=now,
        not_after=grant.not_after,
    )
    session.add(hold)
    session.flush()
    for position, (lot_id, amount) in enumerate(allocations):
        session.add(
            PeerWindowAllocation(
                window_id=hold.id,
                user_id=user_id,
                lot_id=lot_id,
                position=position,
                amount_bytes=amount,
            )
        )
    session.flush()
    return hold


def settle_window(session: Session, user_id: UUID, receipt: ClosedWindow) -> PeerWindowReceipt:
    """Authenticated node caller only; immutable unknown evidence cannot be overwritten."""
    LedgerService._lock_owner(session, user_id, active=False)
    hold = session.get(PeerWindowHold, receipt.window_id)
    if hold is None or hold.user_id != user_id:
        raise QuotaError("window_not_found", 404)
    validate_closed_window(receipt, WindowGrant.model_validate(hold.grant))
    payload = receipt.model_dump(mode="json")
    existing = session.get(PeerWindowReceipt, hold.id)
    if existing is not None:
        if existing.receipt != payload:
            raise QuotaError("window_receipt_conflict")
        return existing
    pinned = {
        row.lot_id: row.amount_bytes
        for row in session.scalars(
            select(PeerWindowAllocation)
            .where(PeerWindowAllocation.window_id == hold.id)
            .order_by(PeerWindowAllocation.position)
        )
    }
    if sum(pinned.values()) != hold.amount_bytes:
        raise QuotaError("quota_inconsistent", 503)
    operation_id = None
    if receipt.observed_bytes:
        bind = session.get_bind()
        engine = bind if isinstance(bind, Engine) else bind.engine
        result = LedgerService(engine)._post(
            session,
            user_id,
            "debit",
            receipt.observed_bytes,
            "peer_window",
            hold.id,
            hold.id,
            pinned=pinned,
        )
        if result.status != "applied":
            raise QuotaError("quota_inconsistent", 503)
        operation_id = result.operation_id
    result_row = PeerWindowReceipt(
        window_id=hold.id,
        user_id=user_id,
        observation=receipt.observation,
        observed_bytes=receipt.observed_bytes,
        operation_id=operation_id,
        receipt=payload,
        created_at=session.scalar(select(func.clock_timestamp())),
    )
    session.add(result_row)
    session.flush()
    return result_row


def reconcile_windows(
    session: Session, user_id: UUID, operations: dict[UUID, QuotaOperation]
) -> int:
    """Restore audit: binding, allocation totals, exact measured debit and no orphan source."""
    violations = 0
    window_ops = {
        row.source_id: row for row in operations.values() if row.source_type == "peer_window"
    }
    chains: dict[UUID, list[WindowGrant]] = {}
    for hold in session.scalars(select(PeerWindowHold).where(PeerWindowHold.user_id == user_id)):
        allocations = list(
            session.scalars(
                select(PeerWindowAllocation)
                .where(PeerWindowAllocation.window_id == hold.id)
                .order_by(PeerWindowAllocation.position)
            )
        )
        violations += int(sum(row.amount_bytes for row in allocations) != hold.amount_bytes)
        violations += int([row.position for row in allocations] != list(range(len(allocations))))
        try:
            grant = WindowGrant.model_validate(hold.grant)
            chains.setdefault(hold.connection_id, []).append(grant)
            violations += int(
                (
                    grant.window_id,
                    grant.connection_id,
                    grant.generation,
                    grant.node_id,
                    grant.runtime_id,
                    grant.slot_id,
                    grant.budget_bytes,
                    grant.not_after,
                )
                != (
                    hold.id,
                    hold.connection_id,
                    hold.generation,
                    hold.node_id,
                    hold.runtime_id,
                    hold.slot_id,
                    hold.amount_bytes,
                    hold.not_after,
                )
            )
            receipt = session.get(PeerWindowReceipt, hold.id)
            if receipt is not None:
                evidence = ClosedWindow.model_validate(receipt.receipt)
                validate_closed_window(evidence, grant)
                violations += int(
                    (evidence.observation, evidence.observed_bytes)
                    != (
                        receipt.observation,
                        receipt.observed_bytes,
                    )
                )
        except ValueError:
            violations += 1
            receipt = session.get(PeerWindowReceipt, hold.id)
        operation = window_ops.pop(hold.id, None)
        measured = receipt.observed_bytes if receipt is not None else None
        if not measured:
            violations += int(operation is not None)
            continue
        violations += int(
            operation is None
            or receipt is None
            or (
                operation.id,
                operation.kind,
                operation.status,
                operation.amount_bytes,
                operation.idempotency_key,
            )
            != (receipt.operation_id, "debit", "applied", measured, hold.id)
        )
        if operation is None:
            continue
        actual = {
            row.lot_id: row.amount_bytes
            for row in session.scalars(
                select(QuotaFundingUse).where(QuotaFundingUse.operation_id == operation.id)
            )
        }
        expected = {}
        remaining = measured
        for allocation in allocations:
            spent = min(remaining, allocation.amount_bytes)
            if spent:
                expected[allocation.lot_id] = spent
                remaining -= spent
        violations += int(actual != expected or remaining != 0)
    for chain in chains.values():
        chain.sort(key=lambda item: item.generation)
        for index, grant in enumerate(chain):
            try:
                # Validate historical grants against their own issuance time,
                # never against today's clock after restore/expiry.
                validate_grant(
                    grant,
                    node_id=chain[0].node_id,
                    runtime_id=chain[0].runtime_id,
                    slot_id=chain[0].slot_id,
                    now=grant.issued_at,
                    previous=chain[index - 1] if index else None,
                )
            except ValueError:
                violations += 1
    for connection in session.scalars(
        select(PeerConnection).where(PeerConnection.user_id == user_id)
    ):
        chain = chains.get(connection.id, [])
        device = session.get(Device, connection.device_id)
        latest = chain[-1] if chain else None
        final = session.get(PeerWindowReceipt, connection.window_id)
        violations += int((connection.state == "closed") != (final is not None))
        violations += int(
            latest is None
            or device is None
            or device.user_id != user_id
            or (
                latest.window_id,
                latest.generation,
                latest.node_id,
                latest.runtime_id,
                latest.slot_id,
                latest.public_key,
            )
            != (
                connection.window_id,
                connection.generation,
                connection.node_id,
                connection.runtime_id,
                connection.slot_id,
                device.public_key,
            )
        )
    return violations + len(window_ops)
