"""Real disposable PostgreSQL; synthetic window evidence, never VPN/kernel acceptance."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_migrations import migration_config
from test_operations import binaries, key
from test_peer_windows import grant
from test_quota import LedgerHarness, balance, event, ledger, reconcile

from vpn_api import operations as ops
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    PeerWindowAllocation,
    PeerWindowReceipt,
    QuotaFundingLot,
    QuotaFundingUse,
    User,
)
from vpn_api.quota.contracts import QuotaError
from vpn_api.tunnels.v3_contracts import ClosedWindow, ContractError, WindowGrant
from vpn_api.tunnels.window_funding import reserve_window, settle_window

__all__ = ["ledger", "key", "binaries"]
pytestmark = pytest.mark.postgres
BUDGET = 65536


def window(h: LedgerHarness, **changes: object) -> WindowGrant:
    with transaction(h.engine) as session:
        now = database_now(session)
    return grant(
        issued_at=now, not_after=now + timedelta(seconds=30), budget_bytes=BUDGET, **changes
    )


def close(value: WindowGrant, amount: int | None) -> ClosedWindow:
    return ClosedWindow.model_validate(
        {
            **{
                name: getattr(value, name)
                for name in (
                    "connection_id",
                    "window_id",
                    "node_id",
                    "runtime_id",
                    "slot_id",
                    "generation",
                )
            },
            "gate_closed": True,
            "observation": "unknown" if amount is None else "measured",
            "meter": "kernel-outer-ip.v1",
            "observed_bytes": amount,
        }
    )


def reserve(h: LedgerHarness, value: WindowGrant) -> None:
    with transaction(h.engine) as session:
        reserve_window(session, h.user_id, value)


def settle(h: LedgerHarness, value: WindowGrant, amount: int | None) -> None:
    with transaction(h.engine) as session:
        settle_window(session, h.user_id, close(value, amount))


def test_hold_blocks_normal_debit_and_settles_exact_original_lot(ledger: LedgerHarness) -> None:
    h = ledger
    first = h.service.apply(h.user_id, event("credit", BUDGET))
    second = h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    reserve(h, value)
    reserve(h, value)
    assert balance(h).reserved_bytes == BUDGET
    assert h.service.apply(h.user_id, event("debit", BUDGET + 1)).status == "rejected"
    assert h.service.apply(h.user_id, event("debit", BUDGET)).status == "applied"
    with transaction(h.engine) as session:
        first_lot = session.get(QuotaFundingLot, first.operation_id)
        second_lot = session.get(QuotaFundingLot, second.operation_id)
        assert first_lot is not None and first_lot.remaining_bytes == BUDGET
        assert second_lot is not None and second_lot.remaining_bytes == 0
    for _ in range(2):
        settle(h, value, 12345)
    assert balance(h).used_bytes == BUDGET + 12345
    assert balance(h).reserved_bytes == 0
    with transaction(h.engine) as session:
        receipt = session.get(PeerWindowReceipt, value.window_id)
        assert receipt is not None
        uses = list(
            session.scalars(
                select(QuotaFundingUse).where(
                    QuotaFundingUse.operation_id == receipt.operation_id,
                )
            )
        )
        assert [(row.lot_id, row.amount_bytes) for row in uses] == [(first.operation_id, 12345)]
        assert h.service.history(session, h.user_id).items[0].source_type == "peer_window"
    reconcile(h)


@pytest.mark.parametrize("amount", [None, 0, BUDGET])
@pytest.mark.parametrize("invalidation", ["expired", "revoked"])
def test_inactive_lot_does_not_double_count_hold(
    ledger: LedgerHarness,
    amount: int | None,
    invalidation: str,
) -> None:
    h = ledger
    with transaction(h.engine) as session:
        h.service._lock_owner(session, h.user_id)
        now = database_now(session)
        credit = h.service._post(
            session,
            h.user_id,
            "credit",
            BUDGET * 2,
            "internal",
            uuid4(),
            uuid4(),
            expires_at=now + timedelta(seconds=1.5),
        )
        value = grant(issued_at=now, not_after=now + timedelta(seconds=1), budget_bytes=BUDGET)
        reserve_window(session, h.user_id, value)
    if invalidation == "expired":
        time.sleep(1.6)
    else:
        with transaction(h.engine) as session:
            h.service._lock_owner(session, h.user_id)
            lot = session.get(QuotaFundingLot, credit.operation_id)
            assert lot is not None
            lot.revoked_at = database_now(session)
    before = balance(h)
    assert (before.reserved_bytes, before.unavailable_bytes, before.available_bytes) == (
        BUDGET,
        BUDGET,
        0,
    )
    settle(h, value, amount)
    after = balance(h)
    assert after.used_bytes == (amount or 0)
    assert after.reserved_bytes == (BUDGET if amount is None else 0)
    assert after.unavailable_bytes == BUDGET * 2 - (amount or 0) - after.reserved_bytes
    reconcile(h)


def test_unknown_is_immutable_and_cannot_release_or_renew(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET * 3))
    value = window(h)
    reserve(h, value)
    settle(h, value, None)
    settle(h, value, None)
    with pytest.raises(QuotaError, match="window_receipt_conflict"):
        settle(h, value, 0)
    next_window = WindowGrant.model_validate(
        {
            **value.model_dump(),
            "window_id": uuid4(),
            "generation": 2,
            "not_after": value.not_after + timedelta(seconds=1),
        }
    )
    with pytest.raises(QuotaError, match="window_reconciliation_required"):
        reserve(h, next_window)
    assert balance(h).reserved_bytes == BUDGET
    assert balance(h).used_bytes == 0
    reconcile(h)


def test_fifo_split_holds_and_other_debits_use_only_free_bytes(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET // 2))
    h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    reserve(h, value)
    assert h.service.apply(h.user_id, event("debit", BUDGET // 2)).status == "applied"
    settle(h, value, BUDGET * 3 // 4)
    with transaction(h.engine) as session:
        allocations = list(
            session.scalars(
                select(PeerWindowAllocation).order_by(
                    PeerWindowAllocation.position,
                )
            )
        )
        assert [row.amount_bytes for row in allocations] == [BUDGET // 2, BUDGET // 2]
    assert balance(h).available_bytes == BUDGET // 4
    reconcile(h)


def test_concurrent_holds_and_debit_cannot_overspend(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET))
    values = [window(h) for _ in range(5)]
    barrier = Barrier(6)

    def race(index: int) -> str:
        barrier.wait(timeout=10)
        if index == 5:
            return h.service.apply(h.user_id, event("debit", BUDGET)).status
        try:
            reserve(h, values[index])
            return "applied"
        except QuotaError as exc:
            assert exc.code == "insufficient_quota"
            return "rejected"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(race, range(6)))
    assert outcomes.count("applied") == 1
    reconcile(h)


def test_concurrent_receipt_replays_debit_once(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    reserve(h, value)
    barrier = Barrier(5)

    def finish(_: int) -> None:
        barrier.wait(timeout=10)
        settle(h, value, 123)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(finish, range(5)))
    assert balance(h).used_bytes == 123
    reconcile(h)


@pytest.mark.parametrize("field", ["runtime_id", "node_id", "connection_id", "generation"])
def test_receipt_binding_rejected(ledger: LedgerHarness, field: str) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    reserve(h, value)
    forged = ClosedWindow.model_validate(
        {
            **close(value, 1).model_dump(),
            field: 2 if field == "generation" else uuid4(),
        }
    )
    with pytest.raises(ContractError, match="receipt_binding_conflict"), transaction(h.engine) as s:
        settle_window(s, h.user_id, forged)
    assert balance(h).reserved_bytes == BUDGET
    reconcile(h)


def test_owner_disable_final_settlement_and_owner_isolation(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    reserve(h, value)
    with transaction(h.engine) as session:
        owner = session.get(User, h.user_id)
        assert owner is not None
        owner.status = "disabled"
        other = User(status="active")
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(QuotaError, match="window_not_found"), transaction(h.engine) as session:
        settle_window(session, other_id, close(value, 1))
    with pytest.raises(QuotaError, match="quota_owner_unavailable"):
        reserve(h, value)
    settle(h, value, 10)
    with transaction(h.engine) as session:
        assert h.service.reconcile(session, h.user_id).consistent


def test_transaction_rollback_and_protected_downgrade(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    with pytest.raises(RuntimeError, match="TEST ONLY"), transaction(h.engine) as session:
        reserve_window(session, h.user_id, value)
        raise RuntimeError("TEST ONLY rollback")
    assert balance(h).reserved_bytes == 0
    reserve(h, value)
    with pytest.raises(RuntimeError, match="TEST ONLY"), transaction(h.engine) as session:
        settle_window(session, h.user_id, close(value, 123))
        raise RuntimeError("TEST ONLY rollback")
    assert balance(h).used_bytes == 0
    assert balance(h).reserved_bytes == BUDGET
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0013_admin")
    reconcile(h)


@pytest.mark.parametrize(
    "table", ["peer_window_holds", "peer_window_allocations", "peer_window_receipts"]
)
@pytest.mark.parametrize("verb", ["DELETE FROM", "TRUNCATE"])
def test_database_history_immutable(ledger: LedgerHarness, table: str, verb: str) -> None:
    with pytest.raises(DBAPIError), ledger.engine.begin() as connection:
        connection.execute(text(f"{verb} {table}"))


def test_encrypted_restore_keeps_unresolved_and_measured_funding(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET * 4))
    for amount in (None, 0, 123):
        value = window(h)
        reserve(h, value)
        settle(h, value, amount)
    archive = tmp_path / "windows.fernet"
    backup = ops.make_backup(h.engine, binaries, archive, key)
    assert backup.tables["peer_window_holds"].rows == 3
    assert backup.tables["peer_window_receipts"].rows == 3
    assert ops.restore_drill(h.engine, binaries, archive, key)["accounts_reconciled"] == 1
    reconcile(h)


def test_funding_deadline_and_revocation_fail_closed(ledger: LedgerHarness) -> None:
    h = ledger
    with transaction(h.engine) as session:
        h.service._lock_owner(session, h.user_id)
        now = database_now(session)
        credit = h.service._post(
            session,
            h.user_id,
            "credit",
            BUDGET,
            "internal",
            uuid4(),
            uuid4(),
            expires_at=now + timedelta(seconds=20),
        )
    value = window(h)
    with pytest.raises(QuotaError, match="window_funding_deadline"):
        reserve(h, value)
    with transaction(h.engine) as session:
        lot = session.get(QuotaFundingLot, credit.operation_id)
        assert lot is not None
        lot.revoked_at = database_now(session)
    with pytest.raises(QuotaError, match="insufficient_quota"):
        reserve(h, value)
    assert balance(h).reserved_bytes == 0
    reconcile(h)


def test_renewal_pending_bound_and_generation_runtime_fencing(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET * 5))
    first = window(h)
    reserve(h, first)
    second = WindowGrant.model_validate(
        {
            **first.model_dump(),
            "window_id": uuid4(),
            "generation": 2,
            "not_after": first.not_after + timedelta(seconds=10),
        }
    )
    for field, replacement in (("runtime_id", uuid4()), ("generation", 3)):
        altered = WindowGrant.model_validate({**second.model_dump(), field: replacement})
        with pytest.raises(ContractError):
            reserve(h, altered)
    reserve(h, second)
    third = WindowGrant.model_validate(
        {
            **second.model_dump(),
            "window_id": uuid4(),
            "generation": 3,
            "not_after": second.not_after + timedelta(seconds=10),
        }
    )
    with pytest.raises(QuotaError, match="window_reconciliation_required"):
        reserve(h, third)
    settle(h, first, 123)
    reserve(h, third)
    assert balance(h).reserved_bytes == BUDGET * 2
    reconcile(h)


def test_replay_payload_and_receipt_evidence_conflicts(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET * 2))
    value = window(h)
    reserve(h, value)
    changed = WindowGrant.model_validate({**value.model_dump(), "budget_bytes": BUDGET + 1})
    with pytest.raises(QuotaError, match="window_funding_conflict"):
        reserve(h, changed)
    for changes in (
        {"gate_closed": False},
        {"observed_bytes": BUDGET + 1},
        {"observed_bytes": None},
    ):
        invalid = ClosedWindow.model_validate({**close(value, 1).model_dump(), **changes})
        with pytest.raises(ContractError), transaction(h.engine) as session:
            settle_window(session, h.user_id, invalid)
    settle(h, value, 1)
    with pytest.raises(QuotaError, match="window_receipt_conflict"):
        settle(h, value, 2)
    reconcile(h)


def test_database_rejects_null_measured_evidence(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    reserve(h, value)
    with pytest.raises(DBAPIError), transaction(h.engine) as session:
        session.add(
            PeerWindowReceipt(
                window_id=value.window_id,
                user_id=h.user_id,
                observation="measured",
                observed_bytes=None,
                operation_id=None,
                receipt={},
                created_at=database_now(session),
            )
        )
    reconcile(h)


def test_reconciliation_detects_wrong_pinned_lot(ledger: LedgerHarness) -> None:
    h = ledger
    first = h.service.apply(h.user_id, event("credit", BUDGET))
    second = h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    reserve(h, value)
    settle(h, value, 123)
    # TEST ONLY corruption as disposable DB superuser, never an operations repair path.
    with h.engine.begin() as connection:
        connection.execute(text("ALTER TABLE peer_window_allocations DISABLE TRIGGER USER"))
        connection.execute(
            text("UPDATE peer_window_allocations SET lot_id=:lot WHERE lot_id=:old"),
            {"lot": second.operation_id, "old": first.operation_id},
        )
        connection.execute(text("ALTER TABLE peer_window_allocations ENABLE TRIGGER USER"))
    with transaction(h.engine) as session:
        assert not h.service.reconcile(session, h.user_id).consistent


def test_0014_existing_hold_metadata_backfill_preserves_immutable_history(
    ledger: LedgerHarness,
) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", BUDGET))
    value = window(h)
    config = migration_config(h.engine)
    command.downgrade(config, "0014_peer_windows")
    with h.engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO peer_window_holds(id,user_id,connection_id,generation,amount_bytes,"
                '"grant",created_at,not_after) VALUES(:id,:user_id,:connection_id,1,:budget,'
                "CAST(:payload AS json),:created_at,:not_after)"
            ),
            {
                "id": value.window_id,
                "user_id": h.user_id,
                "connection_id": value.connection_id,
                "budget": value.budget_bytes,
                "payload": json.dumps(value.model_dump(mode="json")),
                "created_at": value.issued_at,
                "not_after": value.not_after,
            },
        )
    command.upgrade(config, "head")
    with h.engine.connect() as db:
        row = db.execute(
            text('SELECT node_id,runtime_id,slot_id,"grant" FROM peer_window_holds')
        ).one()
        assert tuple(row[:3]) == (value.node_id, value.runtime_id, value.slot_id)
        assert row[3] == value.model_dump(mode="json")
    with pytest.raises(DBAPIError), h.engine.begin() as db:
        db.execute(text("UPDATE peer_window_holds SET slot_id=2"))
