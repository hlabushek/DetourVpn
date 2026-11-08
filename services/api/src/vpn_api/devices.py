"""Authenticated public-key registry, one-time link codes and guest devices (ADR-093).

No key generation or tunnel operations here; revocation reuses the tunnel revocation chain.
"""

import base64
import binascii
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vpn_api.auth.contracts import Input, TokenResponse
from vpn_api.auth.routes import Access, Service, network
from vpn_api.auth.security import AuthError, Principal, keyed_digest
from vpn_api.auth.service import AuthService, database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import AuthIdentity, Device, DeviceLinkCode, SessionFamily, User
from vpn_api.devices_usage import last_seen, used_bytes
from vpn_api.schemas import ErrorResponse
from vpn_api.tunnels.service import revoke_device_tunnels

LINK_CODE_TTL = timedelta(minutes=10)
GUEST_SESSION_TTL = timedelta(days=30)
# Crockford base32 (no I, L, O, U); 8 characters is about 40 bits.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_PATTERN = re.compile(r"[0-9A-HJKMNP-TV-Z]{8}")
MAX_TRAFFIC_CAP_BYTES = 2**50
LABEL_PATTERN = r"^[^\x00-\x1f\x7f]+$"


def new_link_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def normalize_link_code(raw: str) -> str | None:
    """Canonical form of user input: upper case, no separators, Crockford look-alikes folded."""
    value = raw.upper().replace("-", "").replace(" ", "").translate(str.maketrans("OIL", "011"))
    return value if CODE_PATTERN.fullmatch(value) else None


def display_link_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def link_code_digest(auth: AuthService, code: str) -> str:
    return keyed_digest(auth.pepper, "link-code", code)


class DeviceInput(Input):
    platform: Literal["android"]
    key_type: Literal["x25519"]
    public_key: str

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Expected a canonical 32-byte base64 public key") from None
        if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("Expected a canonical nonzero 32-byte base64 public key")
        return value


class DeviceResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    platform: str
    key_type: str
    public_key: str
    created_at: datetime
    revoked_at: datetime | None
    role: Literal["owner", "guest"] = "owner"
    label: str | None = None
    traffic_cap_bytes: int | None = None
    # Attributed, already-measured bytes; a lower bound, not billing (see devices_usage).
    used_bytes_total: int | None = None
    used_bytes_30d: int | None = None
    last_connected_at: datetime | None = None
    last_country_code: str | None = None


class DevicePatch(Input):
    """Only fields present in the request are changed; an explicit null clears a value."""

    label: str | None = Field(default=None, min_length=1, max_length=40, pattern=LABEL_PATTERN)
    traffic_cap_bytes: int | None = Field(default=None, ge=1, le=MAX_TRAFFIC_CAP_BYTES)


class LinkCodeCreate(Input):
    idempotency_key: UUID
    label: str | None = Field(default=None, min_length=1, max_length=40, pattern=LABEL_PATTERN)
    traffic_cap_bytes: int | None = Field(default=None, ge=1, le=MAX_TRAFFIC_CAP_BYTES)


class LinkCodeView(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    label: str | None
    traffic_cap_bytes: int | None
    created_at: datetime
    expires_at: datetime


class LinkCodeCreated(LinkCodeView):
    code: str  # Shown exactly once; only a keyed digest is stored.


class LinkRedeemInput(Input):
    code: SecretStr

    @field_validator("code")
    @classmethod
    def bounded_code(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 32:
            raise ValueError("Invalid code length")
        return value


_errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 413, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1/devices", tags=["devices"], responses=_errors)
link_router = APIRouter(prefix="/api/v1/auth/link", tags=["auth"], responses=_errors)


def active_device_count(session: Session, user_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Device)
            .where(Device.user_id == user_id, Device.revoked_at.is_(None))
        )
        or 0
    )


def require_owner(principal: Principal) -> None:
    if principal.guest:
        raise AuthError("forbidden", 403)


