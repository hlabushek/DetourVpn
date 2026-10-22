"""Telegram HMAC validation. Raw initData and profile fields are never persisted."""

import hashlib
import hmac
import json
import re
from datetime import timedelta
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from vpn_api.auth.contracts import Input, TokenResponse
from vpn_api.auth.routes import Service, network
from vpn_api.auth.security import AuthError
from vpn_api.auth.service import database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import AuthIdentity, MiniAppExchange, SessionFamily, User
from vpn_api.quota.service import LedgerService
from vpn_api.schemas import ErrorResponse


def validate_init_data(raw: str, bot_token: str, now: float) -> tuple[int, str, int]:
    try:
        if len(raw.encode("utf-8")) > 16384 or re.search(r"%(?![0-9a-fA-F]{2})", raw):
            raise ValueError
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors="strict")
        fields = dict(pairs)
        if len(fields) != len(pairs) or not all(re.fullmatch(r"[a-z_]+", k) for k in fields):
            raise ValueError
        supplied = fields.pop("hash")
        if not re.fullmatch(r"[0-9a-f]{64}", supplied):
            raise ValueError
        check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
        key = hmac.digest(b"WebAppData", bot_token.encode(), "sha256")
        expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError
        if not re.fullmatch(r"[0-9]{1,12}", fields["auth_date"]):
            raise ValueError
        issued = int(fields["auth_date"])
        if not 0 <= now - issued <= 300:
            raise ValueError
        user = json.loads(fields["user"])
        subject = user["id"]
        if type(subject) is not int or not 0 < subject < 2**52 or user.get("is_bot", False):
            raise ValueError
        # The authenticated HMAC is a canonical replay identifier, independent of URL order.
        return subject, supplied, issued
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise AuthError("invalid_init_data") from None


class MiniAppInput(Input):
    init_data: SecretStr = Field(min_length=1, max_length=16384)


router = APIRouter(
    tags=["authentication"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 409, 422, 429, 503)},
)


@router.post("/api/v1/auth/mini-app", response_model=TokenResponse)
def exchange(body: MiniAppInput, request: Request, auth: Service) -> TokenResponse:
    if not auth.settings.enable_mini_app:
        raise AuthError("mini_app_disabled", 503)
    auth.rate("mini_app", network(request))
    bot_token = auth.settings.telegram_bot_token
    if bot_token is None:
        raise AuthError("mini_app_disabled", 503)
    with transaction(auth.engine) as session:
        subject, replay, issued = validate_init_data(
            body.init_data.get_secret_value(),
            bot_token.get_secret_value(),
            database_now(session).timestamp(),
        )
        auth.lock(session, "identity", subject)
        now = database_now(session)
        if now.timestamp() - issued > 300:
            raise AuthError("invalid_init_data")
        claimed = session.scalar(
            insert(MiniAppExchange)
            .values(digest=replay, expires_at=now + timedelta(minutes=6))
            .on_conflict_do_nothing()
            .returning(MiniAppExchange.digest)
        )
        if claimed is None:
            raise AuthError("init_data_replayed", 409)
        identity = session.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider == "telegram_bot", AuthIdentity.provider_subject == subject
            )
        )
        if identity is None:
            user = User(status="active", verified_at=now)
            session.add(user)
            session.flush()
            session.add(AuthIdentity(user_id=user.id, provider_subject=subject, verified_at=now))
        else:
            existing = session.scalar(
                select(User).where(User.id == identity.user_id).with_for_update()
            )
            if existing is None or existing.status != "active":
                raise AuthError("invalid_login")
            user = existing
        # A user row lock may have waited behind a ledger/revoke operation.
        now = database_now(session)
        if now.timestamp() - issued > 300:
            raise AuthError("invalid_init_data")
        LedgerService(auth.engine).ensure_initial_grant(
            session, user.id, auth.settings.initial_quota_bytes
        )
        family = SessionFamily(
            user_id=user.id,
            client_kind="mini_app",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        session.add(family)
        session.flush()
        return auth.issue_tokens(session, family, now)
