"""Real local PostgreSQL/API; signed Telegram input is TEST SYNTHETIC ONLY."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from httpx2 import Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from test_authentication import Harness, begin, bot_update, harness, headers, issue, login, webhook
from test_migrations import migration_config
from test_mini_app_validation import signed
from test_rewards import rule

from vpn_api.auth.contracts import TokenResponse
from vpn_api.db.connection import transaction
from vpn_api.db.models import CodeIssue, LoginAttempt, MiniAppExchange, SessionFamily, User

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


def request_code(h: Harness, raw: str, request_id: str) -> Response:
    h.auth.settings.enable_mini_app = True
    return h.client.post(
        "/api/v1/auth/mini-app/request-code",
        json={"init_data": raw, "request_id": request_id},
    )


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


def test_mini_app_identity_can_deliver_android_otp_without_creating_mini_session(
    harness: Harness,
) -> None:
    h = harness
    start = begin(h)
    raw = payload(h, subject=123)
    response = request_code(h, raw, start.request_id.hex)
    assert response.status_code == 200
    assert response.json() == {"status": "processed"}
    assert raw not in response.text and start.verifier not in response.text
    assert len(h.sender.messages) == 1 and h.sender.messages[0][0] == 123
    match = re.search(r"Код входа: ([0-9]{8})", h.sender.messages[0][1])
    assert match is not None
    assert raw not in h.sender.messages[0][1]
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(SessionFamily)) == 0
        attempt = session.get(LoginAttempt, start.request_id)
        assert attempt is not None
        assert attempt.delivery_status == "api_accepted"
        assert attempt.delivery_recorded_at is not None
    tokens = h.auth.verify(start.request_id, start.verifier, match[1], "test-android")
    assert h.client.get("/api/v1/me", headers=headers(tokens)).status_code == 200


def test_mini_app_android_otp_rejects_bad_signature_and_replayed_init_data(
    harness: Harness,
) -> None:
    h = harness
    start = begin(h)
    raw = payload(h, subject=123)
    invalid = request_code(h, raw + "x", start.request_id.hex)
    assert invalid.status_code == 401 and raw not in invalid.text
    assert len(h.sender.messages) == 0

    barrier = Barrier(10)

    def worker(_: int) -> int:
        barrier.wait(timeout=30)
        return request_code(h, raw, start.request_id.hex).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(worker, range(10)))
    assert statuses.count(200) == 1
    assert statuses.count(409) == 9
    assert len(h.sender.messages) == 1
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(MiniAppExchange)) == 1
        attempt = session.get(LoginAttempt, start.request_id)
        assert attempt is not None and attempt.delivery_status == "api_accepted"


def test_beta_invites_block_webhook_mini_app_otp_and_direct_session(harness: Harness) -> None:
    h = harness
    h.auth.settings.beta_invites_only = True
    start = begin(h)

    assert webhook(h, bot_update(start, subject=123)) == "processed"
    assert "только участникам закрытого теста" in h.sender.messages[-1][1]
    assert "Код входа:" not in h.sender.messages[-1][1]

    messages_before = len(h.sender.messages)
    response = request_code(h, payload(h, subject=123), start.request_id.hex)
    assert response.status_code == 200
    assert response.json() == {"status": "rejected"}
    assert len(h.sender.messages) == messages_before

    direct = h.client.post("/api/v1/auth/mini-app", json={"init_data": payload(h, subject=456)})
    assert direct.status_code == 403
    assert "456" not in direct.text

    with transaction(h.engine) as session:
        attempt = session.get(LoginAttempt, start.request_id)
        assert attempt is not None and attempt.subject is None and attempt.generation == 0
        assert session.scalar(select(func.count()).select_from(CodeIssue)) == 0
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_mini_app_rejected_or_rate_limited_code_request_does_not_message_user(
    harness: Harness,
) -> None:
    h = harness
    start = begin(h)
    # A valid Telegram identity with an unknown attempt must not cause a bot DM.
    unknown = request_code(h, payload(h), uuid4().hex)
    assert unknown.status_code == 200
    assert unknown.json() == {"status": "rejected"}
    assert h.sender.messages == []

    valid = request_code(h, payload(h), start.request_id.hex)
    assert valid.status_code == 200 and valid.json() == {"status": "processed"}
    assert len(h.sender.messages) == 1
    limited = request_code(h, payload(h), start.request_id.hex)
    assert limited.status_code == 200 and limited.json() == {"status": "rejected"}
    assert len(h.sender.messages) == 1


def test_mini_app_otp_attempt_cannot_be_rebound_to_another_telegram_user(
    harness: Harness,
) -> None:
    h = harness
    start = begin(h)
    first = request_code(h, payload(h, subject=123), start.request_id.hex)
    assert first.status_code == 200
    first_code = re.search(r"Код входа: ([0-9]{8})", h.sender.messages[-1][1])
    assert first_code is not None and h.sender.messages[-1][0] == 123

    messages_before = len(h.sender.messages)
    second = request_code(h, payload(h, subject=456), start.request_id.hex)
    assert second.status_code == 200 and second.json() == {"status": "rejected"}
    assert len(h.sender.messages) == messages_before
    tokens = h.auth.verify(start.request_id, start.verifier, first_code[1], "test-android")
    assert h.client.get("/api/v1/me", headers=headers(tokens)).json()["telegram_subject"] == 123


def test_mini_app_otp_delivery_unknown_is_not_retried(harness: Harness) -> None:
    h = harness
    h.sender.fail = True
    start = begin(h)
    raw = payload(h, subject=123)
    response = request_code(h, raw, start.request_id.hex)
    assert response.status_code == 200
    assert response.json() == {"status": "delivery_unknown"}
    assert raw not in response.text and start.verifier not in response.text
    assert request_code(h, raw, start.request_id.hex).status_code == 409
    assert len(h.sender.messages) == 0
    with transaction(h.engine) as session:
        attempt = session.get(LoginAttempt, start.request_id)
        assert attempt is not None
        assert attempt.delivery_status == "unknown"
        assert attempt.delivery_recorded_at is not None


def test_late_delivery_receipt_cannot_overwrite_newer_code_generation(harness: Harness) -> None:
    h = harness
    start = begin(h)
    response = request_code(h, payload(h), start.request_id.hex)
    assert response.status_code == 200 and response.json() == {"status": "processed"}
    with transaction(h.engine) as session:
        attempt = session.get(LoginAttempt, start.request_id)
        assert attempt is not None and attempt.generation == 1
        attempt.generation = 2
        attempt.delivery_status = "pending"
        attempt.delivery_recorded_at = None

    result = h.auth.deliver_login_code(start.request_id, 1, 123, "TEST ONLY delayed send")
    assert result == "processed"
    with transaction(h.engine) as session:
        attempt = session.get(LoginAttempt, start.request_id)
        assert attempt is not None
        assert attempt.generation == 2 and attempt.delivery_status == "pending"
        assert attempt.delivery_recorded_at is None


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
