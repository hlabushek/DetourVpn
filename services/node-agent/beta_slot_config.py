"""Validate persistent beta slot keys and render node/API config separately."""

from __future__ import annotations

import base64
import json
from typing import TypedDict
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from vpn_api.tunnels.awg_profile import AWG_PARAMETERS
from vpn_api.tunnels.keys import canonical_key
from vpn_api.tunnels.window_profiles import WindowManifest, WindowSlot


class ConfigError(ValueError):
    """Fixed validation errors only; never echo key material or input values."""


class SlotSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    slot_id: int = Field(strict=True, ge=1, le=16)
    endpoint: str
    server_private_key: SecretStr = Field(repr=False)
    header_protection_key: SecretStr = Field(repr=False)


class BetaSlotConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: int = Field(strict=True, ge=1, le=1)
    node_id: UUID
    api_origin: str
    slots: list[SlotSource] = Field(min_length=1, max_length=16)

    @field_validator("api_origin")
    @classmethod
    def validate_api_origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError("HTTPS beta API origin required")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_slots(self) -> BetaSlotConfiguration:
        if [slot.slot_id for slot in self.slots] != list(range(1, len(self.slots) + 1)):
            raise ValueError("Contiguous beta slots required")
        endpoints = [slot.endpoint for slot in self.slots]
        if len(set(endpoints)) != len(endpoints):
            raise ValueError("Unique slot endpoints required")
        return self


class RenderedConfigs(TypedDict):
    agent_config: bytes
    node_slots: bytes
    api_manifest: bytes
    awg_configs: dict[str, bytes]


def _private_public_key(value: str) -> tuple[str, str]:
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode("ascii") != value:
            raise ValueError
        public_raw = (
            X25519PrivateKey.from_private_bytes(raw)
            .public_key()
            .public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        )
        public = base64.b64encode(public_raw).decode("ascii")
        canonical_key(public)
    except (ValueError, TypeError):
        raise ConfigError("server_private_key_invalid") from None
    return value, public


def render_configs(raw: bytes) -> RenderedConfigs:
    """Validate source JSON and render node, AWG, and API outputs without writing."""
    if not raw or len(raw) > 65536:
        raise ConfigError("beta_slot_config_size_invalid")
    try:
        source = BetaSlotConfiguration.model_validate_json(raw)
    except ValueError:
        raise ConfigError("beta_slot_config_invalid") from None

    node_slots = []
    manifest_slots = []
    awg_configs: dict[str, bytes] = {}
    for source_slot in source.slots:
        private_key, public_key = _private_public_key(
            source_slot.server_private_key.get_secret_value()
        )
        header_key = source_slot.header_protection_key.get_secret_value()
        try:
            canonical_key(header_key)
            profile = WindowSlot.model_validate(
                {
                    "slot_id": source_slot.slot_id,
                    "endpoint": source_slot.endpoint,
                    "server_public_key": public_key,
                    "header_protection_key": header_key,
                }
            )
        except ValueError:
            raise ConfigError("beta_slot_profile_invalid") from None
        node_slots.append(
            {
                "slot_id": profile.slot_id,
                "endpoint": profile.endpoint,
                "server_private_key": private_key,
                "server_public_key": profile.server_public_key,
                "header_protection_key": profile.header_protection_key.get_secret_value(),
            }
        )
        manifest_slots.append(
            {
                "slot_id": profile.slot_id,
                "endpoint": profile.endpoint,
                "server_public_key": profile.server_public_key,
                "header_protection_key": profile.header_protection_key.get_secret_value(),
            }
        )
        awg_configs[f"slot-{profile.slot_id}.conf"] = (
            "[Interface]\n"
            f"PrivateKey = {private_key}\n"
            f"ListenPort = {51900 + profile.slot_id}\n"
            + AWG_PARAMETERS
            + "HeaderProtectionKey = "
            + profile.header_protection_key.get_secret_value()
            + "\n"
        ).encode("ascii")

    agent_config = {
        "node_id": str(source.node_id),
        "api_origin": source.api_origin,
        "slot_count": len(source.slots),
    }
    try:
        WindowManifest.model_validate(
            {"version": 3, "nodes": [{"node_id": str(source.node_id), "slots": manifest_slots}]}
        )
    except ValueError:
        raise ConfigError("beta_window_manifest_invalid") from None
    manifest_bytes = (
        json.dumps(
            {"version": 3, "nodes": [{"node_id": str(source.node_id), "slots": manifest_slots}]},
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    node_slots_bytes = (
        json.dumps({"version": 1, "slots": node_slots}, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    agent_bytes = json.dumps(agent_config, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if any(
        slot.server_private_key.get_secret_value().encode() in manifest_bytes
        for slot in source.slots
    ):
        raise ConfigError("private_key_manifest_leak")
    return {
        "agent_config": agent_bytes,
        "node_slots": node_slots_bytes,
        "api_manifest": manifest_bytes,
        "awg_configs": awg_configs,
    }
