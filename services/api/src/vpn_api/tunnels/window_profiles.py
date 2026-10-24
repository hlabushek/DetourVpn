"""Explicit operator inventory for v3. Never populated by an agent/user request."""

import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from vpn_api.auth.security import AuthError
from vpn_api.tunnels.admission import AWG_PARAMETERS, NodeProfile, canonical_key
from vpn_api.tunnels.v3_contracts import FrozenInput


class WindowSlot(FrozenInput):
    slot_id: int = Field(strict=True, ge=1, le=16)
    server_public_key: str = Field(repr=False)
    header_protection_key: SecretStr
    endpoint: str

    @field_validator("server_public_key")
    @classmethod
    def validate_public(cls, value: str) -> str:
        return canonical_key(value)

    @field_validator("header_protection_key")
    @classmethod
    def validate_header(cls, value: SecretStr) -> SecretStr:
        canonical_key(value.get_secret_value())
        return value

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return NodeProfile.check_endpoint(value)

    @model_validator(mode="after")
    def fixed_port(self) -> "WindowSlot":
        if self.endpoint.rsplit(":", 1)[1] != str(51900 + self.slot_id):
            raise ValueError("Fixed slot UDP port required")
        return self

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


class WindowNode(FrozenInput):
    node_id: UUID
    slots: list[WindowSlot] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def slot_order(self) -> "WindowNode":
        if [slot.slot_id for slot in self.slots] != list(range(1, len(self.slots) + 1)):
            raise ValueError("Contiguous fixed operator slots required")
        return self

    def digest(self) -> str:
        # SecretStr JSON serialization masks values; explicitly bind the real operator key.
        material = {
            "node_id": str(self.node_id),
            "slots": [
                {
                    **slot.model_dump(mode="json"),
                    "header_protection_key": slot.header_protection_key.get_secret_value(),
                }
                for slot in self.slots
            ],
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


class WindowManifest(FrozenInput):
    version: int = Field(strict=True, ge=3, le=3)
    nodes: list[WindowNode] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_inventory(self) -> "WindowManifest":
        for values in (
            [str(node.node_id) for node in self.nodes],
            [slot.endpoint for node in self.nodes for slot in node.slots],
            [slot.server_public_key for node in self.nodes for slot in node.slots],
        ):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate operator identity")
        return self


def load_manifest(path: Path | None) -> WindowManifest:
    if path is None:
        raise AuthError("window_tunnels_unavailable", 503)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 262144 or info.st_nlink != 1:
            raise ValueError("Invalid manifest file")
        if os.name != "nt":
            if info.st_uid != 0 or info.st_mode & 0o027:
                raise ValueError("Root-owned private operator manifest required")
            for parent in path.parents:
                entry = parent.lstat()
                if not stat.S_ISDIR(entry.st_mode) or entry.st_uid != 0 or entry.st_mode & 0o022:
                    raise ValueError("Untrusted manifest directory")
        return WindowManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        raise AuthError("window_manifest_unavailable", 503) from None
