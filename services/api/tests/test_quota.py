"""Real PostgreSQL accounting; values/events are synthetic, never node traffic evidence."""

import json
import os
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Barrier
from typing import Literal
from uuid import UUID, uuid4

import pytest
from alembic import command
from pydantic import ValidationError
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_migrations import migration_config

from vpn_api.config import Settings
from vpn_api.db.connection import transaction
from vpn_api.db.models import QuotaLedger, QuotaOperation, User
from vpn_api.quota.contracts import (
    MAX_BYTES,
    LedgerCommand,
    OperationResult,
    QuotaError,
    QuotaResponse,
)
from vpn_api.quota.service import LedgerService

pytestmark = pytest.mark.postgres


@dataclass
class LedgerHarness:
    engine: Engine
    user_id: UUID
    service: LedgerService


@pytest.fixture
def ledger(engine: Engine) -> LedgerHarness:
    command.upgrade(migration_config(engine), "head")
    with transaction(engine) as session:
        user = User(status="active", verified_at=datetime.now(UTC))
        session.add(user)
        session.flush()
        user_id = user.id
    return LedgerHarness(engine, user_id, LedgerService(engine))


def event(
    kind: Literal["credit", "debit"],
    amount: int,
    *,
    key: UUID | None = None,
    source: UUID | None = None,
) -> LedgerCommand:
    return LedgerCommand(
        kind=kind, amount_bytes=amount, idempotency_key=key or uuid4(), source_id=source or uuid4()
    )


def balance(h: LedgerHarness) -> QuotaResponse:
    with transaction(h.engine) as session:
        return h.service.quota(session, h.user_id)


def reconcile(h: LedgerHarness) -> None:
    with transaction(h.engine) as session:
        result = h.service.reconcile(session, h.user_id)
        assert result.consistent and result.violations == 0
        current = h.service.quota(session, h.user_id)
        assert (result.granted_bytes, result.used_bytes, result.available_bytes) == (
            current.granted_bytes,
            current.used_bytes,
            current.available_bytes,
        )


def test_empty_quota_read_has_no_side_effects(ledger: LedgerHarness) -> None:
    h = ledger
    assert balance(h).model_dump() == {
        "granted_bytes": 0,
        "used_bytes": 0,
        "available_bytes": 0,
        "reserved_bytes": 0,
        "revision": 0,
        "initial_grant_applied": False,
    }
    for _ in range(2):
        with transaction(h.engine) as session:
            assert h.service.history(session, h.user_id).items == []
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperation)) == 0
    reconcile(h)


def test_credit_debit_exact_above_float_precision(ledger: LedgerHarness) -> None:
    h = ledger
    amount = 2**53 + 17
    credit = h.service.apply(h.user_id, event("credit", amount))
    assert credit.status == "applied" and credit.balance_after_bytes == amount
    assert h.service.apply(h.user_id, event("debit", 7)).balance_after_bytes == amount - 7
    assert balance(h).granted_bytes == amount
    assert balance(h).used_bytes == 7
    reconcile(h)


def test_replay_returns_original_result_after_other_operations(ledger: LedgerHarness) -> None:
    h = ledger
    first = event("credit", 100)
    result = h.service.apply(h.user_id, first)
    h.service.apply(h.user_id, event("debit", 20))
    assert h.service.apply(h.user_id, first) == result
    assert result.balance_after_bytes == 100 and balance(h).available_bytes == 80
    assert balance(h).revision == 2
    reconcile(h)


def test_rejected_debit_is_durable_after_later_credit(ledger: LedgerHarness) -> None:
    h = ledger
    debit = event("debit", 50)
    refusal = h.service.apply(h.user_id, debit)
    assert refusal.status == "rejected" and refusal.error_code == "insufficient_quota"
    assert refusal.ledger_entry_id is None and balance(h).revision == 0
    h.service.apply(h.user_id, event("credit", 100))
    assert h.service.apply(h.user_id, debit) == refusal
    assert balance(h).available_bytes == 100 and balance(h).revision == 1
    with pytest.raises(QuotaError, match="quota_source_conflict"):
        h.service.apply(h.user_id, debit.model_copy(update={"idempotency_key": uuid4()}))
    reconcile(h)


@pytest.mark.parametrize("changed", ["amount", "kind", "source"])
def test_idempotency_payload_conflict_does_not_mutate(ledger: LedgerHarness, changed: str) -> None:
    h = ledger
    original = event("credit", 20)
    h.service.apply(h.user_id, original)
    data = original.model_dump()
    data[{"amount": "amount_bytes", "kind": "kind", "source": "source_id"}[changed]] = {
        "amount": 21,
        "kind": "debit",
        "source": uuid4(),
    }[changed]
    with pytest.raises(QuotaError, match="quota_idempotency_conflict"):
        h.service.apply(h.user_id, LedgerCommand.model_validate(data))
    assert balance(h).available_bytes == 20 and balance(h).revision == 1


