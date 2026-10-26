"""Key confirmation and explicit single-peer staging profile; no client private keys."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
from datetime import timedelta
from ipaddress import IPv4Address
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from vpn_api.auth.security import AuthError, Principal
from vpn_api.auth.service import AuthService, database_now
from vpn_api.db.models import Device, DeviceKeyProof, SessionFamily
from vpn_api.tunnels.awg_profile import AWG_PARAMETERS as AWG_PARAMETERS
from vpn_api.tunnels.keys import canonical_key as canonical_key

DOMAIN = "project-vpn:key-confirmation:v1:"


class NodeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    node_id: UUID
    runtime_id: UUID
    server_public_key: str
    header_protection_key: SecretStr
    # Operator-approved staging endpoint. Never supplied by a user or agent request.
    endpoint: str = "45.10.166.106:51892"

    @field_validator("server_public_key")
    @classmethod
    def check_public(cls, value: str) -> str:
        return canonical_key(value)

    @field_validator("header_protection_key")
    @classmethod
    def check_header(cls, value: SecretStr) -> SecretStr:
        canonical_key(value.get_secret_value())
        return value

    @field_validator("endpoint")
    @classmethod
    def check_endpoint(cls, value: str) -> str:
        host, port = value.split(":")
        address = IPv4Address(host)
        if (
            not address.is_global
            or address.is_multicast
            or not port.isascii()
            or not port.isdecimal()
        ):
            raise ValueError("Expected public IPv4 endpoint")
        if not 1 <= int(port) <= 65535 or str(int(port)) != port:
            raise ValueError("Invalid UDP port")
        return value

    def client_config(self) -> str:
        return (
            "[Interface]\nAddress = 10.203.71.2/30\nDNS = 1.1.1.1\nMTU = 1280\n"
            + AWG_PARAMETERS
            + "HeaderProtectionKey = "
            + self.header_protection_key.get_secret_value()
            + "\n[Peer]\nPublicKey = "
            + self.server_public_key
            + "\nEndpoint = "
            + self.endpoint
            + "\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n"
        )


class NodeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    version: int = Field(strict=True, ge=1, le=1)
    profiles: list[NodeProfile] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def unique_nodes(self) -> "NodeManifest":
        for values in (
            [p.node_id for p in self.profiles],
            [p.endpoint for p in self.profiles],
            [p.server_public_key for p in self.profiles],
        ):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate node identity")
        return self


def profiles(auth: AuthService) -> list[NodeProfile]:
    path = auth.settings.tunnel_profile_file
    if path is None:
        return []
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 8192:
            raise ValueError("Invalid profile file")
        if os.name != "nt" and info.st_mode & 0o077:
            raise ValueError("Profile must be private")
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "profiles" in data:
            return NodeManifest.model_validate(data).profiles
        return [NodeProfile.model_validate(data)]
    except (OSError, ValueError):
        raise AuthError("tunnel_profile_unavailable", 503) from None


def profile(auth: AuthService, node_id: UUID | None = None) -> NodeProfile | None:
    configured = profiles(auth)
    if node_id is None:
        return configured[0] if configured else None
    return next((p for p in configured if p.node_id == node_id), None)


def bound_device(session: Session, principal: Principal, device_id: UUID) -> Device:
    device = session.scalar(
        select(Device).where(Device.id == device_id, Device.user_id == principal.user_id)
    )
    if device is None:
        raise AuthError("not_found", 404)
    if device.revoked_at is not None:
        raise AuthError("device_revoked", 409)
    family = session.get(SessionFamily, principal.family_id)
    if family is None or family.device_id != device.id:
        raise AuthError("session_device_mismatch", 409)
    return device


class KeyChallenge(BaseModel):
    id: UUID
    ephemeral_public_key: str
    transcript: str
    expires_in_seconds: int = 120


def key_challenge(session: Session, principal: Principal, device_id: UUID) -> KeyChallenge:
    device = bound_device(session, principal, device_id)
    ephemeral = X25519PrivateKey.generate()
    try:
        shared = ephemeral.exchange(
            X25519PublicKey.from_public_bytes(base64.b64decode(device.public_key))
        )
    except ValueError:
        raise AuthError("device_key_unusable", 409) from None
    challenge_id = uuid4()
    transcript = DOMAIN + challenge_id.hex + ":" + secrets.token_hex(32)
    response = hmac.new(shared, transcript.encode("ascii"), hashlib.sha256).hexdigest()
    now = database_now(session)
    session.add(
        DeviceKeyProof(
            id=challenge_id,
            user_id=principal.user_id,
            device_id=device.id,
            family_id=principal.family_id,
            response_digest=hashlib.sha256(response.encode()).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(seconds=120),
        )
    )
    return KeyChallenge(
        id=challenge_id,
        ephemeral_public_key=base64.b64encode(ephemeral.public_key().public_bytes_raw()).decode(),
        transcript=transcript,
    )


def consume_proof(
    session: Session,
    principal: Principal,
    device_id: UUID,
    proof_id: UUID | None,
    response: SecretStr | None,
) -> None:
    proof = session.get(DeviceKeyProof, proof_id) if proof_id is not None else None
    if (
        proof is None
        or response is None
        or proof.user_id != principal.user_id
        or proof.device_id != device_id
        or proof.family_id != principal.family_id
        or proof.consumed_at is not None
        or proof.expires_at <= database_now(session)
    ):
        raise AuthError("device_key_proof_required", 409)
    supplied = hashlib.sha256(
        response.get_secret_value().encode("ascii", errors="replace")
    ).hexdigest()
    if not secrets.compare_digest(proof.response_digest, supplied):
        raise AuthError("device_key_proof_invalid", 409)
    proof.consumed_at = database_now(session)
