"""Real PostgreSQL/API integration; Telegram delivery is an explicitly fake test sender."""

import re
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_migrations import migration_config

from vpn_api.auth import service as auth_service_module
from vpn_api.auth.contracts import StartResponse, TelegramUpdate, TokenResponse
from vpn_api.auth.security import AuthError, Principal, require_permission
from vpn_api.auth.service import AuthService
from vpn_api.auth.telegram import DeliveryUnknown
from vpn_api.config import Settings
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    AuthIdentity,
    BotUpdate,
    CodeIssue,
    LoginAttempt,
    RateBucket,
    SessionCredential,
    SessionFamily,
    User,
)
from vpn_api.main import create_app

pytestmark = pytest.mark.postgres


class FakeTelegramSender:
    """Tests only: records messages in memory; never contacts Telegram."""

    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.fail = False

    def send(self, chat_id: int, text: str) -> None:
        if self.fail:
            raise DeliveryUnknown
        self.messages.append((chat_id, text))


@dataclass
class Harness:
    engine: Engine
    settings: Settings
    sender: FakeTelegramSender
    client: TestClient
    auth: AuthService


@pytest.fixture
def harness(engine: Engine) -> Iterator[Harness]:
    command.upgrade(migration_config(engine), "head")
    settings = Settings(
        _env_file=None,
        environment="test",
        enable_auth=True,
        database_url=engine.url.render_as_string(False),
        auth_pepper=secrets.token_urlsafe(32),
        telegram_webhook_secret=secrets.token_urlsafe(32),
        telegram_bot_token="123456789:" + secrets.token_urlsafe(32),
        telegram_bot_id=123456789,
        telegram_bot_username="LocalTestLoginBot",
        initial_quota_bytes=0,
    )
    sender = FakeTelegramSender()
    app = create_app(settings, database_engine=engine, telegram_sender=sender)
    with TestClient(app) as client:
        yield Harness(engine, settings, sender, client, app.state.auth)


def begin(h: Harness) -> StartResponse:
    response = h.client.post("/api/v1/auth/start")
    assert response.status_code == 200
    return StartResponse.model_validate(response.json())


def bot_update(start: StartResponse | None, subject: int = 2**40) -> TelegramUpdate:
    return TelegramUpdate.model_validate(
        {
            "update_id": secrets.randbelow(2**50),
            "message": {
                "message_id": 1,
                "date": int(datetime.now(UTC).timestamp()),
                "from": {"id": subject, "is_bot": False},
                "chat": {"id": subject, "type": "private"},
                "text": "/start" + (" " + start.request_id.hex if start is not None else ""),
            },
        }
    )


def webhook(h: Harness, payload: TelegramUpdate) -> str:
    assert h.settings.telegram_webhook_secret is not None
    response = h.client.post(
        "/api/v1/telegram/webhook",
        json=payload.model_dump(mode="json", by_alias=True),
        headers={
            "X-Telegram-Bot-Api-Secret-Token": h.settings.telegram_webhook_secret.get_secret_value()
        },
    )
    assert response.status_code == 200
    return str(response.json()["status"])


def issue(h: Harness, start: StartResponse, subject: int = 2**40) -> str:
    assert webhook(h, bot_update(start, subject)) == "processed"
    match = re.search(r"Код входа: ([0-9]{8})", h.sender.messages[-1][1])
    assert match is not None
    return match[1]


def login(h: Harness, subject: int = 2**40) -> TokenResponse:
    start = begin(h)
    code = issue(h, start, subject)
    return h.auth.verify(start.request_id, start.verifier, code, "test-login")


def headers(tokens: TokenResponse) -> dict[str, str]:
    return {"Authorization": "Bearer " + tokens.access_token}


def wrong(code: str) -> str:
    return str((int(code[0]) + 1) % 10) + code[1:]