def test_distinct_key_cannot_duplicate_business_source(ledger: LedgerHarness) -> None:
    h = ledger
    original = event("credit", 20)
    h.service.apply(h.user_id, original)
    with pytest.raises(QuotaError, match="quota_source_conflict"):
        h.service.apply(h.user_id, event("credit", 20, source=original.source_id))
    assert balance(h).available_bytes == 20


def test_integer_overflow_is_rejected_without_partial_posting(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", MAX_BYTES))
    too_much = event("credit", 1)
    refused = h.service.apply(h.user_id, too_much)
    assert refused.error_code == "quota_capacity_exceeded"
    h.service.apply(h.user_id, event("debit", 1))
    assert h.service.apply(h.user_id, too_much) == refused
    assert balance(h).granted_bytes == MAX_BYTES and balance(h).available_bytes == MAX_BYTES - 1
    reconcile(h)


@pytest.mark.parametrize("amount", [0, -1, 1.5, "10", True, MAX_BYTES + 1])
def test_internal_amount_is_strict_integer(amount: object) -> None:
    with pytest.raises(ValidationError):
        LedgerCommand.model_validate(
            {
                "kind": "credit",
                "amount_bytes": amount,
                "source_id": uuid4(),
                "idempotency_key": uuid4(),
            }
        )


@pytest.mark.parametrize("amount", [True, -1, 1.0, "1.0", "1e3", "-1", MAX_BYTES + 1])
def test_initial_quota_settings_reject_invalid_amount(amount: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, initial_quota_bytes=amount)


def test_initial_quota_env_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPN_INITIAL_QUOTA_BYTES", "9007199254740993")
    assert Settings(_env_file=None).initial_quota_bytes == 9007199254740993


def test_initial_grant_once_even_when_configuration_changes(ledger: LedgerHarness) -> None:
    h = ledger
    for amount in (17, 17, 30):
        with transaction(h.engine) as session:
            h.service.ensure_initial_grant(session, h.user_id, amount)
    assert balance(h).available_bytes == 17 and balance(h).revision == 1
    assert balance(h).initial_grant_applied
    reconcile(h)


def test_zero_initial_receipt_does_not_become_later_free_grant(ledger: LedgerHarness) -> None:
    h = ledger
    for amount in (0, 30):
        with transaction(h.engine) as session:
            h.service.ensure_initial_grant(session, h.user_id, amount)
    assert balance(h).granted_bytes == 0 and balance(h).revision == 1
    assert balance(h).initial_grant_applied
    reconcile(h)


def test_concurrent_initial_grants_are_once(ledger: LedgerHarness) -> None:
    h = ledger
    barrier = Barrier(4, timeout=15)

    def grant(_: int) -> None:
        barrier.wait()
        with transaction(h.engine) as session:
            h.service.ensure_initial_grant(session, h.user_id, 17)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(grant, range(4)))
    assert balance(h).available_bytes == 17 and balance(h).revision == 1


@pytest.mark.parametrize("kind", ["credit", "debit"])
def test_concurrent_replay_has_exactly_one_effect(ledger: LedgerHarness, kind: str) -> None:
    h = ledger
    if kind == "debit":
        h.service.apply(h.user_id, event("credit", 100))
    operation = event("debit" if kind == "debit" else "credit", 10)
    barrier = Barrier(6, timeout=15)

    def post(_: int) -> OperationResult:
        barrier.wait()
        return h.service.apply(h.user_id, operation)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(post, range(6)))
    assert len({result.operation_id for result in results}) == 1
    assert all(result.status == "applied" for result in results)
    assert balance(h).available_bytes == (90 if kind == "debit" else 10)
    reconcile(h)


def test_two_debits_cannot_spend_same_balance(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", 100))
    barrier = Barrier(2, timeout=15)

    def spend(_: int) -> str:
        command = event("debit", 80)
        barrier.wait()
        return h.service.apply(h.user_id, command).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(spend, range(2))) == ["applied", "rejected"]
    assert balance(h).available_bytes == 20 and balance(h).used_bytes == 80
    reconcile(h)


