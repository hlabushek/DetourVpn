"""Real PostgreSQL policy/accounting. Dates are synthetic; no live VPN/payment evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from typing import Literal
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness, harness, headers, login
from test_migrations import migration_config
from test_operations import binaries, key
from test_quota import LedgerHarness, event, ledger
from test_tunnels import setup_request

from vpn_api import operations as ops
from vpn_api import subscriptions as module
from vpn_api.auth.security import AuthError
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    PlanRevision,
    QuotaFundingLot,
    QuotaFundingUse,
    Subscription,
    SubscriptionRevocation,
)
from vpn_api.quota.contracts import QuotaError
from vpn_api.quota.service import LedgerService
from vpn_api.subscriptions import EntitlementService, PlanInput
from vpn_api.tunnels.service import request_tunnel

__all__ = ["harness", "ledger", "binaries", "key"]


def plan(
    h: LedgerHarness,
    *,
    quota: int = 1000,
    kind: Literal["subscription", "addon"] = "subscription",
) -> UUID:
    with transaction(h.engine) as session:
        row = EntitlementService(h.engine).revise_plan(
            session,
            PlanInput(
                code="start" if kind == "subscription" else "extra",
                name="Start",
                kind=kind,
                price_minor=9900,
                quota_bytes=quota,
                period_days=30,
            ),
            uuid4(),
            actor="test-operator",
            reason="Synthetic test catalog",
        )
        return row.id


def grant(h: LedgerHarness, plan_id: UUID, key: UUID | None = None) -> UUID:
    with transaction(h.engine) as session:
        row = EntitlementService(h.engine).grant(
            session,
            h.user_id,
            plan_id,
            key or uuid4(),
            actor="test-operator",
            reason="Free closed test grant",
        )
        return row.id


def test_plan_revision_does_not_rewrite_existing_grant(ledger: LedgerHarness) -> None:
    h = ledger
    old = plan(h)
    subscription_id = grant(h, old)
    new = plan(h, quota=3000)
    with transaction(h.engine) as session:
        subscription = session.get(Subscription, subscription_id)
        assert subscription is not None
        view = EntitlementService.view(session, subscription)
        assert view.plan.id == old and view.plan.quota_bytes == 1000 and view.plan.version == 1
        revision = session.get(PlanRevision, new)
        assert revision is not None and revision.version == 2
        assert not view.plan.payments_enabled and view.basis == "admin_grant"


def test_explicit_demo_catalog_uses_exact_minor_units_and_gib(ledger: LedgerHarness) -> None:
    h = ledger
    for _ in range(2):
        with transaction(h.engine) as session:
            EntitlementService(h.engine).seed_demonstration_catalog(session, actor="test-operator")
    with transaction(h.engine) as session:
        values = {
            row.code: (row.price_minor, row.quota_bytes, row.period_days)
            for row in session.scalars(select(PlanRevision))
        }
        assert values == {
            "start": (9900, 107374182400, 30),
            "plus": (19900, 322122547200, 30),
            "extra": (2900, 10737418240, 30),
        }


def test_ten_parallel_grant_retries_credit_once(ledger: LedgerHarness) -> None:
    h = ledger
    plan_id, key = plan(h), uuid4()
    barrier = Barrier(10, timeout=20)

    def run(_: int) -> UUID:
        barrier.wait()
        return grant(h, plan_id, key)

    with ThreadPoolExecutor(max_workers=10) as pool:
        ids = list(pool.map(run, range(10)))
    assert len(set(ids)) == 1
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(Subscription)) == 1
        assert h.service.quota(session, h.user_id).available_bytes == 1000
        assert h.service.reconcile(session, h.user_id).consistent


def test_expiring_before_free_and_no_double_spend(ledger: LedgerHarness) -> None:
    h = ledger
    h.service.apply(h.user_id, event("credit", 2000))
    subscription_id = grant(h, plan(h))
    spend = event("debit", 1200)
    first = h.service.apply(h.user_id, spend)
    assert h.service.apply(h.user_id, spend) == first
    with transaction(h.engine) as session:
        subscription = session.get(Subscription, subscription_id)
        assert subscription is not None
        lot = session.get(QuotaFundingLot, subscription.funding_id)
        assert lot is not None and lot.remaining_bytes == 0
        assert session.scalar(select(func.count()).select_from(QuotaFundingUse)) == 2
        assert h.service.quota(session, h.user_id).available_bytes == 1800
        assert h.service.reconcile(session, h.user_id).consistent


def test_expired_quota_is_unavailable_without_fake_usage(
    ledger: LedgerHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = ledger
    plan_id = plan(h)
    with transaction(h.engine) as session:
        past = database_now(session) - timedelta(days=31)
    with monkeypatch.context() as patch:
        patch.setattr(module, "database_now", lambda _: past)
        subscription_id = grant(h, plan_id)
    with transaction(h.engine) as session:
        quota = h.service.quota(session, h.user_id)
        assert quota.available_bytes == 0 and quota.unavailable_bytes == 1000
        assert quota.used_bytes == 0 and quota.revision == 1
        subscription = session.get(Subscription, subscription_id)
        assert subscription is not None
        assert EntitlementService.view(session, subscription).state == "expired"
    assert h.service.apply(h.user_id, event("debit", 1)).status == "rejected"
    h.service.apply(h.user_id, event("credit", 50))
    assert h.service.apply(h.user_id, event("debit", 20)).status == "applied"
    with transaction(h.engine) as session:
        assert h.service.quota(session, h.user_id).available_bytes == 30
        assert h.service.reconcile(session, h.user_id).consistent


def test_renewal_is_scheduled_and_addon_expires_with_current_period(ledger: LedgerHarness) -> None:
    h = ledger
    plan_id = plan(h)
    first_id = grant(h, plan_id)
    next_id = grant(h, plan_id)
    addon_id = grant(h, plan(h, quota=100, kind="addon"))
    with transaction(h.engine) as session:
        first, renewal, addon = (
            session.get(Subscription, id_) for id_ in (first_id, next_id, addon_id)
        )
        assert first is not None and renewal is not None and addon is not None
        assert first.ends_at == renewal.starts_at
        assert addon.ends_at == first.ends_at
        assert EntitlementService.view(session, renewal).state == "scheduled"
        quota = h.service.quota(session, h.user_id)
        assert quota.available_bytes == 1100 and quota.unavailable_bytes == 1000


def test_addon_without_active_subscription_is_rejected(ledger: LedgerHarness) -> None:
    plan_id = plan(ledger, kind="addon")
    with pytest.raises(QuotaError, match="active_subscription_required"):
        grant(ledger, plan_id)


def test_revoke_is_audited_and_repeat_does_not_change_spend(ledger: LedgerHarness) -> None:
    h = ledger
    subscription_id = grant(h, plan(h))
    h.service.apply(h.user_id, event("debit", 300))
    key = uuid4()
    for _ in range(2):
        with transaction(h.engine) as session:
            EntitlementService(h.engine).revoke(
                session,
                h.user_id,
                subscription_id,
                key,
                actor="test-operator",
                reason="Revoke test grant",
            )
    with transaction(h.engine) as session:
        quota = h.service.quota(session, h.user_id)
        assert quota.used_bytes == 300 and quota.unavailable_bytes == 700
        assert quota.available_bytes == 0
        assert session.scalar(select(func.count()).select_from(SubscriptionRevocation)) == 1
        assert h.service.reconcile(session, h.user_id).consistent
    with pytest.raises(QuotaError, match="subscription_revocation_conflict"):
        with transaction(h.engine) as session:
            EntitlementService(h.engine).revoke(
                session,
                h.user_id,
                subscription_id,
                uuid4(),
                actor="test-operator",
                reason="Different request",
            )


def test_grant_conflict_and_missing_audit_fail_closed(ledger: LedgerHarness) -> None:
    h = ledger
    first, second, key = plan(h), plan(h, quota=2000), uuid4()
    grant(h, first, key)
    with pytest.raises(QuotaError, match="subscription_idempotency_conflict"):
        grant(h, second, key)
    with pytest.raises(QuotaError, match="audit_context_required"):
        with transaction(h.engine) as session:
            EntitlementService(h.engine).grant(
                session, h.user_id, first, uuid4(), actor="test-operator", reason=""
            )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE plan_revisions SET quota_bytes = quota_bytes + 1",
        "DELETE FROM subscriptions",
        "UPDATE quota_funding_lots SET expires_at = expires_at + interval '1 day'",
        "UPDATE quota_funding_lots SET remaining_bytes = granted_bytes + 1",
        "TRUNCATE quota_funding_uses",
    ],
)
def test_funding_and_terms_cannot_be_rewritten(ledger: LedgerHarness, statement: str) -> None:
    grant(ledger, plan(ledger))
    with pytest.raises(DBAPIError), ledger.engine.begin() as connection:
        connection.execute(text(statement))


def test_read_api_is_owner_scoped_and_has_no_purchase_or_write_route(harness: Harness) -> None:
    h = harness
    first, second = login(h, 11111), login(h, 22222)
    owner = UUID(h.client.get("/api/v1/me", headers=headers(first)).json()["id"])
    local = LedgerHarness(h.engine, owner, LedgerService(h.engine))
    subscription_id = grant(local, plan(local))
    rows = h.client.get("/api/v1/subscriptions", headers=headers(first)).json()
    assert rows[0]["id"] == str(subscription_id)
    assert "actor" not in rows[0] and "reason" not in rows[0]
    assert h.client.get("/api/v1/subscriptions", headers=headers(second)).json() == []
    assert h.client.get("/api/v1/subscriptions").status_code == 401
    assert (
        h.client.post("/api/v1/subscriptions", headers=headers(first), json={}).status_code == 405
    )
    assert h.client.post("/api/v1/payments", headers=headers(first), json={}).status_code == 404


def test_legacy_lease_and_entitlements_cannot_be_mixed(harness: Harness) -> None:
    h = harness
    tokens, body = setup_request(h)
    user_id = UUID(h.client.get("/api/v1/me", headers=headers(tokens)).json()["id"])
    local = LedgerHarness(h.engine, user_id, LedgerService(h.engine))
    plan_id = plan(local)
    tunnel = request_tunnel(h.auth, tokens.access_token, body)
    with pytest.raises(QuotaError, match="subscription_legacy_tunnel_active"):
        grant(local, plan_id)
    response = h.client.delete(f"/api/v1/tunnels/{tunnel.id}", headers=headers(tokens))
    assert response.status_code == 200
    grant(local, plan_id)
    body.idempotency_key = uuid4()
    with pytest.raises(AuthError, match="entitlement_requires_peer_protocol_v3"):
        request_tunnel(h.auth, tokens.access_token, body)


def test_existing_ledger_backfill_preserves_funding_and_history(engine: Engine) -> None:
    config = migration_config(engine)
    command.upgrade(config, "0011_referrals")
    user_id = uuid4()
    operations = [uuid4() for _ in range(3)]
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id,status) VALUES (:id,'active')"), {"id": user_id}
        )
        for sequence, (kind, amount, balance) in enumerate(
            (("credit", 100, 100), ("credit", 100, 200), ("debit", 150, 50)), start=1
        ):
            values = dict(
                id=operations[sequence - 1],
                owner=user_id,
                key=uuid4(),
                source=uuid4(),
                kind=kind,
                amount=amount,
                balance=balance,
                sequence=sequence,
                delta=amount if kind == "credit" else -amount,
                entry=uuid4(),
            )
            connection.execute(
                text("""INSERT INTO quota_operations
                (id,user_id,idempotency_key,kind,amount_bytes,source_type,source_id,status,
                 balance_after_bytes,revision_before,reserved_before_bytes,created_at)
                VALUES (:id,:owner,:key,:kind,:amount,'internal',:source,'applied',
                        :balance,:sequence-1,0,clock_timestamp())"""),
                values,
            )
            connection.execute(
                text("""INSERT INTO quota_ledger
                (id,user_id,operation_id,sequence,kind,delta_bytes,source_type,source_id,
                 idempotency_key,balance_after_bytes,created_at)
                VALUES (:entry,:owner,:id,:sequence,:kind,:delta,'internal',:source,
                        :key,:balance,clock_timestamp())"""),
                values,
            )
    command.upgrade(config, "head")
    with transaction(engine) as session:
        for operation_id, expected in zip(operations[:2], (0, 50), strict=True):
            lot = session.get(QuotaFundingLot, operation_id)
            assert lot is not None
            assert lot.remaining_bytes == lot.initial_remaining_bytes == expected
        assert LedgerService(engine).reconcile(session, user_id).consistent

    assert LedgerService(engine).apply(user_id, event("debit", 20)).status == "applied"
    with transaction(engine) as session:
        assert LedgerService(engine).quota(session, user_id).available_bytes == 30
        assert LedgerService(engine).reconcile(session, user_id).consistent


def test_subscription_restore_keeps_allocations_and_revocation(
    ledger: LedgerHarness,
    key: Path,
    binaries: Path,
    tmp_path: Path,
) -> None:
    h = ledger
    subscription_id = grant(h, plan(h))
    h.service.apply(h.user_id, event("debit", 300))
    with transaction(h.engine) as session:
        EntitlementService(h.engine).revoke(
            session,
            h.user_id,
            subscription_id,
            uuid4(),
            actor="test-operator",
            reason="Restore test revocation",
        )
    archive = tmp_path / "subscriptions.fernet"
    backup = ops.make_backup(h.engine, binaries, archive, key)
    assert backup.tables["subscriptions"].rows == 1
    assert backup.tables["quota_funding_uses"].rows == 1
    assert backup.tables["subscription_revocations"].rows == 1
    result = ops.restore_drill(h.engine, binaries, archive, key)
    assert result["accounts_reconciled"] == 1