def test_complete_api_login_refresh_and_logout(harness: Harness) -> None:
    h = harness
    start = begin(h)
    assert start.verifier not in start.bot_url
    code = issue(h, start)
    response = h.client.post(
        "/api/v1/auth/verify",
        json={
            "request_id": str(start.request_id),
            "verifier": start.verifier,
            "code": code,
        },
    )
    assert response.status_code == 200
    tokens = TokenResponse.model_validate(response.json())
    me = h.client.get("/api/v1/me", headers=headers(tokens))
    assert me.status_code == 200
    assert me.json()["telegram_subject"] == 2**40
    assert me.json()["role"] == "user"
    renewed = h.client.post("/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token})
    assert renewed.status_code == 200
    rotated = TokenResponse.model_validate(renewed.json())
    assert h.client.get("/api/v1/me", headers=headers(tokens)).status_code == 401
    assert h.client.get("/api/v1/me", headers=headers(rotated)).json()["id"] == me.json()["id"]
    assert h.client.post("/api/v1/auth/logout", headers=headers(rotated)).status_code == 204
    assert h.client.get("/api/v1/me", headers=headers(rotated)).status_code == 401
    with pytest.raises(AuthError, match="invalid_refresh"):
        h.auth.refresh(rotated.refresh_token, "logout-check")


def test_wrong_secret_does_not_trust_client_identity(harness: Harness) -> None:
    h = harness
    start = begin(h)
    response = h.client.post(
        "/api/v1/telegram/webhook", json=bot_update(start).model_dump(by_alias=True)
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "untrusted_webhook"
    assert h.sender.messages == []
    assert (
        h.client.post(
            "/api/v1/auth/verify",
            json={
                "request_id": str(start.request_id),
                "verifier": start.verifier,
                "code": "0" * 8,
                "telegram_subject": 1,
                "role": "admin",
            },
        ).status_code
        == 422
    )
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
        assert session.scalar(select(func.count()).select_from(BotUpdate)) == 0


@pytest.mark.parametrize(
    "kind",
    ["bot", "group", "forward", "mismatch", "old", "future", "via_bot", "business", "edited"],
)
def test_untrusted_message_shapes_are_ignored(harness: Harness, kind: str) -> None:
    h = harness
    raw = bot_update(begin(h)).model_dump(by_alias=True)
    message = raw["message"]
    if kind == "bot":
        message["from"]["is_bot"] = True
    elif kind == "group":
        message["chat"]["type"] = "group"
    elif kind == "forward":
        message["forward_origin"] = {"type": "user"}
    elif kind == "mismatch":
        message["chat"]["id"] += 1
    elif kind in {"old", "future"}:
        message["date"] += -301 if kind == "old" else 120
    elif kind == "edited":
        raw["edited_message"] = raw.pop("message")
    else:
        message[kind if kind == "via_bot" else "business_connection_id"] = "untrusted"
    assert webhook(h, TelegramUpdate.model_validate(raw)) == "ignored"
    assert h.sender.messages == []


def test_code_is_one_time_and_bound_to_pairing(harness: Harness) -> None:
    h = harness
    first, second = begin(h), begin(h)
    code = issue(h, first)
    for request_id, verifier in (
        (second.request_id, first.verifier),
        (first.request_id, second.verifier),
    ):
        with pytest.raises(AuthError, match="invalid_login"):
            h.auth.verify(request_id, verifier, code, "binding")
    h.auth.verify(first.request_id, first.verifier, code, "binding")
    with pytest.raises(AuthError, match="invalid_login"):
        h.auth.verify(first.request_id, first.verifier, code, "binding")


def test_five_failures_commit_and_resend_does_not_reset_budget(harness: Harness) -> None:
    h = harness
    start = begin(h)
    code = issue(h, start)
    for _ in range(4):
        with pytest.raises(AuthError, match="invalid_login"):
            h.auth.verify(start.request_id, start.verifier, wrong(code), "bad-code")
    with transaction(h.engine) as session:
        session.execute(
            update(LoginAttempt).values(issued_at=datetime.now(UTC) - timedelta(seconds=61))
        )
    replacement = issue(h, start)
    # The previous generation is never valid, including after an explicit resend.
    with pytest.raises(AuthError, match="invalid_login"):
        h.auth.verify(start.request_id, start.verifier, code, "bad-code")
    with pytest.raises(AuthError, match="invalid_login"):
        h.auth.verify(start.request_id, start.verifier, replacement, "bad-code")
    with transaction(h.engine) as session:
        attempt = session.get(LoginAttempt, start.request_id)
        assert attempt is not None and attempt.checks == 5
        assert session.scalar(select(func.count()).select_from(SessionFamily)) == 0


@pytest.mark.parametrize("field", ["expires_at", "code_expires_at"])
def test_expired_pairing_or_code_cannot_login(harness: Harness, field: str) -> None:
    h = harness
    start = begin(h)
    code = issue(h, start)
    with transaction(h.engine) as session:
        session.execute(
            update(LoginAttempt).values({field: datetime.now(UTC) - timedelta(seconds=1)})
        )
    with pytest.raises(AuthError, match="invalid_login"):
        h.auth.verify(start.request_id, start.verifier, code, "expired")


def test_subject_cannot_be_rebound_and_resend_has_cooldown(harness: Harness) -> None:
    h = harness
    start = begin(h)
    original = issue(h, start, subject=111)
    assert webhook(h, bot_update(start, subject=222)) == "processed"
    assert "Код входа:" not in h.sender.messages[-1][1]
    assert webhook(h, bot_update(start, subject=111)) == "processed"
    assert "Код входа:" not in h.sender.messages[-1][1]
    tokens = h.auth.verify(start.request_id, start.verifier, original, "subject")
    assert h.client.get("/api/v1/me", headers=headers(tokens)).json()["telegram_subject"] == 111


def test_duplicate_update_and_delivery_unknown_never_reissue(harness: Harness) -> None:
    h = harness
    start = begin(h)
    payload = bot_update(start)
    h.sender.fail = True
    assert webhook(h, payload) == "delivery_unknown"
    h.sender.fail = False
    assert webhook(h, payload) == "duplicate"
    assert h.sender.messages == []
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(CodeIssue)) == 1
        assert session.scalar(select(BotUpdate.status)) == "unknown"
        assert session.scalar(select(LoginAttempt.generation)) == 1


def test_concurrent_verifications_create_exactly_one_session(harness: Harness) -> None:
    h = harness
    start = begin(h)
    code = issue(h, start)
    barrier = Barrier(2, timeout=10)

    def verify_once(_: int) -> str:
        barrier.wait()
        try:
            h.auth.verify(start.request_id, start.verifier, code, "race")
            return "ok"
        except AuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(verify_once, range(2))) == ["invalid_login", "ok"]
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(SessionFamily)) == 1
        assert session.scalar(select(func.count()).select_from(User)) == 1


