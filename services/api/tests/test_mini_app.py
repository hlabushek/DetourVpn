"""Real local PostgreSQL/API; signed Telegram input is TEST SYNTHETIC ONLY."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness, begin, harness, headers, issue, login
from test_migrations import migration_config
from test_mini_app_validation import signed
from test_rewards import rule

from vpn_api.auth.contracts import TokenResponse
from vpn_api.db.connection import transaction
from vpn_api.db.models import MiniAppExchange, SessionFamily, User

__all__ = ["harness"]
pytestmark = pytest.mark.postgres


def payload(h: Harness, subject: int = 123) -> str:
    assert h.settings.telegram_bot_token is not None
    return signed(
        h.settings.telegram_bot_token.get_secret_value(),
        auth_date=str(int(datetime.now(UTC).timestamp())),
        user='{"id":' + str(subject) + "}",
    )


def exchange(h: Harness, raw: str) -> TokenResponse:
    h.auth.settings.enable_mini_app = True
    result = h.client.post("/api/v1/auth/mini-app", json={"init_data": raw})
    assert result.status_code == 200
    return TokenResponse.model_validate(result.json())


def test_disabled_and_no_payload_echo(harness: Harness) -> None:
    h = harness
    assert h.client.post("/api/v1/auth/mini-app", json={"init_data": payload(h)}).status_code == 503
    h.auth.settings.enable_mini_app = True
    raw = "DO_NOT_ECHO_RAW_PAYLOAD"
    result = h.client.post("/api/v1/auth/mini-app", json={"init_data": raw})
    assert result.status_code == 401 and raw not in result.text
    result = h.client.post("/api/v1/auth/mini-app", json={"init_data": raw, "extra": raw})
    assert result.status_code == 422 and raw not in result.text


def test_existing_identity_shared_balance_and_restricted_session(harness: Harness) -> None:
    h = harness
    h.auth.settings.initial_quota_bytes = 9007199254740993
    native = headers(login(h, 123))
    mini = headers(exchange(h, payload(h)))
    assert (
        h.client.get("/api/v1/me", headers=native).json()
        == h.client.get("/api/v1/me", headers=mini).json()
    )
    assert (
        h.client.get("/api/v1/quota", headers=native).json()
        == h.client.get("/api/v1/quota", headers=mini).json()
    )
    assert h.client.get("/api/v1/devices", headers=mini).json() == []
    assert h.client.get("/api/v1/quota", headers=mini).json()["available_bytes"] == 9007199254740993
    # Capability escalation is refused even with a valid Mini App bearer token.
    for permission in ("devices:manage", "tunnels:manage"):
        from vpn_api.auth.security import AuthError

        with transaction(h.engine) as session, pytest.raises(AuthError, match="forbidden"):
            h.auth.authenticate(session, mini["Authorization"][7:], permission)
    refreshed = h.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": exchange(h, payload(h)).refresh_token}
    )
    assert refreshed.status_code == 200
    refreshed_headers = {"Authorization": "Bearer " + refreshed.json()["access_token"]}
    assert h.client.get("/api/v1/tunnels", headers=refreshed_headers).status_code == 403
    assert (
        h.client.post(
            "/api/v1/devices",
            headers=refreshed_headers,
            json={
                "platform": "android",
                "key_type": "x25519",
                "public_key": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
            },
        ).status_code
        == 403
    )
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_native_and_mini_app_concurrent_first_login_share_initial_grant(harness: Harness) -> None:
    h = harness
    h.auth.settings.enable_mini_app = True
    h.auth.settings.initial_quota_bytes = 500
    start = begin(h)
    code = issue(h, start, 123)
    barrier = Barrier(2)

    def native_login() -> TokenResponse:
        barrier.wait(timeout=30)
        return h.auth.verify(start.request_id, start.verifier, code, "test-native")

    def mini_login() -> TokenResponse:
        barrier.wait(timeout=30)
        return exchange(h, payload(h))

    with ThreadPoolExecutor(max_workers=2) as pool:
        native_future, mini_future = pool.submit(native_login), pool.submit(mini_login)
        native, mini = headers(native_future.result()), headers(mini_future.result())
    assert (
        h.client.get("/api/v1/me", headers=native).json()
        == h.client.get("/api/v1/me", headers=mini).json()
    )
    assert h.client.get("/api/v1/quota", headers=mini).json()["available_bytes"] == 500
    assert len(h.client.get("/api/v1/quota/history", headers=mini).json()["items"]) == 1


def test_reordered_replay_and_parallel_exchange(harness: Harness) -> None:
    h = harness
    h.auth.settings.enable_mini_app = True
    raw = payload(h)
    barrier = Barrier(10)

    def worker(_: int) -> int:
        barrier.wait(timeout=30)
        return h.client.post("/api/v1/auth/mini-app", json={"init_data": raw}).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(worker, range(10)))
    assert results.count(200) == 1 and results.count(409) == 9
    assert (
        h.client.post(
            "/api/v1/auth/mini-app", json={"init_data": "&".join(reversed(raw.split("&")))}
        ).status_code
        == 409
    )
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(MiniAppExchange)) == 1


def test_reward_retry_history_isolation_and_logout(harness: Harness) -> None:
    h = harness
    auth = headers(exchange(h, payload(h)))
    other = headers(exchange(h, payload(h, 222)))
    rid = rule(h)
    body = {"rule_id": str(rid), "idempotency_key": str(uuid4())}
    first = h.client.post("/api/v1/rewards/claim", headers=auth, json=body)
    assert first.status_code == 200
    assert h.client.post("/api/v1/rewards/claim", headers=auth, json=body).json() == first.json()
    assert h.client.get("/api/v1/quota", headers=auth).json()["available_bytes"] == 17
    assert h.client.get("/api/v1/quota", headers=other).json()["available_bytes"] == 0
    assert (
        h.client.get("/api/v1/quota/history", headers=auth).json()["items"][0]["source_type"]
        == "reward"
    )
    assert h.client.post("/api/v1/auth/logout", headers=auth).status_code == 204
    assert h.client.get("/api/v1/quota", headers=auth).status_code == 401


def test_disabled_user_no_session_and_no_consumed_replay(harness: Harness) -> None:
    h = harness
    login(h, 123)
    with transaction(h.engine) as session:
        session.execute(update(User).values(status="disabled"))
    h.auth.settings.enable_mini_app = True
    assert h.client.post("/api/v1/auth/mini-app", json={"init_data": payload(h)}).status_code == 401
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(MiniAppExchange)) == 0


def test_retention_expiry_and_safe_rollback(harness: Harness) -> None:
    h = harness
    auth = headers(exchange(h, payload(h)))
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(h.engine), "0009_rewards")
    with transaction(h.engine) as session:
        session.execute(
            update(SessionFamily).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.execute(
            update(MiniAppExchange).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert h.client.get("/api/v1/me", headers=auth).status_code == 401
    assert h.auth.cleanup()["mini_app_exchanges"] == 1