def register_device(auth: AuthService, access: str, body: DeviceInput) -> DeviceResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:manage")
        # User lock from authenticate serializes device limits and revocation.
        key = int(keyed_digest(auth.pepper, "device-key", body.public_key)[:15], 16)
        auth.lock(session, "device-key-lock", key)
        existing = session.scalar(select(Device).where(Device.public_key == body.public_key))
        family = session.get(SessionFamily, principal.family_id)
        if family is None:
            raise AuthError("unauthorized")
        if existing is not None:
            if existing.user_id != principal.user_id or existing.revoked_at is not None:
                raise AuthError("device_conflict", 409)
            device = existing
        else:
            if family.device_id is not None:
                raise AuthError("session_device_mismatch", 409)
            if active_device_count(session, principal.user_id) >= auth.settings.device_limit:
                raise AuthError("device_limit_reached", 409)
            code = (
                session.scalar(
                    select(DeviceLinkCode)
                    .where(DeviceLinkCode.id == family.link_code_id)
                    .with_for_update()
                )
                if family.role == "guest" and family.link_code_id is not None
                else None
            )
            device = Device(
                user_id=principal.user_id,
                platform=body.platform,
                key_type=body.key_type,
                public_key=body.public_key,
                created_at=database_now(session),
                role=family.role,
                label=code.label if code is not None else None,
                traffic_cap_bytes=code.traffic_cap_bytes if code is not None else None,
            )
            session.add(device)
            session.flush()
            if code is not None:
                code.consumed_device_id = device.id
        if family.device_id is not None and family.device_id != device.id:
            raise AuthError("session_device_mismatch", 409)
        family.device_id = device.id
        return DeviceResponse.model_validate(device)


def device_views(session: Session, devices: list[Device]) -> list[DeviceResponse]:
    ids = [device.id for device in devices]
    now = database_now(session)
    total = used_bytes(session, ids)
    recent = used_bytes(session, ids, since=now - timedelta(days=30))
    seen = last_seen(session, ids)
    result = []
    for device in devices:
        view = DeviceResponse.model_validate(device)
        view.used_bytes_total = total[device.id]
        view.used_bytes_30d = recent[device.id]
        if device.id in seen:
            view.last_connected_at = seen[device.id].connected_at
            view.last_country_code = seen[device.id].country_code
        result.append(view)
    return result


@router.post("", response_model=DeviceResponse)
def create_device(
    body: DeviceInput, request: Request, auth: Service, access: Access
) -> DeviceResponse:
    auth.rate("device", network(request))
    return register_device(auth, access, body)


