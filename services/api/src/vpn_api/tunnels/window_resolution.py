"""Audited adjudication of immutable unknown peer-window receipts."""

import re
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from vpn_api.db.models import (
    PeerConnection,
    PeerWindowAllocation,
    PeerWindowHold,
    PeerWindowReceipt,
    PeerWindowResolution,
    QuotaOperation,
)
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.tunnels.v3_contracts import ClosedWindow, WindowGrant, validate_closed_window

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def charge_unknown_window(
    session: Session,
    *,
    user_id: UUID,
    window_id: UUID,
    admin_id: UUID,
    idempotency_key: UUID,
    reason: str,
    evidence_digest: str,
) -> dict[str, object]:
    """Charge the exact funded budget as compensation, never as measured traffic."""
    if len(reason.strip()) < 3 or len(reason) > 500 or not _SHA256.fullmatch(evidence_digest):
        raise QuotaError("window_resolution_evidence_invalid", 422)

    LedgerService._lock_owner(session, user_id, active=False)
    hold = session.scalar(
        select(PeerWindowHold)
        .where(PeerWindowHold.id == window_id, PeerWindowHold.user_id == user_id)
        .with_for_update()
    )
    if hold is None:
        raise QuotaError("window_not_found", 404)

    connection = session.scalar(
        select(PeerConnection)
        .where(
            PeerConnection.id == hold.connection_id,
            PeerConnection.user_id == user_id,
        )
        .with_for_update()
    )
    if (
        connection is None
        or connection.state != "closed"
        or connection.window_id != hold.id
        or connection.generation != hold.generation
    ):
        raise QuotaError("window_resolution_connection_not_closed")

    receipt = session.scalar(
        select(PeerWindowReceipt)
        .where(PeerWindowReceipt.window_id == hold.id, PeerWindowReceipt.user_id == user_id)
        .with_for_update()
    )
    if (
        receipt is None
        or receipt.observation != "unknown"
        or receipt.observed_bytes is not None
        or receipt.operation_id is not None
    ):
        raise QuotaError("window_resolution_unknown_receipt_required")
    try:
        grant = WindowGrant.model_validate(hold.grant)
        evidence = ClosedWindow.model_validate(receipt.receipt)
        validate_closed_window(evidence, grant)
    except ValueError:
        raise QuotaError("window_resolution_evidence_invalid", 409) from None
    if evidence.observation != "unknown" or evidence.observed_bytes is not None:
        raise QuotaError("window_resolution_unknown_receipt_required")

    existing_resolution = session.scalar(
        select(PeerWindowResolution).where(PeerWindowResolution.window_id == hold.id)
    )
    if existing_resolution is not None:
        raise QuotaError("window_resolution_already_applied")

    allocations = list(
        session.scalars(
            select(PeerWindowAllocation)
            .where(
                PeerWindowAllocation.window_id == hold.id,
                PeerWindowAllocation.user_id == user_id,
            )
            .order_by(PeerWindowAllocation.position)
            .with_for_update()
        )
    )
    if (
        not allocations
        or [row.position for row in allocations] != list(range(len(allocations)))
        or sum(row.amount_bytes for row in allocations) != hold.amount_bytes
    ):
        raise QuotaError("quota_inconsistent", 503)
    pinned = {row.lot_id: row.amount_bytes for row in allocations}
    if len(pinned) != len(allocations):
        raise QuotaError("quota_inconsistent", 503)

    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    result = LedgerService(engine)._post(
        session,
        user_id,
        "debit",
        hold.amount_bytes,
        "peer_window_resolution",
        hold.id,
        idempotency_key,
        pinned=pinned,
    )
    if result.status != "applied":
        raise QuotaError(result.error_code or "quota_adjustment_failed")
    now = session.scalar(select(func.clock_timestamp()))
    resolution = PeerWindowResolution(
        window_id=hold.id,
        user_id=user_id,
        operation_id=result.operation_id,
        admin_id=admin_id,
        idempotency_key=idempotency_key,
        kind="charge_full_budget",
        reason=reason,
        evidence_digest=evidence_digest,
        amount_bytes=hold.amount_bytes,
        created_at=now,
    )
    session.add(resolution)
    session.flush()
    operation = session.get(QuotaOperation, result.operation_id)
    if operation is None:
        raise QuotaError("quota_inconsistent", 503)
    return {
        "status": "charged_full_budget",
        "amount_bytes": hold.amount_bytes,
        "operation_id": str(operation.id),
    }
