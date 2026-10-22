import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    Device,
    QuotaOperation,
    Tunnel,
    TunnelReservation,
    UsageEpoch,
    UsageSample,
    UsageStream,
    User,
)
from vpn_api.quota.contracts import MAX_BYTES
from vpn_api.quota.service import LedgerService
from vpn_api.usage.contracts import UsageAudit, UsageError, UsageResult, UsageSampleInput


def db_time(session: Session) -> datetime:
    value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise UsageError("usage_storage_unavailable")
    return value


@dataclass
class Watermark:
    sequence: int = 0
    rx: int = 0
    tx: int = 0
    charged_rx: int = 0
    charged_tx: int = 0
    observed_at: datetime | None = None

    @property
    def pending(self) -> int:
        return self.rx - self.charged_rx + self.tx - self.charged_tx


@dataclass(frozen=True)
class EpochWindow:
    started_at: datetime
    ended_at: datetime | None


def classify(
    epoch: UsageEpoch | EpochWindow, mark: Watermark, sample: UsageSampleInput, received: datetime
) -> tuple[str, str | None]:
    if sample.sampled_at < epoch.started_at or (
        epoch.ended_at is not None and sample.sampled_at > epoch.ended_at
    ):
        return "rejected", "outside_epoch"
    if sample.sampled_at > received + timedelta(seconds=60):
        return "rejected", "future_sample"
    if sample.sequence <= mark.sequence:
        if (
            sample.rx_total_bytes > mark.rx
            or sample.tx_total_bytes > mark.tx
            or (mark.observed_at is not None and sample.sampled_at > mark.observed_at)
        ):
            return "rejected", "stale_conflict"
        return "stale", None
    if sample.rx_total_bytes < mark.rx or sample.tx_total_bytes < mark.tx:
        return "rejected", "counter_regression"
    if mark.observed_at is not None and sample.sampled_at < mark.observed_at:
        return "rejected", "timestamp_regression"
    return "valid", None