def test_mixed_concurrent_operations_remain_reconcilable(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", 50))

    def post(index: int) -> OperationResult:
        return h.service.apply(h.user_id, event("credit" if index % 3 == 0 else "debit", 7))

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(post, range(24)))
    assert len({item.operation_id for item in results}) == 24
    assert balance(h).available_bytes >= 0
    reconcile(h)


@pytest.mark.parametrize("seed", [7, 29, 71])
def test_generated_sequences_match_independent_integer_model(
    ledger: LedgerHarness, seed: int
) -> None:
    h = ledger
    rng = random.Random(seed)  # noqa: S311 - deterministic model cases, not security randomness
    granted = used = applied = 0
    saved: list[tuple[LedgerCommand, OperationResult]] = []
    for _ in range(60):
        if saved and rng.randrange(4) == 0:
            original, result = saved[rng.randrange(len(saved))]
            assert h.service.apply(h.user_id, original) == result
            continue
        kind: Literal["credit", "debit"] = "credit" if rng.randrange(2) == 0 else "debit"
        amount = rng.randrange(1, 25)
        operation = event(kind, amount)
        result = h.service.apply(h.user_id, operation)
        if kind == "debit" and amount > granted - used:
            assert result.error_code == "insufficient_quota"
        else:
            assert result.status == "applied"
            applied += 1
            if kind == "credit":
                granted += amount
            else:
                used += amount
        saved.append((operation, result))
        assert result.balance_after_bytes == granted - used
    current = balance(h)
    assert (current.granted_bytes, current.used_bytes, current.revision) == (granted, used, applied)
    reconcile(h)


def test_transaction_rollback_leaves_neither_receipt_nor_entry(ledger: LedgerHarness) -> None:
    h = ledger
    operation = event("credit", 10)
    with pytest.raises(RuntimeError, match="synthetic abort"), transaction(h.engine) as session:
        h.service.apply_in_transaction(session, h.user_id, operation)
        raise RuntimeError("synthetic abort")
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperation)) == 0
    assert balance(h).revision == 0
    assert h.service.apply(h.user_id, operation).status == "applied"


