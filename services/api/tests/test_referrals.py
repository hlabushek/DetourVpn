"""Real PostgreSQL/API. Telegram and seeded tunnel evidence are TEST SYNTHETIC ONLY."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from httpx2 import Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness, harness, headers, login
from test_migrations import migration_config
from test_mini_app import exchange, payload
from test_operations import binaries, key
from test_quota_api import owner

from vpn_api import operations as ops
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    Device,
    QuotaLedger,
    QuotaOperation,
    Referral,
    ReferralAward,
    ReferralCampaign,
    Tunnel,
    TunnelReservation,
    User,
)
from vpn_api.quota.contracts import MAX_BYTES, LedgerCommand
from vpn_api.quota.service import LedgerService

__all__ = ["harness", "binaries", "key"]
pytestmark = pytest.mark.postgres


def campaign(h: Harness, *, budget: int = 170, cap: int = 10) -> UUID:
    with transaction(h.engine) as session:
        now = datetime.now(UTC)
        row = ReferralCampaign(
            reward_bytes=17,
            budget_bytes=budget,
            inviter_cap=cap,
            enabled=True,
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=1),
        )
        session.add(row)
        session.flush()
        return row.id


def code(h: Harness, auth: dict[str, str]) -> str:
    response = h.client.post("/api/v1/referrals/code", headers=auth)
    assert response.status_code == 200, response.text
    return str(response.json()["code"])


def bind(
    h: Harness, auth: dict[str, str], value: str, cid: UUID, key: UUID | None = None
) -> Response:
    return h.client.post(
        "/api/v1/referrals/resolve",
        headers=auth,
        json={"code": value, "campaign_id": str(cid), "idempotency_key": str(key or uuid4())},
    )


def claim(h: Harness, auth: dict[str, str], rid: UUID, key: UUID | None = None) -> Response:
    return h.client.post(
        "/api/v1/referrals/claim",
        headers=auth,
        json={"referral_id": str(rid), "idempotency_key": str(key or uuid4())},
    )


def evidence(
    h: Harness,
    invitee: UUID,
    *,
    observed: int | None = 100,
    settled: bool = True,
    old: bool = False,
    accounted: bool = True,
) -> None:
    """Seed only an explicit TEST state. Full signed lifecycle integration tested separately."""
    with transaction(h.engine) as session:
        now = datetime.now(UTC)
        device = Device(
            user_id=invitee,
            platform="android",
            key_type="x25519",
            public_key=base64.b64encode(uuid4().bytes + uuid4().bytes).decode(),
            created_at=now,
        )
        session.add(device)
        session.flush()
        tunnel = Tunnel(
            user_id=invitee,
            device_id=device.id,
            idempotency_key=uuid4(),
            state="revoked",
            peer_version=2,
            created_at=now - timedelta(days=1) if old else now,
            revoked_at=now,
        )
        session.add(tunnel)
        session.flush()
        session.add(
            TunnelReservation(
                tunnel_id=tunnel.id,
                user_id=invitee,
                amount_bytes=1000,
                charged_bytes=(1000 if observed is None else observed) if settled else None,
                observed_bytes=observed,
                settled_at=now if settled else None,
            )
        )
        session.flush()
        charged = (1000 if observed is None else observed) if settled else 0
        if charged and accounted:
            ledger = LedgerService(h.engine)
            ledger.apply_in_transaction(
                session,
                invitee,
                LedgerCommand(
                    kind="credit", amount_bytes=charged, source_id=uuid4(), idempotency_key=uuid4()
                ),
            )
            result = ledger.apply_tunnel_settlement_in_transaction(
                session, invitee, charged, tunnel.id
            )
            assert result.status == "applied"


def referral_id(h: Harness, invitee: UUID) -> UUID:
    with transaction(h.engine) as session:
        value = session.scalar(select(Referral.id).where(Referral.invitee_id == invitee))
        assert value is not None
        return value


def pair(h: Harness) -> tuple[dict[str, str], dict[str, str], UUID, UUID]:
    inviter = headers(login(h, 123))
    value = code(h, inviter)
    invitee = headers(login(h, 456))
    cid = campaign(h)
    assert bind(h, invitee, value, cid).status_code == 200
    uid = owner(h, invitee)
    return inviter, invitee, cid, referral_id(h, uid)


def test_self_binding_replay_conflict_and_no_identity_leaks(harness: Harness) -> None:
    h = harness
    inviter = headers(login(h))
    first = code(h, inviter)
    assert code(h, inviter) == first
    cid = campaign(h)
    assert bind(h, inviter, first, cid).json()["error"]["code"] == "referral_self_invitation"
    other = headers(login(h, 222))
    second = code(h, other)
    invitee = headers(login(h, 333))
    key = uuid4()
    assert bind(h, invitee, first, cid, key).json() == {"bound": True}
    assert bind(h, invitee, first, cid, key).json() == {"bound": True}
    assert bind(h, invitee, second, cid, key).json()["error"]["code"] == "referral_binding_conflict"
    overview = h.client.get("/api/v1/referrals", headers=inviter)
    assert overview.headers["cache-control"] == "no-store"
    data = overview.json()
    assert data["invited"] == 1 and data["rewarded"] == 0
    assert set(data["items"][0]) == {"id", "created_at", "rewarded", "reward_bytes"}
    rid = UUID(data["items"][0]["id"])
    assert claim(h, other, rid).status_code == 404
    assert h.client.get(f"/api/v1/referrals?before_id={rid}", headers=other).status_code == 422
    assert h.client.get("/api/v1/referrals", headers=other).json()["invited"] == 0


@pytest.mark.parametrize(
    "reason",
    ["old_account", "younger_inviter", "previous_tunnel", "disabled_inviter", "unverified_inviter"],
)
def test_binding_eligibility(harness: Harness, reason: str) -> None:
    h = harness
    inviter = headers(login(h))
    value = code(h, inviter)
    invitee = headers(login(h, 222))
    inviter_id, invitee_id = owner(h, inviter), owner(h, invitee)
    with transaction(h.engine) as session:
        if reason == "old_account":
            session.execute(
                update(User)
                .where(User.id == invitee_id)
                .values(created_at=datetime.now(UTC) - timedelta(days=8))
            )
        elif reason == "younger_inviter":
            session.execute(
                update(User).where(User.id == inviter_id).values(created_at=datetime.now(UTC))
            )
        elif reason == "disabled_inviter":
            session.execute(update(User).where(User.id == inviter_id).values(status="disabled"))
        elif reason == "unverified_inviter":
            session.execute(update(User).where(User.id == inviter_id).values(verified_at=None))
    if reason == "previous_tunnel":
        evidence(h, invitee_id)
    assert bind(h, invitee, value, campaign(h)).status_code == 409


@pytest.mark.parametrize(
    "kind",
    ["no_tunnel", "zero", "lost", "unsettled", "before_binding", "disabled", "missing_debit"],
)
def test_insufficient_evidence_never_credits(harness: Harness, kind: str) -> None:
    h = harness
    inviter, invitee, _, rid = pair(h)
    uid = owner(h, invitee)
    if kind != "no_tunnel":
        evidence(
            h,
            uid,
            observed=None if kind == "lost" else (0 if kind == "zero" else 100),
            settled=kind != "unsettled",
            old=kind == "before_binding",
            accounted=kind != "missing_debit",
        )
    if kind == "disabled":
        with transaction(h.engine) as session:
            session.execute(update(User).where(User.id == uid).values(status="disabled"))
    assert claim(h, inviter, rid).json()["error"]["code"] == "referral_not_qualified"
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(ReferralAward)) == 0


@pytest.mark.parametrize("same_key", [True, False])
def test_ten_concurrent_claims_one_credit_and_durable_replay(
    harness: Harness, same_key: bool
) -> None:
    h = harness
    inviter, invitee, cid, rid = pair(h)
    evidence(h, owner(h, invitee))
    key = uuid4()
    barrier = Barrier(10, timeout=30)

    def worker(_: int) -> dict[str, object]:
        barrier.wait()
        response = claim(h, inviter, rid, key if same_key else uuid4())
        assert response.status_code == 200, response.text
        return dict(response.json())

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(worker, range(10)))
    assert all(r == results[0] for r in results)
    with transaction(h.engine) as session:
        session.execute(
            update(ReferralCampaign).where(ReferralCampaign.id == cid).values(enabled=False)
        )
        assert session.scalar(select(func.count()).select_from(ReferralAward)) == 1
        assert session.scalar(select(func.sum(QuotaLedger.delta_bytes))) == 17
        assert LedgerService(h.engine).reconcile(session, owner(h, inviter)).consistent
    assert claim(h, inviter, rid).json() == results[0]
    assert (
        h.client.get("/api/v1/quota/history", headers=inviter).json()["items"][0]["source_type"]
        == "reward"
    )


@pytest.mark.parametrize("limit", ["budget", "inviter_cap"])
def test_budget_and_cap_across_parallel_invites(harness: Harness, limit: str) -> None:
    h = harness
    auths = [headers(login(h, 100)), headers(login(h, 200))]
    codes = [code(h, a) for a in auths]
    if limit == "inviter_cap":
        auths[1] = auths[0]
        codes[1] = codes[0]
    cid = campaign(
        h, budget=17 if limit == "budget" else 170, cap=1 if limit == "inviter_cap" else 10
    )
    ids = []
    for subject, value in zip([300, 400], codes, strict=True):
        invitee = headers(login(h, subject))
        assert bind(h, invitee, value, cid).status_code == 200
        uid = owner(h, invitee)
        evidence(h, uid)
        ids.append(referral_id(h, uid))
    barrier = Barrier(2, timeout=30)

    def worker(i: int) -> Response:
        barrier.wait()
        return claim(h, auths[i], ids[i])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [0, 1]))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert [r.json()["error"]["code"] for r in results if r.status_code == 409] == [
        "referral_budget_exhausted" if limit == "budget" else "referral_inviter_limit"
    ]
    with transaction(h.engine) as session:
        assert session.scalar(select(func.sum(ReferralAward.reward_bytes))) == 17


def test_claim_rollback_on_capacity_and_key_conflict(harness: Harness) -> None:
    h = harness
    inviter, invitee, _, rid = pair(h)
    evidence(h, owner(h, invitee))
    ledger = LedgerService(h.engine)
    uid, key = owner(h, inviter), uuid4()
    ledger.apply(
        uid,
        LedgerCommand(
            kind="credit", amount_bytes=MAX_BYTES, source_id=uuid4(), idempotency_key=key
        ),
    )
    assert claim(h, inviter, rid, key).json()["error"]["code"] == "quota_idempotency_conflict"
    assert claim(h, inviter, rid).json()["error"]["code"] == "quota_capacity_exceeded"
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(ReferralAward)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_type == "reward")
            )
            == 0
        )
        assert ledger.reconcile(session, uid).consistent


def test_disabled_expired_campaign_and_reward_snapshot(harness: Harness) -> None:
    h = harness
    inviter, invitee, cid, rid = pair(h)
    evidence(h, owner(h, invitee))
    for expired in (False, True):
        with transaction(h.engine) as session:
            session.execute(
                update(ReferralCampaign)
                .where(ReferralCampaign.id == cid)
                .values(
                    enabled=expired,
                    ends_at=datetime.now(UTC) - timedelta(seconds=1)
                    if expired
                    else datetime.now(UTC) + timedelta(days=1),
                )
            )
        assert claim(h, inviter, rid).json()["error"]["code"] == "referral_campaign_unavailable"
    with transaction(h.engine) as session:
        session.execute(
            update(ReferralCampaign)
            .where(ReferralCampaign.id == cid)
            .values(ends_at=datetime.now(UTC) + timedelta(days=1), reward_bytes=19)
        )
    assert claim(h, inviter, rid).json()["reward_bytes"] == 17


def test_database_constraints_immutability_and_safe_downgrade(harness: Harness) -> None:
    h = harness
    inviter, invitee, _, rid = pair(h)
    evidence(h, owner(h, invitee))
    assert claim(h, inviter, rid).status_code == 200
    for model in (Referral, ReferralAward):
        with pytest.raises(DBAPIError, match="append only"):
            with transaction(h.engine) as session:
                session.execute(delete(model))
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0010_mini_app")
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(ReferralAward)) == 1


def test_mini_app_permission_validation_and_revocation(harness: Harness) -> None:
    h = harness
    for path in ("", "/code", "/resolve", "/claim"):
        response = (
            h.client.get("/api/v1/referrals")
            if not path
            else h.client.post("/api/v1/referrals" + path, json={})
        )
        assert response.status_code in (401, 422)
    auth = headers(exchange(h, payload(h)))
    assert h.client.get("/api/v1/referrals", headers=auth).json()["campaigns"] == []
    assert len(code(h, auth)) == 24
    response = h.client.post(
        "/api/v1/referrals/resolve",
        headers=auth,
        json={"code": "private-do-not-echo", "qualified": True},
    )
    assert response.status_code == 422 and "private-do-not-echo" not in response.text
    assert h.client.post("/api/v1/auth/logout", headers=auth).status_code == 204
    assert h.client.get("/api/v1/referrals", headers=auth).status_code == 401


def test_concurrent_binding_cannot_switch_inviter(harness: Harness) -> None:
    h = harness
    inviters = [headers(login(h, 100)), headers(login(h, 200))]
    codes = [code(h, a) for a in inviters]
    invitee = headers(login(h, 300))
    cid = campaign(h)
    barrier = Barrier(2, timeout=30)

    def worker(value: str) -> Response:
        barrier.wait()
        return bind(h, invitee, value, cid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(worker, codes))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(Referral)) == 1


def test_pagination_and_receipt_key_conflict(harness: Harness) -> None:
    h = harness
    auth = headers(login(h))
    uid, cid = owner(h, auth), campaign(h, budget=10000, cap=200)
    # Synthetic accounts avoid 102 Telegram logins; API pagination and ledger are real.
    with transaction(h.engine) as session:
        now = datetime.now(UTC)
        for _ in range(102):
            user = User(status="active", verified_at=now)
            session.add(user)
            session.flush()
            session.add(
                Referral(
                    inviter_id=uid,
                    invitee_id=user.id,
                    campaign_id=cid,
                    idempotency_key=uuid4(),
                    reward_bytes=17,
                    created_at=now,
                )
            )
    first = h.client.get("/api/v1/referrals", headers=auth).json()
    assert first["invited"] == 102 and len(first["items"]) == 100
    second = h.client.get(
        f"/api/v1/referrals?before_id={first['next_before_id']}", headers=auth
    ).json()
    assert len(second["items"]) == 2 and second["next_before_id"] is None
    ids = [r["id"] for r in first["items"] + second["items"]]
    assert len(set(ids)) == 102
    rids = [UUID(value) for value in ids[:2]]
    with transaction(h.engine) as session:
        invitees = [session.get(Referral, rid) for rid in rids]
        uids = [r.invitee_id for r in invitees if r is not None]
    for invitee in uids:
        evidence(h, invitee)
    key = uuid4()
    assert claim(h, auth, rids[0], key).status_code == 200
    assert claim(h, auth, rids[1], key).json()["error"]["code"] == "referral_idempotency_conflict"


def test_referral_accounting_survives_restore(
    harness: Harness, key: Path, binaries: Path, tmp_path: Path
) -> None:
    h = harness
    inviter, invitee, _, rid = pair(h)
    evidence(h, owner(h, invitee))
    assert claim(h, inviter, rid).status_code == 200
    archive = tmp_path / "referrals.fernet"
    manifest = ops.make_backup(h.engine, binaries, archive, key)
    assert manifest.tables["referrals"].rows == 1
    assert manifest.tables["referral_awards"].rows == 1
    assert ops.restore_drill(h.engine, binaries, archive, key)["accounts_reconciled"] == 2
