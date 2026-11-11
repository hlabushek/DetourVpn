"""Latest-release manifest for the in-app updater (ADR-107).

One manifest describes the single latest APK. It is a static file on the server, written by the
release script; the API only validates and serves it. A missing or invalid file is "unavailable",
never a partial or guessed manifest.

Signing (ADR-107 decision 5): the file is an envelope `{payload, signature, key_id}`. `payload`
is the exact manifest JSON bytes (standard base64), `signature` is Ed25519 over those bytes. The
app verifies it with its own copy of the public key; the API refuses to serve an envelope that
does not verify with the same key, so a wrong or missing key fails closed on both sides.
"""

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path
from typing import Annotated

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Owner's update-signing public key: raw 32-byte Ed25519 key, standard base64. Must equal
# `UpdateSignature.PUBLIC_KEY_B64` (Java; a test pins the equality). Empty = no signed key has
# been generated yet, so no manifest is served and the app offers no update.
UPDATE_SIGNING_PUBLIC_KEY = ""

MAX_MANIFEST_BYTES = 16 * 1024
_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_VERSION_NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,31}")


class AppUpdateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_code: Annotated[int, Field(ge=1, le=2_147_483_647)]
    version_name: str
    min_supported_version_code: Annotated[int, Field(ge=1, le=2_147_483_647)]
    apk_url: Annotated[str, Field(min_length=12, max_length=512)]
    apk_sha256: str
    apk_size_bytes: Annotated[int, Field(ge=1, le=512 * 1024 * 1024)]
    release_notes_ru: Annotated[str, Field(max_length=2000)]

    @field_validator("version_name")
    @classmethod
    def _version_name(cls, value: str) -> str:
        if not _VERSION_NAME.fullmatch(value):
            raise ValueError("version_name_invalid")
        return value

    @field_validator("apk_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://") or any(ch.isspace() for ch in value):
            raise ValueError("apk_url_invalid")
        return value

    @field_validator("apk_sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("apk_sha256_invalid")
        return value

    @model_validator(mode="after")
    def _minimum_not_above_latest(self) -> "AppUpdateManifest":
        if self.min_supported_version_code > self.version_code:
            raise ValueError("min_supported_above_latest")
        return self


class SignedAppUpdateManifest(BaseModel):
    """What the app receives: it verifies `signature` over the decoded `payload` bytes itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: Annotated[str, Field(min_length=4, max_length=MAX_MANIFEST_BYTES)]
    signature: Annotated[str, Field(min_length=86, max_length=88)]
    key_id: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]


def sign_manifest(manifest: AppUpdateManifest, private_key: Ed25519PrivateKey) -> dict[str, str]:
    """The envelope for `manifest`. Pure: the caller loads the key and never stores it here."""
    payload = json.dumps(
        manifest.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "payload": base64.b64encode(payload).decode("ascii"),
        "signature": base64.b64encode(private_key.sign(payload)).decode("ascii"),
        "key_id": key_id(public),
    }


def key_id(public_key: bytes) -> str:
    """Short, non-secret label of a public key (first 16 hex of its SHA-256)."""
    return hashlib.sha256(public_key).hexdigest()[:16]


def _b64(value: str) -> bytes:
    if not _BASE64.fullmatch(value) or len(value) % 4:
        raise ValueError("base64_invalid")
    return base64.b64decode(value, validate=True)


def verify_envelope(
    raw: object, public_key_b64: str
) -> tuple[SignedAppUpdateManifest, AppUpdateManifest] | None:
    """The envelope and its manifest when the signature verifies with this key, else None."""
    try:
        public_key = _b64(public_key_b64)
        if len(public_key) != 32:
            return None
        envelope = SignedAppUpdateManifest.model_validate(raw)
        if envelope.key_id != key_id(public_key):
            return None
        payload = _b64(envelope.payload)
        signature = _b64(envelope.signature)
        if len(signature) != 64:
            return None
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
        manifest = AppUpdateManifest.model_validate(json.loads(payload.decode("utf-8")))
    except (InvalidSignature, ValueError, UnicodeDecodeError, binascii.Error):
        # pydantic's ValidationError is a ValueError; details are not echoed to clients.
        return None
    return envelope, manifest


def load_signed(
    path: Path | None, public_key_b64: str = UPDATE_SIGNING_PUBLIC_KEY
) -> tuple[SignedAppUpdateManifest, AppUpdateManifest] | None:
    """Verified envelope, or None when unset, missing, oversized, unsigned or invalid."""
    if path is None or not public_key_b64:
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES * 2:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return verify_envelope(raw, public_key_b64)


def load_manifest(
    path: Path | None, public_key_b64: str = UPDATE_SIGNING_PUBLIC_KEY
) -> AppUpdateManifest | None:
    """The verified manifest only; an unsigned or wrongly signed file is unavailable."""
    signed = load_signed(path, public_key_b64)
    return None if signed is None else signed[1]