def test_database_failure_between_receipt_and_entry_rolls_back(ledger: LedgerHarness) -> None:
    h = ledger
    with h.engine.begin() as connection:
        connection.execute(
            text("""CREATE FUNCTION synthetic_insert_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic test abort'; END; $$;""")
        )
        connection.execute(
            text("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON quota_ledger
            FOR EACH ROW EXECUTE FUNCTION synthetic_insert_failure()""")
        )
    operation = event("credit", 10)
    with pytest.raises(DBAPIError, match="synthetic test abort"):
        h.service.apply(h.user_id, operation)
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperation)) == 0
    with h.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER synthetic_failure ON quota_ledger"))
        connection.execute(text("DROP FUNCTION synthetic_insert_failure()"))
    assert h.service.apply(h.user_id, operation).status == "applied"


@pytest.mark.parametrize("table", ["quota_ledger", "quota_operations"])
@pytest.mark.parametrize("action", ["UPDATE", "DELETE", "TRUNCATE"])
def test_append_only_guards(ledger: LedgerHarness, table: str, action: str) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", 10))
    statement = (
        f"UPDATE {table} SET kind = kind"  # noqa: S608 - fixed parameter lists
        if action == "UPDATE"
        else f"DELETE FROM {table}"  # noqa: S608 - fixed parameter lists
        if action == "DELETE"
        else f"TRUNCATE {table} CASCADE"
    )  # noqa: S608 - fixed parameter lists
    with pytest.raises(DBAPIError, match="append only"), h.engine.begin() as connection:
        connection.execute(text(statement))
    assert balance(h).available_bytes == 10


def test_same_keys_are_scoped_to_owner(ledger: LedgerHarness) -> None:
    h = ledger
    with transaction(h.engine) as session:
        other = User(status="active")
        session.add(other)
        session.flush()
        other_id = other.id
    operation = event("credit", 17)
    first = h.service.apply(h.user_id, operation)
    second = h.service.apply(other_id, operation)
    assert first.operation_id != second.operation_id
    assert balance(h).available_bytes == 17


def test_disabled_owner_cannot_post_but_can_be_reconciled(ledger: LedgerHarness) -> None:
    h = ledger
    with transaction(h.engine) as session:
        owner = session.get(User, h.user_id)
        assert owner is not None
        owner.status = "disabled"
    with pytest.raises(QuotaError, match="quota_owner_unavailable"):
        h.service.apply(h.user_id, event("credit", 1))
    with transaction(h.engine) as session:
        assert h.service.reconcile(session, h.user_id).consistent


def test_cross_owner_receipt_reference_is_rejected_by_database(ledger: LedgerHarness) -> None:
    h = ledger
    receipt = h.service.apply(h.user_id, event("debit", 1))
    with transaction(h.engine) as session:
        other = User(status="active")
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(IntegrityError) as failure, transaction(h.engine) as session:
        session.add(
            QuotaLedger(
                user_id=other_id,
                operation_id=receipt.operation_id,
                sequence=1,
                kind="credit",
                delta_bytes=1,
                source_type="internal",
                source_id=uuid4(),
                idempotency_key=uuid4(),
                balance_after_bytes=1,
                created_at=datetime.now(UTC),
            )
        )
    assert (
        getattr(getattr(failure.value.orig, "diag", None), "constraint_name", None)
        == "fk_quota_ledger_operation_owner"
    )


def test_reconcile_detects_tampered_prefix_without_repairing(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", 10))
    h.service.apply(h.user_id, event("credit", 10))
    # Explicit forensic corruption by disposable test DB owner, not a runtime repair path.
    with h.engine.begin() as connection:
        connection.execute(text("ALTER TABLE quota_ledger DISABLE TRIGGER quota_ledger_immutable"))
        connection.execute(
            text("UPDATE quota_ledger SET balance_after_bytes = 9 WHERE sequence = 1")
        )
        connection.execute(text("ALTER TABLE quota_ledger ENABLE TRIGGER quota_ledger_immutable"))
    with transaction(h.engine) as session:
        result = h.service.reconcile(session, h.user_id)
        assert not result.consistent and result.violations > 0
        assert (
            session.scalar(select(QuotaLedger.balance_after_bytes).where(QuotaLedger.sequence == 1))
            == 9
        )


def test_reconcile_detects_missing_ledger_and_fast_read_fails_closed(ledger: LedgerHarness) -> None:
    h = ledger
    with transaction(h.engine) as session:
        session.add(
            QuotaOperation(
                user_id=h.user_id,
                idempotency_key=uuid4(),
                kind="credit",
                amount_bytes=1,
                source_type="internal",
                source_id=uuid4(),
                status="applied",
                error_code=None,
                balance_after_bytes=1,
                revision_before=0,
                created_at=datetime.now(UTC),
            )
        )
    with transaction(h.engine) as session:
        assert not h.service.reconcile(session, h.user_id).consistent
    with pytest.raises(QuotaError, match="quota_inconsistent"):
        balance(h)


@pytest.mark.parametrize("rejected", [False, True])
def test_nonempty_quota_downgrade_refuses_all_receipts(
    ledger: LedgerHarness, rejected: bool
) -> None:
    h = ledger
    h.service.apply(h.user_id, event("debit" if rejected else "credit", 1))
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0003_devices")
    reconcile(h)


def test_empty_quota_downgrade_upgrade_and_guards(ledger: LedgerHarness) -> None:
    h = ledger
    config = migration_config(h.engine)
    command.downgrade(config, "0003_devices")
    with h.engine.connect() as connection:
        assert connection.scalar(text("SELECT to_regprocedure('reject_quota_mutation()')")) is None
    command.upgrade(config, "head")
    command.check(config)
    h.service.apply(h.user_id, event("credit", 1))
    with pytest.raises(DBAPIError, match="append only"), h.engine.begin() as connection:
        connection.execute(text("DELETE FROM quota_ledger"))


@pytest.mark.parametrize("corrupt", [False, True])
def test_real_reconcile_cli_reports_counts_and_exit_status(
    ledger: LedgerHarness, corrupt: bool
) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", 10))
    if corrupt:
        with h.engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE quota_ledger DISABLE TRIGGER quota_ledger_immutable")
            )
            connection.execute(text("UPDATE quota_ledger SET balance_after_bytes = 9"))
            connection.execute(
                text("ALTER TABLE quota_ledger ENABLE TRIGGER quota_ledger_immutable")
            )
    env = dict(os.environ)
    env["VPN_DATABASE_URL"] = h.engine.url.render_as_string(False)
    env["VPN_ENABLE_AUTH"] = "false"
    env["VPN_ENVIRONMENT"] = "test"
    result = subprocess.run(  # noqa: S603 - fixed workspace script, no shell, ephemeral DB via env
        [sys.executable, "scripts/quota_reconcile.py"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == (1 if corrupt else 0)
    counts = json.loads(result.stdout)
    assert counts["accounts"] == 1 and counts["inconsistent_accounts"] == int(corrupt)
    assert str(h.user_id) not in result.stdout
    assert h.engine.url.render_as_string(False) not in result.stdout + result.stderr