@router.get("", response_model=list[DeviceResponse])
def list_devices(
    auth: Service,
    access: Access,
    include_revoked: bool = False,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(100, ge=1, le=100),
) -> list[DeviceResponse]:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:read")
        statement = select(Device).where(Device.user_id == principal.user_id)
        if principal.guest:  # A guest sees only its own device.
            statement = statement.where(Device.id == principal.device_id)
        if not include_revoked:
            statement = statement.where(Device.revoked_at.is_(None))
        devices = list(
            session.scalars(
                statement.order_by(Device.created_at.desc(), Device.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return device_views(session, devices)


@router.post("/link-codes", response_model=LinkCodeCreated, status_code=201)
def create_link_code(
    body: LinkCodeCreate, request: Request, auth: Service, access: Access
) -> LinkCodeCreated:
    auth.rate("link_create", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:read")
        require_owner(principal)  # authenticate() holds the user lock: limits cannot race.
        now = database_now(session)
        if session.scalar(
            select(DeviceLinkCode.id).where(
                DeviceLinkCode.user_id == principal.user_id,
                DeviceLinkCode.idempotency_key == body.idempotency_key,
            )
        ):
            # The code is shown once; a replay cannot reveal it. Cancel and create a new one.
            raise AuthError("link_code_exists", 409)
        pending = (
            session.scalar(
                select(func.count())
                .select_from(DeviceLinkCode)
                .where(
                    DeviceLinkCode.user_id == principal.user_id,
                    DeviceLinkCode.revoked_at.is_(None),
                    DeviceLinkCode.consumed_at.is_(None),
                    DeviceLinkCode.expires_at > now,
                )
            )
            or 0
        )
        if active_device_count(session, principal.user_id) + pending >= auth.settings.device_limit:
            raise AuthError("device_limit_reached", 409)
        for _ in range(3):
            code = new_link_code()
            row = DeviceLinkCode(
                user_id=principal.user_id,
                code_digest=link_code_digest(auth, code),
                idempotency_key=body.idempotency_key,
                label=body.label,
                traffic_cap_bytes=body.traffic_cap_bytes,
                created_at=now,
                expires_at=now + LINK_CODE_TTL,
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError:
                continue  # Digest collision; vanishingly rare.
            return LinkCodeCreated(
                id=row.id,
                label=row.label,
                traffic_cap_bytes=row.traffic_cap_bytes,
                created_at=row.created_at,
                expires_at=row.expires_at,
                code=display_link_code(code),
            )
    raise AuthError("link_code_unavailable", 503)


@router.get("/link-codes", response_model=list[LinkCodeView])
def list_link_codes(auth: Service, access: Access) -> list[LinkCodeView]:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:read")
        require_owner(principal)
        rows = session.scalars(
            select(DeviceLinkCode)
            .where(
                DeviceLinkCode.user_id == principal.user_id,
                DeviceLinkCode.revoked_at.is_(None),
                DeviceLinkCode.consumed_at.is_(None),
                DeviceLinkCode.expires_at > database_now(session),
            )
            .order_by(DeviceLinkCode.created_at.desc())
        )
        return [LinkCodeView.model_validate(row) for row in rows]


@router.delete("/link-codes/{code_id}", status_code=204)
def cancel_link_code(code_id: UUID, request: Request, auth: Service, access: Access) -> Response:
    auth.rate("link_create", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:read")
        require_owner(principal)
        row = session.scalar(
            select(DeviceLinkCode)
            .where(DeviceLinkCode.id == code_id, DeviceLinkCode.user_id == principal.user_id)
            .with_for_update()
        )
        if row is None:
            raise AuthError("not_found", 404)
        if row.consumed_at is None and row.revoked_at is None:
            row.revoked_at = database_now(session)
    return Response(status_code=204)


@link_router.post("/redeem", response_model=TokenResponse)
def redeem_link_code(body: LinkRedeemInput, request: Request, auth: Service) -> TokenResponse:
    """Exchange a one-time code for a guest session on the owner's account (no auth)."""
    auth.rate("link_redeem", network(request))
    code = normalize_link_code(body.code.get_secret_value())
    # One generic error for unknown, expired, used and cancelled codes: no oracle.
    invalid = AuthError("invalid_link_code", 404)
    if code is None:
        raise invalid
    digest = link_code_digest(auth, code)
    with transaction(auth.engine) as session:
        probe = session.scalar(select(DeviceLinkCode).where(DeviceLinkCode.code_digest == digest))
        if probe is None:
            raise invalid
        # Lock order is user then code, as in every owner-side path; no deadlock with cancel.
        user = session.scalar(select(User).where(User.id == probe.user_id).with_for_update())
        row = session.scalar(
            select(DeviceLinkCode).where(DeviceLinkCode.id == probe.id).with_for_update()
        )
        now = database_now(session)
        identity = session.scalar(select(AuthIdentity).where(AuthIdentity.user_id == probe.user_id))
        if (
            row is None
            or user is None
            or user.status != "active"
            or row.consumed_at is not None
            or row.revoked_at is not None
            or row.expires_at <= now
            or identity is None
            or not auth.beta_subject_allowed(session, identity.provider_subject)
        ):
            raise invalid
        if active_device_count(session, user.id) >= auth.settings.device_limit:
            raise AuthError("device_limit_reached", 409)
        row.consumed_at = now
        family = SessionFamily(
            user_id=user.id,
            created_at=now,
            expires_at=now + GUEST_SESSION_TTL,
            role="guest",
            link_code_id=row.id,
        )
        session.add(family)
        session.flush()
        return auth.issue_tokens(session, family, now)


def owned_device(session: Session, principal: Principal, device_id: UUID) -> Device:
    if principal.guest and device_id != principal.device_id:
        raise AuthError("forbidden", 403)
    device = session.scalar(
        select(Device).where(Device.id == device_id, Device.user_id == principal.user_id)
    )
    if device is None:
        raise AuthError("not_found", 404)
    return device


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: UUID, auth: Service, access: Access) -> DeviceResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:read")
        return device_views(session, [owned_device(session, principal, device_id)])[0]


@router.patch("/{device_id}", response_model=DeviceResponse)
def patch_device(
    device_id: UUID, body: DevicePatch, request: Request, auth: Service, access: Access
) -> DeviceResponse:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:read")
        require_owner(principal)
        device = owned_device(session, principal, device_id)
        if device.revoked_at is not None:
            raise AuthError("not_found", 404)
        if "label" in body.model_fields_set:
            device.label = body.label
        if "traffic_cap_bytes" in body.model_fields_set:
            device.traffic_cap_bytes = body.traffic_cap_bytes
        session.flush()
        return device_views(session, [device])[0]


@router.delete("/{device_id}", status_code=204)
def revoke_device(device_id: UUID, request: Request, auth: Service, access: Access) -> Response:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        # Any owner session (native or Mini-App) may revoke any device; a guest only its own.
        principal = auth.authenticate(session, access, "devices:read")
        if principal.guest and device_id != principal.device_id:
            raise AuthError("forbidden", 403)
        device = session.scalar(
            select(Device)
            .where(Device.id == device_id, Device.user_id == principal.user_id)
            .with_for_update()
        )
        if device is None:
            raise AuthError("not_found", 404)
        now = database_now(session)
        if device.revoked_at is None:
            device.revoked_at = now
        revoke_device_tunnels(session, principal.user_id, device.id)
        session.execute(
            update(SessionFamily)
            .where(SessionFamily.user_id == principal.user_id, SessionFamily.device_id == device_id)
            .values(revoked_at=now)
        )
    return Response(status_code=204)