def test_same_identity_concurrent_pairings_use_one_account(harness: Harness) -> None:
    h = harness
    starts = [begin(h), begin(h)]
    codes = [issue(h, start) for start in starts]
    barrier = Barrier(2, timeout=10)

    def verify_once(index: int) -> None:
        barrier.wait()
        h.auth.verify(
            starts[index].request_id, starts[index].verifier, codes[index], "identity-race"
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(verify_once, range(2)))
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        assert session.scalar(select(func.count()).select_from(AuthIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(SessionFamily)) == 2


def test_refresh_reuse_revokes_rotated_tokens(harness: Harness) -> None:
    h = harness
    first = login(h)
    rotated = h.auth.refresh(first.refresh_token, "refresh")
    with pytest.raises(AuthError, match="refresh_reused"):
        h.auth.refresh(first.refresh_token, "refresh")
    assert h.client.get("/api/v1/me", headers=headers(rotated)).status_code == 401
    with pytest.raises(AuthError, match="invalid_refresh"):
        h.auth.refresh(rotated.refresh_token, "refresh")


def test_concurrent_refresh_detects_reuse(harness: Harness) -> None:
    h = harness
    first = login(h)
    barrier = Barrier(2, timeout=10)

    def refresh_once(_: int) -> str:
        barrier.wait()
        try:
            h.auth.refresh(first.refresh_token, "refresh-race")
            return "ok"
        except AuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(refresh_once, range(2))) == ["ok", "refresh_reused"]
    with transaction(h.engine) as session:
        assert session.scalar(select(SessionFamily.revoked_at)) is not None


def test_logout_all_scopes_to_account_and_disabled_users_fail(harness: Harness) -> None:
    h = harness
    first, same, other = login(h, 111), login(h, 111), login(h, 222)
    assert h.client.post("/api/v1/auth/logout-all", headers=headers(first)).status_code == 204
    assert h.client.get("/api/v1/me", headers=headers(same)).status_code == 401
    assert h.client.get("/api/v1/me", headers=headers(other)).status_code == 200
    with transaction(h.engine) as session:
        session.execute(update(User).values(status="disabled"))
    assert h.client.get("/api/v1/me", headers=headers(other)).status_code == 401
    with pytest.raises(AuthError, match="invalid_refresh"):
        h.auth.refresh(other.refresh_token, "disabled")


