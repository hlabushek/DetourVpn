"""Authenticated real-DB quota API; Telegram delivery is the shared explicit test fake."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from test_authentication import Harness, begin, harness, headers, issue, login
from test_quota import event

from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    AuthIdentity,
    LoginAttempt,
    QuotaLedger,
    QuotaOperation,
    SessionFamily,
    User,
)
from vpn_api.quota.service import LedgerService

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def owner(h: Harness, authorization: dict[str, str]) -> UUID:
    return UUID(h.client.get("/api/v1/me", headers=authorization).json()["id"])


def test_login_applies_configured_quota_once(harness: Harness) -> None:
    h = harness
    h.settings.initial_quota_bytes = 17
    first = login(h)
    quota = h.client.get("/api/v1/quota", headers=headers(first))
    assert quota.status_code == 200
    assert quota.json() == {
        "granted_bytes": 17,
        "used_bytes": 0,
        "available_bytes": 17,
        "reserved_bytes": 0,
        "revision": 1,
        "initial_grant_applied": True,
    }
    assert quota.headers["cache-control"] == "no-store"
    h.settings.initial_quota_bytes = 99
    second = login(h)
    assert h.client.get("/api/v1/quota", headers=headers(second)).json() == quota.json()
    history = h.client.get("/api/v1/quota/history", headers=headers(second)).json()
    assert len(history["items"]) == 1
    assert history["items"][0]["source_type"] == "registration"
    assert history["items"][0]["delta_bytes"] == 17


def test_concurrent_logins_do_not_duplicate_initial_credit(harness: Harness) -> None:
    h = harness
    h.settings.initial_quota_bytes = 17
    starts = [begin(h), begin(h)]
    codes = [issue(h, start) for start in starts]
    barrier = Barrier(2, timeout=15)

    def verify(index: int) -> None:
        barrier.wait()
        h.auth.verify(
            starts[index].request_id, starts[index].verifier, codes[index], "quota-login-race"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(verify, range(2)))
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperation)) == 1
        assert session.scalar(select(func.sum(QuotaLedger.delta_bytes))) == 17


def test_zero_default_registration_is_a_receipt(harness: Harness) -> None:
    h = harness
    first = login(h)
    assert h.client.get("/api/v1/quota", headers=headers(first)).json()["available_bytes"] == 0
    h.settings.initial_quota_bytes = 17
    second = login(h)
    quota = h.client.get("/api/v1/quota", headers=headers(second)).json()
    assert quota["initial_grant_applied"] and quota["revision"] == 1
    assert quota["granted_bytes"] == 0


def test_quota_and_history_are_owner_scoped(harness: Harness) -> None:
    h = harness
    first, other = login(h, 111), login(h, 222)
    first_id, other_id = owner(h, headers(first)), owner(h, headers(other))
    command = event("credit", 27)
    LedgerService(h.engine).apply(first_id, command)
    for path in ("/api/v1/quota", "/api/v1/quota/history"):
        assert h.client.get(path).status_code == 401
    # Caller-provided selectors cannot change the owner selected by the bearer.
    response = h.client.get(f"/api/v1/quota?user_id={first_id}", headers=headers(other))
    assert response.json()["available_bytes"] == 0
    own = h.client.get(f"/api/v1/quota/history?user_id={first_id}", headers=headers(other)).json()
    assert all(entry["source_id"] == str(other_id) for entry in own["items"])
    assert str(command.source_id) not in str(own)
    assert h.client.post("/api/v1/auth/logout", headers=headers(other)).status_code == 204
    assert h.client.get("/api/v1/quota", headers=headers(other)).status_code == 401


def test_history_snapshot_is_stable_during_new_credit(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    user_id = owner(h, headers(tokens))
    service = LedgerService(h.engine)
    for _ in range(3):
        service.apply(user_id, event("credit", 1))
    page = h.client.get("/api/v1/quota/history?limit=2", headers=headers(tokens)).json()
    assert [row["sequence"] for row in page["items"]] == [4, 3]
    assert page["snapshot_revision"] == 4 and page["next_before_sequence"] == 3
    service.apply(user_id, event("credit", 1))
    remaining = h.client.get(
        "/api/v1/quota/history",
        params={
            "limit": 2,
            "before_sequence": page["next_before_sequence"],
            "snapshot_revision": page["snapshot_revision"],
        },
        headers=headers(tokens),
    ).json()
    assert [row["sequence"] for row in remaining["items"]] == [2, 1]
    assert remaining["next_before_sequence"] is None
    assert remaining["snapshot_revision"] == 4
    assert h.client.get("/api/v1/quota", headers=headers(tokens)).json()["revision"] == 5


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "before_sequence=0",
        "snapshot_revision=-1",
        "snapshot_revision=999",
        "before_sequence=999&snapshot_revision=1",
    ],
)
def test_invalid_history_cursor_has_structured_error(harness: Harness, query: str) -> None:
    h = harness
    tokens = login(h)
    response = h.client.get("/api/v1/quota/history?" + query, headers=headers(tokens))
    assert response.status_code == 422
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_public_caller_cannot_credit_debit_or_use_admin_role(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    for path in ("/api/v1/quota", "/api/v1/quota/credit", "/api/v1/quota/debit"):
        response = h.client.post(path, headers=headers(tokens), json={"amount_bytes": 100})
        assert response.status_code in (404, 405)
    with transaction(h.engine) as session:
        session.execute(text("UPDATE users SET role = 'admin'"))
    assert (
        h.client.post(
            "/api/v1/quota/credit", headers=headers(tokens), json={"amount_bytes": 100}
        ).status_code
        == 404
    )
    assert h.client.get("/api/v1/quota", headers=headers(tokens)).json()["available_bytes"] == 0


def test_corrupt_head_fails_closed_without_sql_or_subject_in_error(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    # Explicit test-only forensic tampering by owner of this disposable database.
    with h.engine.begin() as connection:
        connection.execute(text("ALTER TABLE quota_ledger DISABLE TRIGGER quota_ledger_immutable"))
        connection.execute(text("UPDATE quota_ledger SET balance_after_bytes = 9"))
        connection.execute(text("ALTER TABLE quota_ledger ENABLE TRIGGER quota_ledger_immutable"))
    response = h.client.get("/api/v1/quota", headers=headers(tokens))
    assert response.status_code == 503 and response.json()["error"]["code"] == "quota_inconsistent"
    assert "quota_ledger" not in response.text and str(2**40) not in response.text


def test_get_refresh_and_device_reads_do_not_add_credits(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    for _ in range(2):
        assert h.client.get("/api/v1/quota", headers=headers(tokens)).status_code == 200
        assert h.client.get("/api/v1/quota/history", headers=headers(tokens)).status_code == 200
        assert h.client.get("/api/v1/devices", headers=headers(tokens)).status_code == 200
    h.auth.refresh(tokens.refresh_token, "quota-refresh")
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(QuotaOperation)) == 1
        assert session.scalar(select(func.sum(QuotaLedger.delta_bytes))) == 0


def test_api_preserves_integer_bytes_above_float_precision(harness: Harness) -> None:
    h = harness
    amount = 2**53 + 17
    h.settings.initial_quota_bytes = amount
    tokens = login(h)
    response = h.client.get("/api/v1/quota", headers=headers(tokens))
    assert response.json()["available_bytes"] == amount
    assert str(amount) in response.text


def test_session_failure_rolls_back_initial_grant_and_allows_safe_retry(harness: Harness) -> None:
    h = harness
    h.settings.initial_quota_bytes = 17
    start = begin(h)
    code = issue(h, start)
    with h.engine.begin() as connection:
        connection.execute(
            text("""CREATE FUNCTION synthetic_session_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic session abort'; END; $$;""")
        )
        connection.execute(
            text("""CREATE TRIGGER synthetic_failure BEFORE INSERT ON session_credentials
            FOR EACH ROW EXECUTE FUNCTION synthetic_session_failure()""")
        )
    body = {"request_id": str(start.request_id), "verifier": start.verifier, "code": code}
    response = h.client.post("/api/v1/auth/verify", json=body)
    assert response.status_code == 503
    assert "synthetic" not in response.text
    with transaction(h.engine) as session:
        for table in (User, AuthIdentity, SessionFamily, QuotaOperation, QuotaLedger):
            assert session.scalar(select(func.count()).select_from(table)) == 0
        attempt = session.get(LoginAttempt, start.request_id)
        assert (
            attempt is not None and attempt.consumed_at is None and attempt.code_digest is not None
        )
    with h.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER synthetic_failure ON session_credentials"))
        connection.execute(text("DROP FUNCTION synthetic_session_failure()"))
    renewed = h.client.post("/api/v1/auth/verify", json=body)
    assert renewed.status_code == 200
    authorization = {"Authorization": "Bearer " + renewed.json()["access_token"]}
    assert h.client.get("/api/v1/quota", headers=authorization).json()["available_bytes"] == 17
