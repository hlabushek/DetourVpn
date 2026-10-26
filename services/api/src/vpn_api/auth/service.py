import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vpn_api.auth.contracts import (
    StartResponse,
    TelegramUpdate,
    TokenResponse,
    WebhookResponse,
)
from vpn_api.auth.security import (
    AuthError,
    Principal,
    beta_invite_digest,
    digest,
    keyed_digest,
    require_permission,
    token,
)
from vpn_api.auth.telegram import DeliveryUnknown, Sender
from vpn_api.config import Settings
from vpn_api.db.connection import transaction
from vpn_api.db.models import (
    AuthIdentity,
    BetaInvite,
    BotUpdate,
    CodeIssue,
    Device,
    LoginAttempt,
    MiniAppExchange,
    RateBucket,
    SessionCredential,
    SessionFamily,
    User,
)
from vpn_api.quota.service import LedgerService


def database_now(session: Session) -> datetime:
    # clock_timestamp, unlike transaction_timestamp, advances after waiting for locks.
    value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("Database clock unavailable")
    return value


@dataclass(frozen=True)
class PreparedLoginCode:
    message: str
    issued: bool
    generation: int | None = None


def cleanup_retained_auth_rows(engine: Engine) -> dict[str, int]:
    """Delete only authentication records beyond their established retention cutoffs."""
    result: dict[str, int] = {}
    with transaction(engine) as session:
        now = database_now(session)
        for model, condition in (
            (RateBucket, RateBucket.expires_at < now),
            (MiniAppExchange, MiniAppExchange.expires_at < now),
            (LoginAttempt, LoginAttempt.expires_at < now - timedelta(days=1)),
            (CodeIssue, CodeIssue.created_at < now - timedelta(days=1)),
            (BotUpdate, BotUpdate.created_at < now - timedelta(days=7)),
            (SessionFamily, SessionFamily.expires_at < now - timedelta(days=1)),
        ):
            ids = session.execute(delete(model).where(condition).returning(model.__table__.c[0]))
            result[model.__tablename__] = len(ids.all())
    return result


