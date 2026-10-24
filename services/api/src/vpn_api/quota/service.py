from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, case, func, or_, select
from sqlalchemy.orm import Session

from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    PeerWindowAllocation,
    PeerWindowReceipt,
    QuotaFundingLot,
    QuotaFundingUse,
    QuotaLedger,
    QuotaOperation,
    TunnelReservation,
    User,
)
from vpn_api.quota.contracts import (
    MAX_BYTES,
    HistoryResponse,
    LedgerCommand,
    LedgerEntryResponse,
    OperationResult,
    QuotaError,
    QuotaResponse,
    ReconciliationResult,
)


class LedgerService:
    """Trusted transaction boundary. There is deliberately no HTTP write adapter."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def held_lots(session: Session, user_id: UUID) -> dict[UUID, int]:
        """Unknown receipts retain their entire hold, including after expiry/revoke."""
        return {
            lot_id: int(amount)
            for lot_id, amount in session.execute(
                select(PeerWindowAllocation.lot_id, func.sum(PeerWindowAllocation.amount_bytes))
                .outerjoin(
                    PeerWindowReceipt,
                    PeerWindowReceipt.window_id == PeerWindowAllocation.window_id,
                )
                .where(
                    PeerWindowAllocation.user_id == user_id,
                    or_(
                        PeerWindowReceipt.window_id.is_(None),
                        PeerWindowReceipt.observation == "unknown",
                    ),
                )
                .group_by(PeerWindowAllocation.lot_id)
            )
        }

    @staticmethod
    def _lock_owner(session: Session, user_id: UUID, *, active: bool = True) -> None:
        owner = session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if owner is None or (active and owner.status != "active"):
            raise QuotaError("quota_owner_unavailable")

    @staticmethod
    def _state(session: Session, user_id: UUID, now: datetime | None = None) -> QuotaResponse:
        now = now if now is not None else session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise QuotaError("database_clock_unavailable", 503)
        row = session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case((QuotaLedger.kind == "credit", QuotaLedger.delta_bytes), else_=0)
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case((QuotaLedger.kind == "debit", -QuotaLedger.delta_bytes), else_=0)
                    ),
                    0,
                ),
                func.count(),
                func.coalesce(func.max(QuotaLedger.sequence), 0),
            ).where(QuotaLedger.user_id == user_id)
        ).one()
        granted, used, count, maximum = (int(item) for item in row)
        accepted = (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.user_id == user_id, QuotaOperation.status == "applied")
            )
            or 0
        )
        head = session.scalar(
            select(QuotaLedger)
            .where(QuotaLedger.user_id == user_id)
            .order_by(QuotaLedger.sequence.desc())
            .limit(1)
        )
        if (
            not 0 <= used <= granted <= MAX_BYTES
            or count != maximum
            or count != accepted
            or count > MAX_BYTES
            or (head is not None and head.balance_after_bytes != granted - used)
        ):
            raise QuotaError("quota_inconsistent", 503)
        initial = session.scalar(
            select(QuotaLedger.id).where(
                QuotaLedger.user_id == user_id, QuotaLedger.source_type == "registration"
            )
        )
        reserved = int(
            session.scalar(
                select(func.coalesce(func.sum(TunnelReservation.amount_bytes), 0)).where(
                    TunnelReservation.user_id == user_id, TunnelReservation.settled_at.is_(None)
                )
            )
            or 0
        )
        held = LedgerService.held_lots(session, user_id)
        reserved += sum(held.values())
        funded = unavailable = 0
        for lot in session.scalars(
            select(QuotaFundingLot).where(QuotaFundingLot.user_id == user_id)
        ):
            locked = held.pop(lot.id, 0)
            if not 0 <= locked <= lot.remaining_bytes:
                raise QuotaError("quota_inconsistent", 503)
            funded += lot.remaining_bytes
            if (
                lot.revoked_at is not None
                or lot.starts_at > now
                or (lot.expires_at is not None and lot.expires_at <= now)
            ):
                unavailable += lot.remaining_bytes - locked
        if held:
            raise QuotaError("quota_inconsistent", 503)
        if int(funded) != granted - used or not 0 <= reserved <= granted - used - unavailable:
            raise QuotaError("quota_inconsistent", 503)
        return QuotaResponse(
            granted_bytes=granted,
            used_bytes=used,
            available_bytes=granted - used - reserved - unavailable,
            reserved_bytes=reserved,
            unavailable_bytes=unavailable,
            revision=count,
            initial_grant_applied=initial is not None,
        )

    @staticmethod
    def _result(session: Session, receipt: QuotaOperation) -> OperationResult:
        entry_id = session.scalar(
            select(QuotaLedger.id).where(QuotaLedger.operation_id == receipt.id)
        )
        if (receipt.status == "applied") != (entry_id is not None):
            raise QuotaError("quota_inconsistent", 503)
        return OperationResult.model_validate(
            {
                "operation_id": receipt.id,
                "ledger_entry_id": entry_id,
                "status": receipt.status,
                "error_code": receipt.error_code,
                "balance_after_bytes": receipt.balance_after_bytes,
            }
        )

    def apply(self, user_id: UUID, command: LedgerCommand) -> OperationResult:
        with transaction(self.engine) as session:
            return self.apply_in_transaction(session, user_id, command)

    def apply_in_transaction(
        self, session: Session, user_id: UUID, command: LedgerCommand
    ) -> OperationResult:
        self._lock_owner(session, user_id)
        return self._post(
            session,
            user_id,
            command.kind,
            command.amount_bytes,
            "internal",
            command.source_id,
            command.idempotency_key,
        )

    def ensure_initial_grant(self, session: Session, user_id: UUID, amount: int) -> None:
        if type(amount) is not int or not 0 <= amount <= MAX_BYTES:
            raise QuotaError("invalid_quota_amount", 422)
        self._lock_owner(session, user_id)
        receipt = session.scalar(
            select(QuotaOperation).where(
                QuotaOperation.user_id == user_id, QuotaOperation.source_type == "registration"
            )
        )
        # Configuration changes must not alter an already processed account's welcome grant.
        result = (
            self._result(session, receipt)
            if receipt is not None
            else self._post(session, user_id, "credit", amount, "registration", user_id, user_id)
        )
        if result.status != "applied":
            raise QuotaError("initial_quota_unavailable", 503)

    def apply_usage_in_transaction(
        self, session: Session, user_id: UUID, amount: int, sample_id: UUID
    ) -> OperationResult:
        """Only a scoped, validated usage binding may call this; not an HTTP/user permission."""
        if type(amount) is not int or not 0 < amount <= MAX_BYTES:
            raise QuotaError("invalid_quota_amount", 422)
        # Final accounting remains possible after account disable; this cannot grant access.
        self._lock_owner(session, user_id, active=False)
        return self._post(session, user_id, "debit", amount, "usage", sample_id, sample_id)

    def apply_tunnel_settlement_in_transaction(
        self, session: Session, user_id: UUID, amount: int, tunnel_id: UUID
    ) -> OperationResult:
        """Final snapshot only; distinct source so I-008 cannot reconcile it as a sample."""
        if type(amount) is not int or not 0 < amount <= MAX_BYTES:
            raise QuotaError("invalid_quota_amount", 422)
        self._lock_owner(session, user_id, active=False)
        return self._post(session, user_id, "debit", amount, "tunnel_lease", tunnel_id, tunnel_id)

    def _post(
        self,
        session: Session,
        user_id: UUID,
        kind: str,
        amount: int,
        source_type: str,
        source_id: UUID,
        key: UUID,
        *,
        starts_at: datetime | None = None,
        expires_at: datetime | None = None,
        pinned: dict[UUID, int] | None = None,
    ) -> OperationResult:
        if (source_type == "peer_window") != (pinned is not None):
            raise QuotaError("invalid_window_funding", 503)
        existing = session.scalar(
            select(QuotaOperation).where(
                QuotaOperation.user_id == user_id, QuotaOperation.idempotency_key == key
            )
        )
        if existing is not None:
            if (existing.kind, existing.amount_bytes, existing.source_type, existing.source_id) != (
                kind,
                amount,
                source_type,
                source_id,
            ):
                raise QuotaError("quota_idempotency_conflict")
            return self._result(session, existing)
        business = session.scalar(
            select(QuotaOperation.id).where(
                QuotaOperation.user_id == user_id,
                QuotaOperation.source_type == source_type,
                QuotaOperation.source_id == source_id,
            )
        )
        if business is not None:
            raise QuotaError("quota_source_conflict")
        now = session.scalar(select(func.clock_timestamp()))
        current = self._state(session, user_id, now)
        error: str | None = None
        if kind == "debit" and amount > (
            sum(pinned.values()) if pinned is not None else current.available_bytes
        ):
            error = "insufficient_quota"
        elif (
            current.revision == MAX_BYTES
            or (kind == "credit" and current.granted_bytes + amount > MAX_BYTES)
            or (kind == "debit" and current.used_bytes + amount > MAX_BYTES)
        ):
            error = "quota_capacity_exceeded"
        delta = amount if kind == "credit" else -amount
        prior_balance = current.granted_bytes - current.used_bytes
        balance = prior_balance + delta if error is None else prior_balance
        receipt = QuotaOperation(
            user_id=user_id,
            idempotency_key=key,
            kind=kind,
            amount_bytes=amount,
            source_type=source_type,
            source_id=source_id,
            status="applied" if error is None else "rejected",
            error_code=error,
            balance_after_bytes=balance,
            revision_before=current.revision,
            reserved_before_bytes=current.reserved_bytes - (sum(pinned.values()) if pinned else 0),
            unavailable_before_bytes=current.unavailable_bytes,
            created_at=now,
        )
        session.add(receipt)
        session.flush()
        if error is None:
            session.add(
                QuotaLedger(
                    user_id=user_id,
                    operation_id=receipt.id,
                    sequence=current.revision + 1,
                    kind=kind,
                    delta_bytes=delta,
                    source_type=source_type,
                    source_id=source_id,
                    idempotency_key=key,
                    balance_after_bytes=balance,
                    created_at=now,
                )
            )
            session.flush()
            if kind == "credit":
                session.add(
                    QuotaFundingLot(
                        id=receipt.id,
                        user_id=user_id,
                        granted_bytes=amount,
                        initial_remaining_bytes=amount,
                        remaining_bytes=amount,
                        starts_at=starts_at or now,
                        expires_at=expires_at,
                    )
                )
            else:
                remaining = amount
                held = self.held_lots(session, user_id)
                lots = session.scalars(
                    select(QuotaFundingLot)
                    .where(
                        QuotaFundingLot.user_id == user_id,
                        QuotaFundingLot.remaining_bytes > 0,
                        *(
                            [QuotaFundingLot.id.in_(pinned)]
                            if pinned is not None
                            else [
                                QuotaFundingLot.starts_at <= now,
                                QuotaFundingLot.revoked_at.is_(None),
                                or_(
                                    QuotaFundingLot.expires_at.is_(None),
                                    QuotaFundingLot.expires_at > now,
                                ),
                            ]
                        ),
                    )
                    .order_by(
                        QuotaFundingLot.expires_at.asc().nulls_last(),
                        QuotaFundingLot.starts_at,
                        QuotaFundingLot.id,
                    )
                    .with_for_update()
                )
                ordered = list(lots)
                if pinned is not None:
                    ordered.sort(key=lambda lot: list(pinned).index(lot.id))
                for lot in ordered:
                    capacity = (
                        pinned[lot.id]
                        if pinned is not None
                        else (lot.remaining_bytes - held.get(lot.id, 0))
                    )
                    spent = min(remaining, capacity)
                    if spent:
                        lot.remaining_bytes -= spent
                        session.add(
                            QuotaFundingUse(
                                operation_id=receipt.id,
                                lot_id=lot.id,
                                user_id=user_id,
                                amount_bytes=spent,
                            )
                        )
                        remaining -= spent
                    if remaining == 0:
                        break
                if remaining:
                    raise QuotaError("quota_inconsistent", 503)
            session.flush()
        return self._result(session, receipt)

    def quota(self, session: Session, user_id: UUID) -> QuotaResponse:
        self._lock_owner(session, user_id)
        return self._state(session, user_id)

    def history(
        self,
        session: Session,
        user_id: UUID,
        *,
        limit: int = 50,
        before_sequence: int | None = None,
        snapshot_revision: int | None = None,
        include_inactive: bool = False,
    ) -> HistoryResponse:
        self._lock_owner(session, user_id, active=not include_inactive)
        current = self._state(session, user_id)
        snapshot = current.revision if snapshot_revision is None else snapshot_revision
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or not 0 <= snapshot <= current.revision
            or (before_sequence is not None and not 1 <= before_sequence <= snapshot + 1)
        ):
            raise QuotaError("invalid_quota_cursor", 422)
        statement = select(QuotaLedger).where(
            QuotaLedger.user_id == user_id, QuotaLedger.sequence <= snapshot
        )
        if before_sequence is not None:
            statement = statement.where(QuotaLedger.sequence < before_sequence)
        rows = list(
            session.scalars(statement.order_by(QuotaLedger.sequence.desc()).limit(limit + 1))
        )
        items = [LedgerEntryResponse.model_validate(row) for row in rows[:limit]]
        return HistoryResponse(
            items=items,
            snapshot_revision=snapshot,
            next_before_sequence=items[-1].sequence if len(rows) > limit else None,
        )

    def reconcile(self, session: Session, user_id: UUID) -> ReconciliationResult:
        self._lock_owner(session, user_id, active=False)
        receipts = {
            row.id: row
            for row in session.scalars(
                select(QuotaOperation).where(QuotaOperation.user_id == user_id)
            )
        }
        entries = list(
            session.scalars(
                select(QuotaLedger)
                .where(QuotaLedger.user_id == user_id)
                .order_by(QuotaLedger.sequence)
            )
        )
        violations = granted = used = 0
        prefixes = {0: (0, 0, 0)}
        matched: set[UUID] = set()
        for expected, entry in enumerate(entries, start=1):
            if entry.kind == "credit":
                granted += entry.delta_bytes
            else:
                used -= entry.delta_bytes
            balance = granted - used
            violations += int(
                entry.sequence != expected
                or entry.balance_after_bytes != balance
                or not 0 <= used <= granted <= MAX_BYTES
            )
            prefixes[entry.sequence] = (granted, used, balance)
            receipt = receipts.get(entry.operation_id)
            if receipt is None:
                violations += 1
                continue
            matched.add(receipt.id)
            violations += int(
                receipt.status != "applied"
                or receipt.error_code is not None
                or (
                    receipt.kind,
                    receipt.amount_bytes,
                    receipt.source_type,
                    receipt.source_id,
                    receipt.idempotency_key,
                    receipt.balance_after_bytes,
                    receipt.revision_before,
                )
                != (
                    entry.kind,
                    abs(entry.delta_bytes),
                    entry.source_type,
                    entry.source_id,
                    entry.idempotency_key,
                    entry.balance_after_bytes,
                    entry.sequence - 1,
                )
            )
        for receipt in receipts.values():
            if receipt.status == "applied":
                violations += int(receipt.id not in matched)
                continue
            prefix = prefixes.get(receipt.revision_before)
            if prefix is None or receipt.id in matched:
                violations += 1
                continue
            prior_grants, prior_used, prior_balance = prefix
            violations += int(
                not 0 <= receipt.reserved_before_bytes <= prior_balance
                or not 0
                <= receipt.unavailable_before_bytes
                <= prior_balance - receipt.reserved_before_bytes
            )
            expected_error = (
                "insufficient_quota"
                if receipt.kind == "debit"
                and receipt.amount_bytes
                > prior_balance - receipt.reserved_before_bytes - receipt.unavailable_before_bytes
                else "quota_capacity_exceeded"
                if (
                    receipt.revision_before == MAX_BYTES
                    or (
                        receipt.kind == "credit" and prior_grants + receipt.amount_bytes > MAX_BYTES
                    )
                    or (receipt.kind == "debit" and prior_used + receipt.amount_bytes > MAX_BYTES)
                )
                else None
            )
            violations += int(
                receipt.error_code != expected_error
                or expected_error is None
                or receipt.balance_after_bytes != prior_balance
            )
        reserved = int(
            session.scalar(
                select(func.coalesce(func.sum(TunnelReservation.amount_bytes), 0)).where(
                    TunnelReservation.user_id == user_id, TunnelReservation.settled_at.is_(None)
                )
            )
            or 0
        )
        reserved += sum(self.held_lots(session, user_id).values())
        violations += int(not 0 <= reserved <= granted - used)
        lease_ops = {
            row.source_id: row for row in receipts.values() if row.source_type == "tunnel_lease"
        }
        for reservation in session.scalars(
            select(TunnelReservation).where(TunnelReservation.user_id == user_id)
        ):
            operation = lease_ops.pop(reservation.tunnel_id, None)
            if reservation.settled_at is None or reservation.charged_bytes == 0:
                violations += int(operation is not None)
            else:
                violations += int(
                    operation is None
                    or operation.kind != "debit"
                    or operation.status != "applied"
                    or operation.amount_bytes != reservation.charged_bytes
                    or operation.idempotency_key != reservation.tunnel_id
                )
        violations += len(lease_ops)
        lots = list(
            session.scalars(select(QuotaFundingLot).where(QuotaFundingLot.user_id == user_id))
        )
        violations += int(len(lots) != sum(entry.kind == "credit" for entry in entries))
        violations += int(sum(lot.remaining_bytes for lot in lots) != granted - used)
        for lot in lots:
            allocated = int(
                session.scalar(
                    select(func.coalesce(func.sum(QuotaFundingUse.amount_bytes), 0)).where(
                        QuotaFundingUse.lot_id == lot.id
                    )
                )
                or 0
            )
            violations += int(lot.initial_remaining_bytes - allocated != lot.remaining_bytes)
            credit = receipts.get(lot.id)
            violations += int(
                credit is None
                or credit.kind != "credit"
                or credit.status != "applied"
                or credit.amount_bytes != lot.granted_bytes
            )
        now = session.scalar(select(func.clock_timestamp()))
        if not isinstance(now, datetime):
            raise QuotaError("database_clock_unavailable", 503)
        held = self.held_lots(session, user_id)
        unavailable = sum(
            lot.remaining_bytes - held.get(lot.id, 0)
            for lot in lots
            if lot.revoked_at is not None
            or lot.starts_at > now
            or (lot.expires_at is not None and lot.expires_at <= now)
        )
        violations += sum(int(held.get(lot.id, 0) > lot.remaining_bytes) for lot in lots)
        from vpn_api.tunnels.window_funding import reconcile_windows

        violations += reconcile_windows(session, user_id, receipts)
        violations += int(not 0 <= reserved <= granted - used - unavailable)
        for operation_id, amount in session.execute(
            select(QuotaFundingUse.operation_id, func.sum(QuotaFundingUse.amount_bytes))
            .where(QuotaFundingUse.user_id == user_id)
            .group_by(QuotaFundingUse.operation_id)
        ):
            debit = receipts.get(operation_id)
            violations += int(
                debit is None
                or debit.kind != "debit"
                or debit.status != "applied"
                or debit.amount_bytes != int(amount)
            )
        return ReconciliationResult(
            consistent=violations == 0,
            ledger_entries=len(entries),
            receipts=len(receipts),
            violations=violations,
            granted_bytes=granted,
            used_bytes=used,
            available_bytes=granted - used - reserved - unavailable,
        )