def test_access_and_family_expiry(harness: Harness) -> None:
    h = harness
    tokens = login(h)
    with transaction(h.engine) as session:
        session.execute(
            update(SessionCredential).values(
                access_expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
    assert h.client.get("/api/v1/me", headers=headers(tokens)).status_code == 401
    rotated = h.auth.refresh(tokens.refresh_token, "expired-access")
    with transaction(h.engine) as session:
        session.execute(
            update(SessionFamily).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(AuthError, match="invalid_refresh"):
        h.auth.refresh(rotated.refresh_token, "expired-family")


def test_rate_limit_unknown_ids_and_untrusted_forwarding(harness: Harness) -> None:
    h = harness
    for _ in range(10):
        assert h.client.post("/api/v1/auth/start").status_code == 200
    assert (
        h.client.post("/api/v1/auth/start", headers={"X-Forwarded-For": "192.0.2.1"}).status_code
        == 429
    )
    for index in range(61):
        with pytest.raises(AuthError) as result:
            h.auth.verify(uuid4(), secrets.token_urlsafe(32), "0" * 8, "unknown-id")
        assert result.value.code == ("invalid_login" if index < 60 else "rate_limited")
    with transaction(h.engine) as session:
        count = session.scalar(select(func.count()).select_from(RateBucket))
        assert count is not None and count > 0


def test_subject_rolling_issuance_limits(harness: Harness) -> None:
    h = harness
    with transaction(h.engine) as session:
        session.add_all([CodeIssue(subject=111, created_at=datetime.now(UTC)) for _ in range(5)])
        session.add_all(
            [
                CodeIssue(subject=222, created_at=datetime.now(UTC) - timedelta(hours=1))
                for _ in range(20)
            ]
        )
    for subject in (111, 222):
        assert webhook(h, bot_update(begin(h), subject)) == "processed"
        assert "Код входа:" not in h.sender.messages[-1][1]


def test_secrets_are_not_stored_or_echoed(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h = harness
    start = begin(h)
    code = issue(h, start)
    tokens = h.auth.verify(start.request_id, start.verifier, code, "privacy")
    with transaction(h.engine) as session:
        attempt = session.get(LoginAttempt, start.request_id)
        credential = session.scalar(select(SessionCredential))
        assert attempt is not None and credential is not None
        assert attempt.verifier_digest != start.verifier and attempt.code_digest is None
        assert credential.access_digest != tokens.access_token
        assert credential.refresh_digest != tokens.refresh_token
    response = h.client.post("/api/v1/auth/verify", json={"verifier": start.verifier, "code": code})
    assert response.status_code == 422
    for secret in (start.verifier, code, tokens.access_token, tokens.refresh_token):
        assert secret not in response.text and secret not in caplog.text
    oversized = h.client.post("/api/v1/auth/verify", content=b"x" * 16385)
    assert oversized.status_code == 413
    assert oversized.json()["error"]["request_id"] == oversized.headers["x-request-id"]


def test_auth_downgrade_refuses_populated_state(harness: Harness) -> None:
    begin(harness)
    with pytest.raises(DBAPIError, match="Refusing downgrade"):
        command.downgrade(migration_config(harness.engine), "0001_accounts")


def test_storage_failure_is_sanitized_and_health_stays_live(harness: Harness) -> None:
    h = harness
    with h.engine.begin() as connection:
        connection.execute(text("DROP TABLE rate_buckets"))
    response = h.client.post("/api/v1/auth/start")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert "rate_buckets" not in response.text and "INSERT" not in response.text
    assert h.client.get("/api/v1/health").json() == {"status": "ok"}


def test_chunked_body_limit_uses_stable_error_contract(harness: Harness) -> None:
    response = harness.client.post("/api/v1/auth/verify", content=iter([b"x" * 8192] * 3))
    assert response.status_code == 413
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_expiry_during_identity_wait_does_not_create_account(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    start = begin(h)
    code = issue(h, start)
    real_clock = auth_service_module.database_now
    calls = 0

    def advanced_clock(session: Session) -> datetime:
        # Explicit simulated clock advancement at the post-lock boundary; DB remains real.
        nonlocal calls
        calls += 1
        current = real_clock(session)
        return current + timedelta(hours=1) if calls >= 3 else current

    monkeypatch.setattr(auth_service_module, "database_now", advanced_clock)
    with pytest.raises(AuthError, match="invalid_login"):
        h.auth.verify(start.request_id, start.verifier, code, "clock-boundary")
    with transaction(h.engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
        assert session.scalar(select(func.count()).select_from(SessionFamily)) == 0


def test_cleanup_preserves_live_sessions_and_identity(harness: Harness) -> None:
    h = harness
    active = login(h)
    expired = begin(h)
    with transaction(h.engine) as session:
        session.execute(
            update(LoginAttempt)
            .where(LoginAttempt.id == expired.request_id)
            .values(expires_at=datetime.now(UTC) - timedelta(days=2))
        )
        session.add(
            BotUpdate(
                bot_id=1,
                update_id=1,
                created_at=datetime.now(UTC) - timedelta(days=8),
                status="sent",
            )
        )
    result = h.auth.cleanup()
    assert result["login_attempts"] == 1 and result["bot_updates"] == 1
    assert h.client.get("/api/v1/me", headers=headers(active)).status_code == 200


def test_rbac_unknown_roles_permissions_and_admin_mfa() -> None:
    for role in ("user", "admin", "unexpected"):
        principal = Principal(uuid4(), uuid4(), role, 111)
        with pytest.raises(AuthError):
            require_permission(principal, "admin:write")
        with pytest.raises(AuthError):
            require_permission(principal, "unknown:permission")


def test_auth_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, enable_auth=True)
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.post("/api/v1/auth/start").status_code == 503