class AuthService:
    def __init__(self, engine: Engine, settings: Settings, sender: Sender) -> None:
        self.engine = engine
        self.settings = settings
        if settings.auth_pepper is None:
            raise ValueError("Authentication requires a pepper")
        self.pepper = settings.auth_pepper.get_secret_value()
        self.sender = sender

    def lock(self, session: Session, domain: str, subject: int) -> None:
        key = int(keyed_digest(self.pepper, domain, subject)[:16], 16)
        if key >= 2**63:
            key -= 2**64
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def beta_subject_allowed(self, session: Session, subject: int) -> bool:
        if not self.settings.beta_invites_only:
            return True
        if self.settings.auth_pepper is None:
            return False
        invite_digest = beta_invite_digest(self.settings.auth_pepper.get_secret_value(), subject)
        # FOR SHARE serializes authorization with admin revocation through commit.
        return (
            session.scalar(
                select(BetaInvite.id)
                .where(BetaInvite.subject_digest == invite_digest, BetaInvite.revoked_at.is_(None))
                .with_for_update(read=True)
            )
            is not None
        )

    def rate(self, operation: str, network: str) -> None:
        limits = {
            "start": (10, 100),
            "verify": (60, 1000),
            "refresh": (120, 1000),
            "webhook": (120, 1000),
            "device": (30, 300),
            "reward": (120, 3000),
            "mini_app": (30, 300),
        }
        per_network, global_limit = limits[operation]
        exceeded = False
        with transaction(self.engine) as session:
            now = database_now(session)
            window = int(now.timestamp()) // 60
            for subject, limit in (("global", global_limit), (network, per_network)):
                key = keyed_digest(self.pepper, "rate", operation, subject, window)
                statement = insert(RateBucket).values(
                    key=key, count=1, expires_at=now + timedelta(minutes=2)
                )
                count = session.scalar(
                    statement.on_conflict_do_update(
                        index_elements=[RateBucket.key], set_={"count": RateBucket.count + 1}
                    ).returning(RateBucket.count)
                )
                exceeded = exceeded or (count is not None and count > limit)
        if exceeded:
            raise AuthError("rate_limited", 429)

    def start(self, network: str) -> StartResponse:
        self.rate("start", network)
        verifier = token()
        with transaction(self.engine) as session:
            now = database_now(session)
            attempt = LoginAttempt(
                id=UUID(bytes=secrets.token_bytes(16)),
                verifier_digest=digest(verifier),
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
            session.add(attempt)
            session.flush()
            return StartResponse(
                request_id=attempt.id,
                verifier=verifier,
                bot_url=f"https://t.me/{self.settings.telegram_bot_username}?start={attempt.id.hex}",
                expires_at=attempt.expires_at,
            )

    def prepare_login_code(
        self, session: Session, request_id: UUID, subject: int
    ) -> PreparedLoginCode:
        """Build the OTP message and persist its digest inside the caller's transaction."""
        attempt = session.scalar(
            select(LoginAttempt).where(LoginAttempt.id == request_id).with_for_update()
        )
        now = database_now(session)
        if (
            attempt is None
            or attempt.expires_at <= now
            or attempt.consumed_at is not None
            or attempt.checks >= 5
            or (attempt.subject is not None and attempt.subject != subject)
        ):
            return PreparedLoginCode("Попытка недоступна. Начните новый вход в приложении.", False)
        if not self.beta_subject_allowed(session, subject):
            return PreparedLoginCode(
                "Вход доступен только участникам закрытого теста. Обратитесь к организатору.",
                False,
            )

        recent = (
            session.scalar(
                select(func.count())
                .select_from(CodeIssue)
                .where(
                    CodeIssue.subject == subject,
                    CodeIssue.created_at > now - timedelta(minutes=15),
                )
            )
            or 0
        )
        daily = (
            session.scalar(
                select(func.count())
                .select_from(CodeIssue)
                .where(
                    CodeIssue.subject == subject,
                    CodeIssue.created_at > now - timedelta(days=1),
                )
            )
            or 0
        )
        if (
            recent >= 5
            or daily >= 20
            or (attempt.issued_at is not None and attempt.issued_at > now - timedelta(seconds=60))
        ):
            return PreparedLoginCode("Лимит запросов кода. Подождите перед новой попыткой.", False)

        code = f"{secrets.randbelow(100_000_000):08d}"
        while attempt.code_digest is not None and hmac.compare_digest(
            attempt.code_digest,
            keyed_digest(self.pepper, "otp", attempt.id.hex, attempt.generation, code),
        ):
            code = f"{secrets.randbelow(100_000_000):08d}"
        attempt.subject = subject
        attempt.generation += 1
        attempt.code_digest = keyed_digest(
            self.pepper, "otp", attempt.id.hex, attempt.generation, code
        )
        attempt.issued_at = now
        attempt.code_expires_at = min(attempt.expires_at, now + timedelta(minutes=5))
        attempt.delivery_status = "pending"
        attempt.delivery_recorded_at = None
        session.add(CodeIssue(subject=subject, created_at=now))
        return PreparedLoginCode(
            message=(
                f"Код входа: {code}\nДействует до 5 минут. Введите только в нашем "
                f"приложении. Telegram ID аккаунта: {subject}. "
                "Если вход начали не вы, не передавайте код."
            ),
            issued=True,
            generation=attempt.generation,
        )

    def deliver_login_code(
        self, request_id: UUID, generation: int, subject: int, message: str
    ) -> str:
        status = "api_accepted"
        try:
            self.sender.send(subject, message)
        except DeliveryUnknown:
            status = "unknown"
        with transaction(self.engine) as session:
            now = database_now(session)
            session.execute(
                update(LoginAttempt)
                .where(
                    LoginAttempt.id == request_id,
                    LoginAttempt.generation == generation,
                    LoginAttempt.delivery_status == "pending",
                )
                .values(delivery_status=status, delivery_recorded_at=now)
            )
        return "delivery_unknown" if status == "unknown" else "processed"

    def webhook(self, payload: TelegramUpdate, network: str) -> WebhookResponse:
        self.rate("webhook", network)
        message = payload.message
        if (
            message is None
            or message.sender.is_bot
            or message.chat.type != "private"
            or message.chat.id != message.sender.id
            or any(
                value is not None
                for value in (
                    message.forward_origin,
                    message.forward_date,
                    message.via_bot,
                    message.business_connection_id,
                )
            )
        ):
            return WebhookResponse(status="ignored")
        command = re.fullmatch(
            r"/start(?:@"
            + re.escape(self.settings.telegram_bot_username)
            + r")?(?: ([0-9a-f]{32}))?",
            message.text,
        )
        if command is None:
            return WebhookResponse(status="ignored")
        reply = "Начните вход в нашем приложении и откройте ссылку на бота из него."
        delivery_attempt: tuple[UUID, int] | None = None
        with transaction(self.engine) as session:
            now = database_now(session)
            age = now.timestamp() - message.date
            if not -60 <= age <= 300:
                return WebhookResponse(status="ignored")
            # Durable claim happens before sending. Retries never resend or reissue a code.
            claimed = session.scalar(
                insert(BotUpdate)
                .values(
                    bot_id=self.settings.telegram_bot_id,
                    update_id=payload.update_id,
                    created_at=now,
                    status="claimed",
                )
                .on_conflict_do_nothing()
                .returning(BotUpdate.update_id)
            )
            if claimed is None:
                return WebhookResponse(status="duplicate")
            if command[1] is not None:
                self.lock(session, "subject", message.sender.id)
                request_id = UUID(hex=command[1])
                prepared = self.prepare_login_code(session, request_id, message.sender.id)
                reply = prepared.message
                if prepared.issued and prepared.generation is not None:
                    delivery_attempt = (request_id, prepared.generation)
        status: str = "sent"
        try:
            self.sender.send(message.chat.id, reply)
        except DeliveryUnknown:
            status = "unknown"
        with transaction(self.engine) as session:
            now = database_now(session)
            session.execute(
                update(BotUpdate)
                .where(
                    BotUpdate.bot_id == self.settings.telegram_bot_id,
                    BotUpdate.update_id == payload.update_id,
                )
                .values(status=status)
            )
            if delivery_attempt is not None:
                request_id, generation = delivery_attempt
                session.execute(
                    update(LoginAttempt)
                    .where(
                        LoginAttempt.id == request_id,
                        LoginAttempt.generation == generation,
                        LoginAttempt.delivery_status == "pending",
                    )
                    .values(
                        delivery_status="unknown" if status == "unknown" else "api_accepted",
                        delivery_recorded_at=now,
                    )
                )
        return WebhookResponse(status="delivery_unknown" if status == "unknown" else "processed")

    def issue_tokens(self, session: Session, family: SessionFamily, now: datetime) -> TokenResponse:
        access, refresh = token(), token()
        expires = min(now + timedelta(minutes=10), family.expires_at)
        session.add(
            SessionCredential(
                family_id=family.id,
                access_digest=digest(access),
                refresh_digest=digest(refresh),
                created_at=now,
                access_expires_at=expires,
            )
        )
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            access_expires_at=expires,
            refresh_expires_at=family.expires_at,
        )

    def verify(self, request_id: UUID, verifier: str, code: str, network: str) -> TokenResponse:
        self.rate("verify", network)
        with transaction(self.engine) as session:
            result = self._verify(session, request_id, verifier, code)
        # Failed-attempt counters must commit; raising inside the transaction would roll them back.
        if isinstance(result, AuthError):
            raise result
        return result

    def _verify(
        self, session: Session, request_id: UUID, verifier: str, code: str
    ) -> TokenResponse | AuthError:
        attempt = session.scalar(
            select(LoginAttempt).where(LoginAttempt.id == request_id).with_for_update()
        )
        now = database_now(session)
        if attempt is None or not hmac.compare_digest(attempt.verifier_digest, digest(verifier)):
            return AuthError("invalid_login")
        if (
            attempt.expires_at <= now
            or attempt.consumed_at is not None
            or attempt.checks >= 5
            or attempt.code_digest is None
            or attempt.subject is None
            or attempt.code_expires_at is None
            or attempt.code_expires_at <= now
        ):
            return AuthError("invalid_login")
        candidate = keyed_digest(self.pepper, "otp", attempt.id.hex, attempt.generation, code)
        if not hmac.compare_digest(attempt.code_digest, candidate):
            attempt.checks += 1
            return AuthError("invalid_login")
        if not self.beta_subject_allowed(session, attempt.subject):
            return AuthError("beta_invite_required", 403)
        # Subject lock serializes initial account creation across different challenges.
        self.lock(session, "identity", attempt.subject)
        identity = session.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider == "telegram_bot",
                AuthIdentity.provider_subject == attempt.subject,
            )
        )
        existing: User | None = None
        if identity is not None:
            existing = session.scalar(
                select(User).where(User.id == identity.user_id).with_for_update()
            )
            if existing is None or existing.status != "active":
                return AuthError("invalid_login")
        # Establish the auth decision only after all contended identity/user locks.
        # No new account may persist if a challenge expired while waiting.
        now = database_now(session)
        if attempt.code_expires_at <= now or attempt.expires_at <= now:
            return AuthError("invalid_login")
        if existing is None:
            user = User(status="active", verified_at=now)
            session.add(user)
            session.flush()
            session.add(
                AuthIdentity(user_id=user.id, provider_subject=attempt.subject, verified_at=now)
            )
        else:
            user = existing
        LedgerService(self.engine).ensure_initial_grant(
            session, user.id, self.settings.initial_quota_bytes
        )
        attempt.consumed_at = now
        attempt.code_digest = None
        family = SessionFamily(user_id=user.id, created_at=now, expires_at=now + timedelta(days=30))
        session.add(family)
        session.flush()
        return self.issue_tokens(session, family, now)

    def authenticate(self, session: Session, access: str, permission: str) -> Principal:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", access):
            raise AuthError("unauthorized")
        credential = session.scalar(
            select(SessionCredential).where(SessionCredential.access_digest == digest(access))
        )
        if credential is None:
            raise AuthError("unauthorized")
        family = session.get(SessionFamily, credential.family_id)
        if family is None:
            raise AuthError("unauthorized")
        identity = session.scalar(
            select(AuthIdentity).where(AuthIdentity.user_id == family.user_id)
        )
        if identity is None or not self.beta_subject_allowed(session, identity.provider_subject):
            raise AuthError("unauthorized")
        user = session.scalar(select(User).where(User.id == family.user_id).with_for_update())
        session.refresh(family, with_for_update=True)
        session.refresh(credential)
        now = database_now(session)
        if (
            user is None
            or user.status != "active"
            or family.revoked_at is not None
            or family.expires_at <= now
            or credential.access_expires_at <= now
            or credential.rotated_at is not None
        ):
            raise AuthError("unauthorized")
        device = session.get(Device, family.device_id) if family.device_id is not None else None
        if family.device_id is not None and (
            device is None or device.revoked_at is not None or device.user_id != user.id
        ):
            raise AuthError("unauthorized")
        if identity is None:
            raise AuthError("unauthorized")
        principal = Principal(user.id, family.id, user.role, identity.provider_subject)
        if family.client_kind == "mini_app" and permission not in {
            "self:read",
            "sessions:revoke",
            "quota:read",
            "rewards:claim",
            "referrals:manage",
            "devices:read",
        }:
            raise AuthError("forbidden", 403)
        require_permission(principal, permission)
        return principal

    def refresh(self, refresh_token: str, network: str) -> TokenResponse:
        self.rate("refresh", network)
        with transaction(self.engine) as session:
            result = self._refresh(session, refresh_token)
        if isinstance(result, AuthError):
            raise result
        return result

    def _refresh(self, session: Session, refresh_token: str) -> TokenResponse | AuthError:
        credential = session.scalar(
            select(SessionCredential).where(
                SessionCredential.refresh_digest == digest(refresh_token)
            )
        )
        if credential is None:
            return AuthError("invalid_refresh")
        family = session.get(SessionFamily, credential.family_id)
        if family is None:
            return AuthError("invalid_refresh")
        identity = session.scalar(
            select(AuthIdentity).where(AuthIdentity.user_id == family.user_id)
        )
        if identity is None or not self.beta_subject_allowed(session, identity.provider_subject):
            return AuthError("invalid_refresh")
        user = session.scalar(select(User).where(User.id == family.user_id).with_for_update())
        session.refresh(family, with_for_update=True)
        session.refresh(credential, with_for_update=True)
        now = database_now(session)
        if user is None or user.status != "active" or family.revoked_at or family.expires_at <= now:
            return AuthError("invalid_refresh")
        if credential.rotated_at is not None:
            family.revoked_at = now
            return AuthError("refresh_reused")
        device = session.get(Device, family.device_id) if family.device_id is not None else None
        if family.device_id is not None and (
            device is None or device.revoked_at is not None or device.user_id != user.id
        ):
            return AuthError("invalid_refresh")
        credential.rotated_at = now
        return self.issue_tokens(session, family, now)

    def logout(self, access: str, all_sessions: bool) -> None:
        with transaction(self.engine) as session:
            principal = self.authenticate(session, access, "sessions:revoke")
            statement = update(SessionFamily).where(SessionFamily.user_id == principal.user_id)
            if not all_sessions:
                statement = statement.where(SessionFamily.id == principal.family_id)
            session.execute(statement.values(revoked_at=database_now(session)))

    def cleanup(self) -> dict[str, int]:
        """Run explicitly on a local schedule; no data-plane or account deletion."""
        return cleanup_retained_auth_rows(self.engine)
