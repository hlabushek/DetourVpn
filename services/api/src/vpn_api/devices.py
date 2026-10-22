"""Authenticated public-key registry; no key generation or tunnel operations."""

import base64
import binascii
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select, update

from vpn_api.auth.contracts import Input
from vpn_api.auth.routes import Access, Service, network
from vpn_api.auth.security import AuthError, keyed_digest
from vpn_api.auth.service import AuthService, database_now
from vpn_api.db.connection import transaction
from vpn_api.db.models import Device, SessionFamily
from vpn_api.schemas import ErrorResponse
from vpn_api.tunnels.service import revoke_device_tunnels

MAX_ACTIVE_DEVICES = 3  # Local product assumption, not a paid-plan entitlement.


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


router = APIRouter(
    prefix="/api/v1/devices",
    tags=["devices"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 413, 422, 429, 503)},
)


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
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(Device)
                    .where(Device.user_id == principal.user_id, Device.revoked_at.is_(None))
                )
                or 0
            )
            if count >= MAX_ACTIVE_DEVICES:
                raise AuthError("device_limit_reached", 409)
            device = Device(
                user_id=principal.user_id,
                platform=body.platform,
                key_type=body.key_type,
                public_key=body.public_key,
                created_at=database_now(session),
            )
            session.add(device)
            session.flush()
        if family.device_id is not None and family.device_id != device.id:
            raise AuthError("session_device_mismatch", 409)
        family.device_id = device.id
        return DeviceResponse.model_validate(device)


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
        if not include_revoked:
            statement = statement.where(Device.revoked_at.is_(None))
        devices = session.scalars(
            statement.order_by(Device.created_at.desc(), Device.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return [DeviceResponse.model_validate(device) for device in devices]


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: UUID, auth: Service, access: Access) -> DeviceResponse:
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:read")
        device = session.scalar(
            select(Device).where(Device.id == device_id, Device.user_id == principal.user_id)
        )
        if device is None:
            raise AuthError("not_found", 404)
        return DeviceResponse.model_validate(device)


@router.delete("/{device_id}", status_code=204)
def revoke_device(device_id: UUID, request: Request, auth: Service, access: Access) -> Response:
    auth.rate("device", network(request))
    with transaction(auth.engine) as session:
        principal = auth.authenticate(session, access, "devices:manage")
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