class UsageService:
    """Trusted internal entry point. Identity must come from an independent node transport."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.ledger = LedgerService(engine)

    @staticmethod
    def _lock_id(session: Session, value: str) -> None:
        key = int(hashlib.sha256(value.encode("ascii")).hexdigest()[:15], 16)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    @staticmethod
    def _owner(session: Session, user_id: UUID, *, active: bool = False) -> None:
        user = session.scalar(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or (active and user.status != "active"):
            raise UsageError("usage_owner_unavailable")

    def bind(self, trusted_node_id: UUID, peer_id: UUID, device_id: UUID) -> UUID:
        """Trusted provisioning only; never bind identity supplied by a public sample."""
        with transaction(self.engine) as session:
            device = session.get(Device, device_id)
            if device is None:
                raise UsageError("usage_device_unavailable")
            self._owner(session, device.user_id, active=True)
            session.refresh(device)
            if device.revoked_at is not None:
                raise UsageError("usage_device_unavailable")
            managed = session.scalar(
                select(Tunnel.id)
                .join(TunnelReservation, TunnelReservation.tunnel_id == Tunnel.id)
                .where(Tunnel.id == peer_id, Tunnel.node_id == trusted_node_id)
            )
            if managed is not None:
                raise UsageError("usage_managed_by_lease")
            self._lock_id(session, "binding:" + trusted_node_id.hex + ":" + peer_id.hex)
            existing = session.scalar(
                select(UsageStream).where(
                    UsageStream.node_id == trusted_node_id, UsageStream.peer_id == peer_id
                )
            )
            if existing is not None:
                if existing.device_id != device_id:
                    raise UsageError("usage_binding_conflict")
                return existing.id
            binding = UsageStream(
                node_id=trusted_node_id,
                peer_id=peer_id,
                device_id=device_id,
                user_id=device.user_id,
                created_at=db_time(session),
            )
            session.add(binding)
            session.flush()
            return binding.id

    def _stream(
        self, session: Session, node_id: UUID, peer_id: UUID, *, active: bool = False
    ) -> UsageStream:
        stream = session.scalar(
            select(UsageStream).where(
                UsageStream.node_id == node_id, UsageStream.peer_id == peer_id
            )
        )
        if stream is None:
            raise UsageError("usage_source_unknown")
        self._owner(session, stream.user_id, active=active)
        session.refresh(stream, with_for_update=True)
        if active:
            device = session.get(Device, stream.device_id)
            if device is None or device.revoked_at is not None:
                raise UsageError("usage_device_unavailable")
        return stream

    def open_epoch(
        self,
        trusted_node_id: UUID,
        peer_id: UUID,
        epoch_id: UUID,
        expected_generation: int,
        started_at: datetime,
    ) -> UUID:
        if (
            type(expected_generation) is not int
            or not 0 <= expected_generation < MAX_BYTES
            or started_at.tzinfo is None
            or started_at.utcoffset() is None
        ):
            raise UsageError("usage_invalid_epoch")
        started_at = started_at.astimezone(UTC)
        with transaction(self.engine) as session:
            stream = self._stream(session, trusted_node_id, peer_id, active=True)
            self._lock_id(session, "epoch:" + epoch_id.hex)
            existing = session.get(UsageEpoch, epoch_id)
            if existing is not None:
                if (
                    existing.stream_id != stream.id
                    or existing.generation != expected_generation + 1
                    or existing.started_at != started_at
                ):
                    raise UsageError("usage_epoch_conflict")
                return existing.id
            if stream.generation != expected_generation:
                raise UsageError("usage_generation_conflict")
            if started_at > db_time(session) + timedelta(seconds=60):
                raise UsageError("usage_invalid_epoch")
            previous = session.scalar(
                select(UsageEpoch)
                .where(
                    UsageEpoch.stream_id == stream.id, UsageEpoch.generation == stream.generation
                )
                .with_for_update()
            )
            if stream.generation > 0 and previous is None:
                raise UsageError("usage_inconsistent")
            if previous is not None:
                self._validate_projection(session, previous)
                if (
                    previous.ended_at is not None
                    or started_at <= previous.started_at
                    or (previous.observed_at is not None and started_at < previous.observed_at)
                ):
                    raise UsageError("usage_invalid_epoch")
                previous.ended_at = started_at
            stream.generation += 1
            session.add(
                UsageEpoch(
                    id=epoch_id,
                    stream_id=stream.id,
                    generation=stream.generation,
                    started_at=started_at,
                    created_at=db_time(session),
                )
            )
            session.flush()
            return epoch_id

    @staticmethod
    def _mark(epoch: UsageEpoch) -> Watermark:
        return Watermark(
            epoch.last_sequence,
            epoch.rx_bytes,
            epoch.tx_bytes,
            epoch.charged_rx,
            epoch.charged_tx,
            epoch.observed_at,
        )

    @staticmethod
    def _validate_projection(session: Session, epoch: UsageEpoch) -> None:
        count, maximum = session.execute(
            select(func.count(), func.coalesce(func.max(UsageSample.arrival_order), 0)).where(
                UsageSample.counter_epoch == epoch.id
            )
        ).one()
        valid = session.scalar(
            select(UsageSample)
            .where(
                UsageSample.counter_epoch == epoch.id,
                UsageSample.status.in_(["charged", "blocked", "no_change"]),
            )
            .order_by(UsageSample.arrival_order.desc())
            .limit(1)
        )
        charged = session.scalar(
            select(UsageSample)
            .where(UsageSample.counter_epoch == epoch.id, UsageSample.status == "charged")
            .order_by(UsageSample.arrival_order.desc())
            .limit(1)
        )
        expected = (
            (valid.sequence, valid.rx_total_bytes, valid.tx_total_bytes, valid.sampled_at)
            if valid is not None
            else (0, 0, 0, None)
        )
        expected_charged = (
            (charged.rx_total_bytes, charged.tx_total_bytes) if charged is not None else (0, 0)
        )
        if (
            count != epoch.receipt_count
            or count != maximum
            or (epoch.last_sequence, epoch.rx_bytes, epoch.tx_bytes, epoch.observed_at) != expected
            or (epoch.charged_rx, epoch.charged_tx) != expected_charged
        ):
            raise UsageError("usage_inconsistent")

    def ingest(self, trusted_node_id: UUID, sample: UsageSampleInput) -> UsageResult:
        with transaction(self.engine) as session:
            return self.ingest_in_transaction(session, trusted_node_id, sample)

    def ingest_in_transaction(
        self, session: Session, trusted_node_id: UUID, sample: UsageSampleInput
    ) -> UsageResult:
        sample = UsageSampleInput.model_validate(sample.model_dump())
        stream = self._stream(session, trusted_node_id, sample.peer_id)
        self._lock_id(
            session, "sample-key:" + trusted_node_id.hex + ":" + sample.idempotency_key.hex
        )
        existing = session.scalar(
            select(UsageSample).where(
                UsageSample.node_id == trusted_node_id,
                UsageSample.idempotency_key == sample.idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.stream_id != stream.id
                or existing.counter_epoch != sample.counter_epoch
                or (
                    existing.schema_version,
                    existing.sequence,
                    existing.sampled_at,
                    existing.rx_total_bytes,
                    existing.tx_total_bytes,
                )
                != (
                    sample.schema_version,
                    sample.sequence,
                    sample.sampled_at,
                    sample.rx_total_bytes,
                    sample.tx_total_bytes,
                )
            ):
                raise UsageError("usage_idempotency_conflict")
            return self._result(existing)
        epoch = session.scalar(
            select(UsageEpoch)
            .where(UsageEpoch.id == sample.counter_epoch, UsageEpoch.stream_id == stream.id)
            .with_for_update()
        )
        if epoch is None:
            raise UsageError("usage_epoch_unknown")
        business = session.scalar(
            select(UsageSample.id).where(
                UsageSample.counter_epoch == epoch.id, UsageSample.sequence == sample.sequence
            )
        )
        if business is not None:
            raise UsageError("usage_sequence_conflict")
        self._validate_projection(session, epoch)
        if epoch.receipt_count == MAX_BYTES:
            raise UsageError("usage_capacity_exceeded")
        now = db_time(session)
        mark = self._mark(epoch)
        status, reason = classify(epoch, mark, sample, now)
        record = UsageSample(
            id=uuid4(),
            node_id=trusted_node_id,
            stream_id=stream.id,
            user_id=stream.user_id,
            counter_epoch=epoch.id,
            schema_version=sample.schema_version,
            sequence=sample.sequence,
            arrival_order=epoch.receipt_count + 1,
            sampled_at=sample.sampled_at,
            epoch_end_at_receipt=epoch.ended_at,
            rx_total_bytes=sample.rx_total_bytes,
            tx_total_bytes=sample.tx_total_bytes,
            idempotency_key=sample.idempotency_key,
            status=status,
            reason=reason,
            delta_bytes=0,
            pending_bytes=mark.pending,
            received_at=now,
        )
        if status == "valid":
            record.delta_bytes = (
                sample.rx_total_bytes - mark.charged_rx + sample.tx_total_bytes - mark.charged_tx
            )
            epoch.last_sequence = sample.sequence
            epoch.rx_bytes, epoch.tx_bytes = sample.rx_total_bytes, sample.tx_total_bytes
            epoch.observed_at = sample.sampled_at
            if record.delta_bytes > 0:
                outcome = self.ledger.apply_usage_in_transaction(
                    session, stream.user_id, record.delta_bytes, record.id
                )
                record.quota_operation_id = outcome.operation_id
                record.status = "charged" if outcome.status == "applied" else "blocked"
                record.reason = outcome.error_code
                if outcome.status == "applied":
                    epoch.charged_rx, epoch.charged_tx = (
                        sample.rx_total_bytes,
                        sample.tx_total_bytes,
                    )
            else:
                record.status = "no_change"
            record.pending_bytes = (
                epoch.rx_bytes - epoch.charged_rx + epoch.tx_bytes - epoch.charged_tx
            )
        epoch.receipt_count += 1
        session.add(record)
        session.flush()
        return self._result(record)

    @staticmethod
    def _result(record: UsageSample) -> UsageResult:
        return UsageResult.model_validate(
            {
                "sample_id": record.id,
                "status": record.status,
                "reason": record.reason,
                "delta_bytes": record.delta_bytes,
                "pending_bytes": record.pending_bytes,
                "quota_operation_id": record.quota_operation_id,
            }
        )

    def audit_user(self, session: Session, user_id: UUID) -> UsageAudit:
        self._owner(session, user_id)
        streams = list(session.scalars(select(UsageStream).where(UsageStream.user_id == user_id)))
        violations = samples_count = epoch_count = pending = 0
        for stream in streams:
            epochs = list(
                session.scalars(
                    select(UsageEpoch)
                    .where(UsageEpoch.stream_id == stream.id)
                    .order_by(UsageEpoch.generation)
                )
            )
            violations += int(len(epochs) != stream.generation)
            for generation, epoch in enumerate(epochs, start=1):
                epoch_count += 1
                next_epoch = epochs[generation] if generation < len(epochs) else None
                violations += int(
                    epoch.generation != generation
                    or epoch.ended_at != (next_epoch.started_at if next_epoch is not None else None)
                )
                mark = Watermark()
                rows = list(
                    session.scalars(
                        select(UsageSample)
                        .where(UsageSample.counter_epoch == epoch.id)
                        .order_by(UsageSample.arrival_order)
                    )
                )
                samples_count += len(rows)
                for arrival, row in enumerate(rows, start=1):
                    sample = UsageSampleInput(
                        schema_version=1,
                        peer_id=stream.peer_id,
                        counter_epoch=epoch.id,
                        sequence=row.sequence,
                        sampled_at=row.sampled_at,
                        rx_total_bytes=row.rx_total_bytes,
                        tx_total_bytes=row.tx_total_bytes,
                        idempotency_key=row.idempotency_key,
                    )
                    status, reason = classify(
                        EpochWindow(epoch.started_at, row.epoch_end_at_receipt),
                        mark,
                        sample,
                        row.received_at,
                    )
                    violations += int(
                        row.epoch_end_at_receipt is not None
                        and row.epoch_end_at_receipt != epoch.ended_at
                    )
                    delta = 0
                    if status == "valid":
                        violations += int(
                            epoch.ended_at is not None and sample.sampled_at > epoch.ended_at
                        )
                        delta = (
                            sample.rx_total_bytes
                            - mark.charged_rx
                            + sample.tx_total_bytes
                            - mark.charged_tx
                        )
                        mark.sequence, mark.rx, mark.tx, mark.observed_at = (
                            sample.sequence,
                            sample.rx_total_bytes,
                            sample.tx_total_bytes,
                            sample.sampled_at,
                        )
                        if delta:
                            operation = (
                                session.get(QuotaOperation, row.quota_operation_id)
                                if row.quota_operation_id
                                else None
                            )
                            if operation is None:
                                violations += 1
                                status = "blocked"
                            else:
                                violations += int(
                                    (
                                        operation.user_id,
                                        operation.kind,
                                        operation.amount_bytes,
                                        operation.source_type,
                                        operation.source_id,
                                        operation.idempotency_key,
                                    )
                                    != (stream.user_id, "debit", delta, "usage", row.id, row.id)
                                )
                                status = "charged" if operation.status == "applied" else "blocked"
                                reason = operation.error_code
                                if operation.status == "applied":
                                    mark.charged_rx, mark.charged_tx = (
                                        sample.rx_total_bytes,
                                        sample.tx_total_bytes,
                                    )
                        else:
                            status = "no_change"
                    violations += int(
                        row.arrival_order != arrival
                        or row.schema_version != 1
                        or (row.node_id, row.user_id, row.stream_id)
                        != (stream.node_id, user_id, stream.id)
                        or (row.status, row.reason, row.delta_bytes, row.pending_bytes)
                        != (status, reason, delta, mark.pending)
                        or (row.quota_operation_id is not None) != (delta > 0)
                    )
                violations += int(epoch.receipt_count != len(rows) or self._mark(epoch) != mark)
                pending += mark.pending
        usage_ops = set(
            session.scalars(
                select(QuotaOperation.id).where(
                    QuotaOperation.user_id == user_id, QuotaOperation.source_type == "usage"
                )
            )
        )
        referenced = set(
            session.scalars(
                select(UsageSample.quota_operation_id).where(
                    UsageSample.user_id == user_id, UsageSample.quota_operation_id.is_not(None)
                )
            )
        )
        violations += len(usage_ops.symmetric_difference(referenced))
        ledger_audit = self.ledger.reconcile(session, user_id)
        violations += ledger_audit.violations
        return UsageAudit(
            consistent=violations == 0,
            streams=len(streams),
            epochs=epoch_count,
            samples=samples_count,
            pending_bytes=pending,
            violations=violations,
        )
