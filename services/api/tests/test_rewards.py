"""Real PostgreSQL/API. Synthetic reward configuration; Telegram TEST FAKE ONLY."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count
from threading import Barrier
from typing import Literal
from uuid import UUID, uuid4

import pytest
from alembic import command
from httpx2 import Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness, harness, headers, login
from test_migrations import migration_config
from test_quota_api import owner

from vpn_api.auth import service as auth_module
from vpn_api.db.connection import transaction
from vpn_api.db.models import QuotaLedger, QuotaOperation, RewardClaim, RewardRule
from vpn_api.quota.contracts import MAX_BYTES, LedgerCommand
from vpn_api.quota.service import LedgerService
from vpn_api.rewards import service as reward_module
from vpn_api.rewards.contracts import ClaimInput
from vpn_api.rewards.service import RewardService

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def rule(h: Harness, kind: Literal["daily", "clicker"] = "daily", *, cap: int = 1) -> UUID:
    with transaction(h.engine) as session:
        now = datetime.now(UTC)
        row = RewardRule(
            reward_type=kind,
            reward_bytes=17,
            daily_cap_bytes=17 * cap,
            daily_cap_claims=cap,
            cooldown_seconds=1,
            enabled=True,
            starts_at=now - timedelta(days=10),
            ends_at=now + timedelta(days=10),
        )
        session.add(row)
        session.flush()
        return row.id


def post(h: Harness, auth: dict[str, str], rule_id: UUID, key: UUID | None = None) -> Response:
    return h.client.post(
        "/api/v1/rewards/claim",
        headers=auth,
        json={"rule_id": str(rule_id), "idempotency_key": str(key or uuid4())},
    )


def test_daily_replay_history_and_account_isolation(harness: Harness) -> None:
    h = harness
    auth, other = headers(login(h)), headers(login(h, 222))
    rid, key = rule(h), uuid4()
    first = post(h, auth, rid, key)
    assert first.status_code == 200
    assert post(h, auth, rid, key).json() == first.json()
    assert post(h, auth, rid).json()["error"]["code"] == "reward_limit_reached"
    assert post(h, other, rid, key).status_code == 200
    history = h.client.get("/api/v1/quota/history", headers=auth).json()["items"]
    assert history[0]["source_type"] == "reward" and history[0]["delta_bytes"] == 17
    assert h.client.get("/api/v1/quota", headers=auth).json()["available_bytes"] == 17
    with transaction(h.engine) as session:
        assert LedgerService(h.engine).reconcile(session, owner(h, auth)).consistent


@pytest.mark.parametrize("same_key", [True, False])
def test_ten_parallel_claims_credit_exactly_once(harness: Harness, same_key: bool) -> None:
    h = harness
    auth = headers(login(h))
    rid, key = rule(h), uuid4()
    barrier = Barrier(10, timeout=30)

    def worker(_: int) -> tuple[int, dict[str, object]]:
        barrier.wait()
        response = post(h, auth, rid, key if same_key else uuid4())
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(worker, range(10)))
    assert sum(status == 200 for status, _ in results) == (10 if same_key else 1)
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(RewardClaim)) == 1
        assert session.scalar(select(func.sum(QuotaLedger.delta_bytes))) == 17


def test_cooldown_cap_and_utc_rollover(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness
    auth = headers(login(h))
    rid = rule(h, "clicker", cap=2)
    now = datetime.now(UTC).replace(hour=23, minute=59, second=58, microsecond=0)
    # Only reward clock is controlled; SQL transactions/ledger remain real.
    monkeypatch.setattr(reward_module, "database_now", lambda _: now)
    key = uuid4()
    assert post(h, auth, rid, key).status_code == 200
    assert post(h, auth, rid).json()["error"]["code"] == "reward_cooldown"
    now += timedelta(seconds=1)
    assert post(h, auth, rid).status_code == 200
    assert post(h, auth, rid).json()["error"]["code"] == "reward_limit_reached"
    now += timedelta(seconds=1)
    assert post(h, auth, rid).status_code == 200
    assert post(h, auth, rid, key).json()["reward_bytes"] == 17


def test_daily_rollover_and_old_receipt_survives_disabled_config(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    auth = headers(login(h))
    rid, key = rule(h), uuid4()
    now = datetime.now(UTC).replace(hour=23, minute=59, second=58, microsecond=0)
    monkeypatch.setattr(reward_module, "database_now", lambda _: now)
    first = post(h, auth, rid, key).json()
    now += timedelta(seconds=3)
    assert post(h, auth, rid).status_code == 200
    with transaction(h.engine) as session:
        row = session.get(RewardRule, rid)
        assert row is not None
        row.enabled = False
        row.reward_bytes = 19
        row.daily_cap_bytes = 19
    assert post(h, auth, rid, key).json() == first
    assert post(h, auth, rid).json()["error"]["code"] == "reward_unavailable"


def test_byte_cap_config_change_and_pending_cooldown(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    auth = headers(login(h))
    rid = rule(h, "clicker", cap=10)
    now = datetime.now(UTC)
    monkeypatch.setattr(reward_module, "database_now", lambda _: now)
    assert post(h, auth, rid).status_code == 200
    with transaction(h.engine) as session:
        row = session.get(RewardRule, rid)
        assert row is not None
        row.daily_cap_bytes = 20
    now += timedelta(seconds=2)
    assert post(h, auth, rid).json()["error"]["code"] == "reward_limit_reached"
    view = h.client.get("/api/v1/rewards/rules", headers=auth).json()["items"][0]
    assert view["remaining_bytes"] == 3 and not view["eligible"]


def test_validation_auth_conflict_window_and_revocation(harness: Harness) -> None:
    h = harness
    auth = headers(login(h))
    rid, other, key = rule(h), rule(h, "clicker"), uuid4()
    assert h.client.get("/api/v1/rewards/rules").status_code == 401
    assert post(h, {}, rid).status_code == 401
    assert (
        h.client.post(
            "/api/v1/rewards/claim",
            headers=auth,
            json={"rule_id": str(rid), "idempotency_key": str(key), "reward_bytes": 999},
        ).status_code
        == 422
    )
    assert post(h, auth, uuid4()).json()["error"]["code"] == "reward_unavailable"
    assert post(h, auth, rid, key).status_code == 200
    assert post(h, auth, other, key).json()["error"]["code"] == "reward_idempotency_conflict"
    with transaction(h.engine) as session:
        row = session.get(RewardRule, other)
        assert row is not None
        row.starts_at = datetime.now(UTC) + timedelta(days=1)
    assert post(h, auth, other).json()["error"]["code"] == "reward_unavailable"
    assert h.client.post("/api/v1/auth/logout", headers=auth).status_code == 204
    assert post(h, auth, rid, key).status_code == 401


def test_atomic_rollback_and_safe_downgrade(harness: Harness) -> None:
    h = harness
    auth = headers(login(h))
    user_id, rid = owner(h, auth), rule(h)
    with pytest.raises(RuntimeError, match="injected"):
        with transaction(h.engine) as session:
            RewardService(h.engine).claim(
                session, user_id, ClaimInput(rule_id=rid, idempotency_key=uuid4())
            )
            raise RuntimeError("injected after claim")
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(RewardClaim)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_type == "reward")
            )
            == 0
        )
    assert post(h, auth, rid).status_code == 200
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0008_tunnel_admission")
    with pytest.raises(DBAPIError, match="append only"):
        with transaction(h.engine) as session:
            session.execute(text("DELETE FROM reward_claims"))


def test_rate_limit_failed_claims_are_counted(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    auth = headers(login(h))
    # Fixed test clock: 121 requests must exercise one minute bucket, even at rollover.
    now = datetime.now(UTC)
    monkeypatch.setattr(auth_module, "database_now", lambda _: now)
    for _ in range(120):
        assert post(h, auth, uuid4()).status_code == 409
    assert post(h, auth, uuid4()).status_code == 429


def test_empty_rules_default(harness: Harness) -> None:
    response = harness.client.get("/api/v1/rewards/rules", headers=headers(login(harness)))
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["timezone"] == "UTC"


@pytest.mark.parametrize("byte_cap", [False, True])
def test_concurrent_clicker_caps(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, byte_cap: bool
) -> None:
    h = harness
    auth = headers(login(h))
    rid = rule(h, "clicker", cap=3)
    if byte_cap:
        with transaction(h.engine) as session:
            row = session.get(RewardRule, rid)
            assert row is not None
            row.daily_cap_claims = 10
    # TEST-ONLY advancing server clock: allows cooldown to pass between locked
    # claims so this test exercises daily count/byte caps under concurrency.
    base = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    ticks = count()
    monkeypatch.setattr(
        reward_module, "database_now", lambda _: base + timedelta(seconds=next(ticks))
    )
    barrier = Barrier(10, timeout=30)

    def worker(_: int) -> int:
        barrier.wait()
        return post(h, auth, rid).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(worker, range(10)))
    assert statuses.count(200) == 3
    assert statuses.count(409) == 7
    assert h.client.get("/api/v1/quota", headers=auth).json()["available_bytes"] == 51


def test_reward_overflow_does_not_create_partial_accounting(harness: Harness) -> None:
    h = harness
    auth = headers(login(h))
    uid, rid = owner(h, auth), rule(h)
    LedgerService(h.engine).apply(
        uid,
        LedgerCommand(
            kind="credit", amount_bytes=MAX_BYTES - 16, source_id=uuid4(), idempotency_key=uuid4()
        ),
    )
    assert post(h, auth, rid).json()["error"]["code"] == "quota_capacity_exceeded"
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(RewardClaim)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(QuotaOperation)
                .where(QuotaOperation.source_type == "reward")
            )
            == 0
        )
        assert LedgerService(h.engine).reconcile(session, uid).consistent
