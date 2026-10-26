import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from uuid import UUID


class AuthError(Exception):
    def __init__(self, code: str, status: int = 401) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def keyed_digest(pepper: str, *parts: str | int) -> str:
    # Versioned canonical encoding, with domain separation supplied by the first part.
    payload = json.dumps(["v1", *parts], separators=(",", ":"), ensure_ascii=True)
    return hmac.new(pepper.encode("ascii"), payload.encode("ascii"), hashlib.sha256).hexdigest()


def beta_invite_digest(pepper: str, telegram_subject: int) -> str:
    """Pseudonymize a beta invitee identity with explicit domain separation."""
    return keyed_digest(pepper, "beta-invite", telegram_subject)


def token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    family_id: UUID
    role: str
    telegram_subject: int


def require_permission(principal: Principal, permission: str) -> None:
    grants = {
        "user": {
            "self:read",
            "sessions:revoke",
            "devices:manage",
            "devices:read",
            "quota:read",
            "tunnels:manage",
            "rewards:claim",
            "referrals:manage",
        },
        "admin": {
            "self:read",
            "sessions:revoke",
            "devices:manage",
            "devices:read",
            "quota:read",
            "tunnels:manage",
            "rewards:claim",
            "referrals:manage",
        },
    }
    if permission.startswith("admin:"):
        if principal.role == "admin":
            raise AuthError("admin_mfa_required", 403)
        raise AuthError("forbidden", 403)
    if permission not in grants.get(principal.role, set()):
        raise AuthError("forbidden", 403)
