from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, StrictInt

from vpn_api.auth.contracts import Input


class TunnelInput(Input):
    device_id: UUID
    node_id: UUID | None = None
    idempotency_key: UUID
    proof_id: UUID | None = None
    proof_response: SecretStr | None = Field(default=None, min_length=64, max_length=64)
    budget_bytes: StrictInt = Field(default=1048576, ge=65536, le=16777216)


class TunnelResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    device_id: UUID
    node_id: UUID | None = None
    state: Literal["admission_pending", "provision_pending", "active", "revoke_pending", "revoked"]
    peer_version: int
    created_at: datetime
    revoked_at: datetime | None
    lease_deadline: datetime | None = None
    budget_bytes: int | None = None
    # No config field or optional private key field: admission is closed.


class PeerPollInput(Input):
    contract_version: StrictInt
    capabilities: list[Literal["peer-removal.v1"]] = Field(min_length=1, max_length=1)
    limit: StrictInt = Field(default=100, ge=1, le=100)


class PeerRemoval(BaseModel):
    command_id: UUID
    peer_id: UUID
    peer_version: int
    operation: Literal["remove_peer"] = "remove_peer"
    public_key: str


class PeerPollResponse(BaseModel):
    contract_version: Literal[1] = 1
    commands: list[PeerRemoval]


class PeerAckInput(Input):
    contract_version: StrictInt
    capability: Literal["peer-removal.v1"]
    command_id: UUID
    peer_id: UUID
    peer_version: StrictInt = Field(ge=1, le=9223372036854775807)
    outcome: Literal["removed"]


class PeerAckResponse(BaseModel):
    contract_version: Literal[1] = 1
    command_id: UUID
    state: Literal["revoked"] = "revoked"
